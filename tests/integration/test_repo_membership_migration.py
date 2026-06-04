"""Migration up/down/up validation for ghrm_repo_membership (S49.1).

ADDITIVE migration — creates the ``ghrm_repo_membership`` table only and
does NOT drop any column from ``ghrm_user_github_access``. Binds the
migration's upgrade/downgrade to a real connection through alembic's
Operations context and asserts the table appears, drops, and reappears
cleanly. The ``db`` fixture's create_all() already created the table, so
we drop it first to exercise the migration in isolation.
"""
import importlib.util
import os

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _load_migration():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "migrations",
        "versions",
        "20260530_1000_ghrm_repo_membership.py",
    )
    spec = importlib.util.spec_from_file_location(
        "ghrm_repo_membership_migration", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()

NEW_TABLE = "ghrm_repo_membership"
IDENTITY_TABLE = "ghrm_user_github_access"


def _table_names(connection):
    return set(inspect(connection).get_table_names())


def _column_names(connection, table):
    return {column["name"] for column in inspect(connection).get_columns(table)}


@pytest.fixture
def migration_connection(db):
    connection = db.engine.connect()
    transaction = connection.begin()
    operations = Operations(MigrationContext.configure(connection))
    if NEW_TABLE in _table_names(connection):
        operations.drop_table(NEW_TABLE)
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


class TestMigrationUpDownUp:
    def test_upgrade_creates_membership_table(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        assert NEW_TABLE in _table_names(migration_connection)

    def test_downgrade_drops_membership_table(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
        assert NEW_TABLE not in _table_names(migration_connection)

    def test_up_down_up_is_clean(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
        assert NEW_TABLE in _table_names(migration_connection)

    def test_membership_migration_does_not_touch_identity_table(
        self, migration_connection
    ):
        """The membership migration is purely additive — it creates the new
        table and leaves ``ghrm_user_github_access`` columns untouched. The
        deploy-token drop is a separate migration (S49.3 wave)."""
        before = _column_names(migration_connection, IDENTITY_TABLE)
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        after = _column_names(migration_connection, IDENTITY_TABLE)
        assert after == before
