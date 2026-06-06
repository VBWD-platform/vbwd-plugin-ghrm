"""Migration up/down/up + CRUD round-trip for collaborator_permission (S51).

ADDITIVE migration: adds ``collaborator_permission`` to ``ghrm_software_package``
with ``server_default='pull'`` so existing rows backfill to read; downgrade
drops it.

The ``db`` fixture's create_all() builds the table from the updated model (the
column already present), so the migration test first drops the column to
simulate the realistic pre-migration schema, then exercises the migration in
isolation.
"""
import importlib.util
import os
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from plugins.ghrm.src.repositories.software_package_repository import (
    GhrmSoftwarePackageRepository,
)
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from vbwd.models.enums import BillingPeriod


def _load_migration():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "migrations",
        "versions",
        "20260605_1000_pkg_collab_perm.py",
    )
    spec = importlib.util.spec_from_file_location(
        "ghrm_pkg_collab_perm_migration", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()

TABLE_NAME = "ghrm_software_package"
COLUMN_NAME = "collaborator_permission"


def _column_names(connection, table):
    return {column["name"] for column in inspect(connection).get_columns(table)}


def _make_plan(db, name: str) -> TarifPlan:
    plan = TarifPlan(
        name=name,
        slug=f"plan-{uuid4().hex[:8]}",
        price_float=10.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


@pytest.fixture
def migration_connection(db):
    connection = db.engine.connect()
    transaction = connection.begin()
    operations = Operations(MigrationContext.configure(connection))
    # The updated model created the table WITH the column; drop it so we
    # exercise the additive migration against a realistic pre-migration schema.
    if COLUMN_NAME in _column_names(connection, TABLE_NAME):
        operations.drop_column(TABLE_NAME, COLUMN_NAME)
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


class TestCollaboratorPermissionMigration:
    def test_upgrade_adds_column(self, migration_connection):
        assert COLUMN_NAME not in _column_names(migration_connection, TABLE_NAME)
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        assert COLUMN_NAME in _column_names(migration_connection, TABLE_NAME)

    def test_existing_rows_backfill_to_pull(self, migration_connection):
        # A plan + a column-less package row simulate the pre-migration state.
        plan_id = uuid4()
        migration_connection.execute(
            text(
                "INSERT INTO subscription_tarif_plan (id, name, slug, price_float, "
                "billing_period, trial_days, is_active, created_at, updated_at, "
                "version) VALUES "
                "(:id, 'Legacy', :slug, 0, 'MONTHLY', 0, true, now(), now(), 1)"
            ),
            {"id": plan_id, "slug": f"plan-{uuid4().hex[:8]}"},
        )
        migration_connection.execute(
            text(
                "INSERT INTO ghrm_software_package "
                "(id, tariff_plan_id, name, slug, github_owner, github_repo, "
                "github_protected_branch, sync_api_key, download_counter, is_active, "
                "sort_order, created_at, updated_at, version) VALUES "
                "(:id, :plan, 'Legacy', 'legacy-pkg', 'acme', 'legacy-repo', "
                "'release', 'legacy-key', 0, true, 0, now(), now(), 1)"
            ),
            {"id": uuid4(), "plan": plan_id},
        )
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        backfilled = migration_connection.execute(
            text(
                "SELECT collaborator_permission FROM ghrm_software_package "
                "WHERE slug = 'legacy-pkg'"
            )
        ).scalar()
        assert backfilled == "pull"

    def test_downgrade_drops_column(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
        assert COLUMN_NAME not in _column_names(migration_connection, TABLE_NAME)

    def test_up_down_up_is_clean(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
        assert COLUMN_NAME in _column_names(migration_connection, TABLE_NAME)

    def test_down_revision_chains_to_drop_token(self):
        assert migration.down_revision == "20260604_1000_ghrm_drop_token"


class TestCollaboratorPermissionCrudRoundTrip:
    def test_persists_chosen_level(self, db):
        plan = _make_plan(db, "Pro")
        repo = GhrmSoftwarePackageRepository(db.session)
        package = GhrmSoftwarePackage(
            tariff_plan_id=plan.id,
            name="Pro",
            slug=f"pro-{uuid4().hex[:6]}",
            github_owner="acme",
            github_repo=f"pro-{uuid4().hex[:6]}",
            collaborator_permission="push",
        )
        repo.save(package)

        reloaded = repo.find_by_slug(package.slug)
        assert reloaded.collaborator_permission == "push"

    def test_default_persists_as_pull(self, db):
        plan = _make_plan(db, "Basic")
        repo = GhrmSoftwarePackageRepository(db.session)
        package = GhrmSoftwarePackage(
            tariff_plan_id=plan.id,
            name="Basic",
            slug=f"basic-{uuid4().hex[:6]}",
            github_owner="acme",
            github_repo=f"basic-{uuid4().hex[:6]}",
        )
        repo.save(package)

        reloaded = repo.find_by_slug(package.slug)
        assert reloaded.collaborator_permission == "pull"
