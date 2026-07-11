"""Migration up/down/up + CRUD round-trip for the access-kind columns (S132).

ADDITIVE migration: adds ``access_kind`` (server_default ``'repo'``),
``github_org`` and ``github_team_slug`` to ``ghrm_software_package`` only. The
membership table is untouched (the ``repo_grants`` JSON column is reused). The
``db`` fixture's create_all() builds the tables from the updated models (columns
already present), so the migration test first re-creates the pre-migration
schema (drop the new columns) before exercising the migration in isolation.
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
        "20260711_1000_ghrm_access_kind.py",
    )
    spec = importlib.util.spec_from_file_location("ghrm_access_kind_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()

PKG_TABLE = "ghrm_software_package"
NEW_COLUMNS = ("access_kind", "github_org", "github_team_slug")


def _column_names(connection, table):
    return {column["name"] for column in inspect(connection).get_columns(table)}


def _make_plan(db, name: str) -> TarifPlan:
    plan = TarifPlan(
        name=name,
        slug=f"plan-{uuid4().hex[:8]}",
        price=10.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


@pytest.fixture
def migration_connection(app):
    """Recreate the pre-migration schema (drop the new columns) so the additive
    migration is exercised in isolation. Opens its OWN connection + transaction
    and rolls back at teardown (self-cleaning, no wipe).
    """
    from vbwd.extensions import db

    connection = db.engine.connect()
    transaction = connection.begin()
    operations = Operations(MigrationContext.configure(connection))
    for column in NEW_COLUMNS:
        if column in _column_names(connection, PKG_TABLE):
            operations.drop_column(PKG_TABLE, column)
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


class TestAccessKindMigration:
    def test_chains_off_current_ghrm_head(self):
        assert migration.revision == "20260711_1000_ghrm_access_kind"
        assert migration.down_revision == "20260709_1000_ghrm_plan_nullable"

    def test_upgrade_adds_columns(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        columns = _column_names(migration_connection, PKG_TABLE)
        for column in NEW_COLUMNS:
            assert column in columns

    def test_existing_package_backfills_to_repo(self, migration_connection):
        plan_id = uuid4()
        migration_connection.execute(
            text(
                "INSERT INTO subscription_tarif_plan (id, name, slug, price, "
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
                "sort_order, collaborator_permission, package_kind, bundle_repos, "
                "created_at, updated_at, version) VALUES "
                "(:id, :plan, 'Legacy', 'legacy-access-pkg', 'acme', 'legacy-repo', "
                "'release', 'legacy-key', 0, true, 0, 'pull', 'single', '[]', "
                "now(), now(), 1)"
            ),
            {"id": uuid4(), "plan": plan_id},
        )
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        row = migration_connection.execute(
            text(
                "SELECT access_kind, github_org, github_team_slug "
                "FROM ghrm_software_package WHERE slug = 'legacy-access-pkg'"
            )
        ).first()
        assert row[0] == "repo"
        assert row[1] is None
        assert row[2] is None

    def test_downgrade_drops_columns(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
        columns = _column_names(migration_connection, PKG_TABLE)
        for column in NEW_COLUMNS:
            assert column not in columns

    def test_up_down_up_is_clean(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
        columns = _column_names(migration_connection, PKG_TABLE)
        for column in NEW_COLUMNS:
            assert column in columns


class TestAccessKindCrudRoundTrip:
    def test_persists_team_access_kind(self, db):
        plan = _make_plan(db, "Team Plan")
        repository = GhrmSoftwarePackageRepository(db.session)
        package = GhrmSoftwarePackage(
            tariff_plan_id=plan.id,
            name="Team Pkg",
            slug=f"team-{uuid4().hex[:6]}",
            github_owner="acme",
            github_repo="showcase",
            access_kind="team",
            github_org="acme-inc",
            github_team_slug="developers",
        )
        repository.save(package)
        reloaded = repository.find_by_slug(package.slug)
        assert reloaded.access_kind == "team"
        assert reloaded.github_org == "acme-inc"
        assert reloaded.github_team_slug == "developers"
        assert reloaded.access_targets()[0].key() == (
            "team",
            "acme-inc",
            "developers",
        )

    def test_default_access_kind_is_repo(self, db):
        plan = _make_plan(db, "Repo Plan")
        repository = GhrmSoftwarePackageRepository(db.session)
        package = GhrmSoftwarePackage(
            tariff_plan_id=plan.id,
            name="Repo Pkg",
            slug=f"repo-{uuid4().hex[:6]}",
            github_owner="acme",
            github_repo=f"solo-{uuid4().hex[:6]}",
        )
        repository.save(package)
        reloaded = repository.find_by_slug(package.slug)
        assert reloaded.access_kind == "repo"
        assert reloaded.github_org is None
