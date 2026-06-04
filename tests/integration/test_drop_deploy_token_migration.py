"""Migration up/down/up validation for the deploy-token drop (S49.3 wave).

The identity model ``GhrmUserGithubAccess`` is trimmed to identity/OAuth only;
the per-user collaborator-state columns (``deploy_token``, ``token_expires_at``,
``access_status``, ``grace_expires_at``, ``last_synced_at``) move out — the
first three are gone for good, the lifecycle now lives in
``ghrm_repo_membership``. This migration drops those columns; downgrade
re-adds them so the graph is reversible.

The ``db`` fixture's create_all() builds the table from the trimmed model
(columns already absent), so the test first re-adds the legacy columns to
simulate the pre-migration schema, then exercises the migration in isolation.
"""
import importlib.util
import os

import pytest
import sqlalchemy as sa
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
        "20260604_1000_drop_deploy_token.py",
    )
    spec = importlib.util.spec_from_file_location("ghrm_drop_token_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()

IDENTITY_TABLE = "ghrm_user_github_access"
DROPPED_COLUMNS = (
    "deploy_token",
    "token_expires_at",
    "access_status",
    "grace_expires_at",
    "last_synced_at",
)


def _column_names(connection, table):
    return {column["name"] for column in inspect(connection).get_columns(table)}


@pytest.fixture
def migration_connection(db):
    connection = db.engine.connect()
    transaction = connection.begin()
    operations = Operations(MigrationContext.configure(connection))
    # The trimmed model created the table without the legacy columns; re-add
    # them so we exercise the drop against a realistic pre-migration schema.
    existing = _column_names(connection, IDENTITY_TABLE)
    for column_name in DROPPED_COLUMNS:
        if column_name not in existing:
            if column_name in (
                "token_expires_at",
                "grace_expires_at",
                "last_synced_at",
            ):
                operations.add_column(
                    IDENTITY_TABLE, sa.Column(column_name, sa.DateTime(), nullable=True)
                )
            elif column_name == "access_status":
                operations.add_column(
                    IDENTITY_TABLE,
                    sa.Column(
                        column_name,
                        sa.String(length=32),
                        nullable=False,
                        server_default="active",
                    ),
                )
            else:
                operations.add_column(
                    IDENTITY_TABLE, sa.Column(column_name, sa.Text(), nullable=True)
                )
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


class TestDropDeployTokenMigration:
    def test_upgrade_drops_columns(self, migration_connection):
        before = _column_names(migration_connection, IDENTITY_TABLE)
        for column_name in DROPPED_COLUMNS:
            assert column_name in before
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        after = _column_names(migration_connection, IDENTITY_TABLE)
        for column_name in DROPPED_COLUMNS:
            assert column_name not in after
        # Identity/OAuth columns survive.
        assert "github_username" in after
        assert "oauth_token" in after

    def test_downgrade_readds_columns(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
        after = _column_names(migration_connection, IDENTITY_TABLE)
        for column_name in DROPPED_COLUMNS:
            assert column_name in after

    def test_up_down_up_is_clean(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
        after = _column_names(migration_connection, IDENTITY_TABLE)
        for column_name in DROPPED_COLUMNS:
            assert column_name not in after

    def test_down_revision_chains_to_membership(self):
        assert migration.down_revision == "20260530_1000_ghrm_membership"
