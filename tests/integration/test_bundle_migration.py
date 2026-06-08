"""Migration up/down/up + backfill + CRUD round-trip for bundles (S59).

ADDITIVE migration: adds ``package_kind`` + ``bundle_repos`` to
``ghrm_software_package`` and ``repo_grants`` to ``ghrm_repo_membership`` (all
with server_defaults), drops the ``uq_ghrm_pkg_owner_repo`` unique constraint
(D4), and backfills each membership's ``repo_grants`` from its package's
representative repo. Downgrade re-adds the constraint and drops the columns.

The ``db`` fixture's create_all() builds the tables from the updated models
(columns already present, constraint already dropped), so the migration test
first re-creates the pre-migration schema (drop the new columns, add the old
constraint) before exercising the migration in isolation.
"""
import importlib.util
import json
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
        "20260607_1000_ghrm_bundle.py",
    )
    spec = importlib.util.spec_from_file_location("ghrm_bundle_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()

PKG_TABLE = "ghrm_software_package"
MEMBERSHIP_TABLE = "ghrm_repo_membership"
NEW_PKG_COLUMNS = ("package_kind", "bundle_repos")
NEW_MEMBERSHIP_COLUMN = "repo_grants"
UNIQUE_CONSTRAINT = "uq_ghrm_pkg_owner_repo"


def _column_names(connection, table):
    return {column["name"] for column in inspect(connection).get_columns(table)}


def _unique_constraint_names(connection, table):
    return {uc["name"] for uc in inspect(connection).get_unique_constraints(table)}


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
    """Recreate the pre-migration schema so the additive migration is exercised
    in isolation: drop the new columns and (re-)add the dropped unique constraint.
    """
    connection = db.engine.connect()
    transaction = connection.begin()
    operations = Operations(MigrationContext.configure(connection))
    for column in NEW_PKG_COLUMNS:
        if column in _column_names(connection, PKG_TABLE):
            operations.drop_column(PKG_TABLE, column)
    if NEW_MEMBERSHIP_COLUMN in _column_names(connection, MEMBERSHIP_TABLE):
        operations.drop_column(MEMBERSHIP_TABLE, NEW_MEMBERSHIP_COLUMN)
    if UNIQUE_CONSTRAINT not in _unique_constraint_names(connection, PKG_TABLE):
        operations.create_unique_constraint(
            UNIQUE_CONSTRAINT, PKG_TABLE, ["github_owner", "github_repo"]
        )
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


class TestBundleMigration:
    def test_chains_off_current_head(self):
        assert migration.revision == "20260607_1000_ghrm_bundle"
        assert migration.down_revision == "20260605_1000_ghrm_pkg_perm"

    def test_upgrade_adds_columns_and_drops_constraint(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        pkg_columns = _column_names(migration_connection, PKG_TABLE)
        assert "package_kind" in pkg_columns
        assert "bundle_repos" in pkg_columns
        assert NEW_MEMBERSHIP_COLUMN in _column_names(
            migration_connection, MEMBERSHIP_TABLE
        )
        assert UNIQUE_CONSTRAINT not in _unique_constraint_names(
            migration_connection, PKG_TABLE
        )

    def test_existing_package_backfills_to_single(self, migration_connection):
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
                "sort_order, collaborator_permission, created_at, updated_at, "
                "version) VALUES "
                "(:id, :plan, 'Legacy', 'legacy-pkg', 'acme', 'legacy-repo', "
                "'release', 'legacy-key', 0, true, 0, 'pull', now(), now(), 1)"
            ),
            {"id": uuid4(), "plan": plan_id},
        )
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        row = migration_connection.execute(
            text(
                "SELECT package_kind, bundle_repos FROM ghrm_software_package "
                "WHERE slug = 'legacy-pkg'"
            )
        ).first()
        assert row[0] == "single"
        bundle_repos = row[1] if isinstance(row[1], list) else json.loads(row[1])
        assert bundle_repos == []

    def test_existing_membership_repo_grants_backfilled_from_package(
        self, migration_connection
    ):
        plan_id = uuid4()
        pkg_id = uuid4()
        user_id = uuid4()
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
                "sort_order, collaborator_permission, created_at, updated_at, "
                "version) VALUES "
                "(:id, :plan, 'Legacy', 'legacy-pkg2', 'acme', 'rep-repo', "
                "'release', 'legacy-key2', 0, true, 0, 'pull', now(), now(), 1)"
            ),
            {"id": pkg_id, "plan": plan_id},
        )
        migration_connection.execute(
            text(
                "INSERT INTO vbwd_user (id, email, password_hash, is_active, "
                "created_at, updated_at, version) VALUES "
                "(:id, :email, 'x', true, now(), now(), 1)"
            ),
            {"id": user_id, "email": f"u-{uuid4().hex[:8]}@example.com"},
        )
        migration_connection.execute(
            text(
                "INSERT INTO ghrm_repo_membership "
                "(id, user_id, package_id, status, invitation_id, created_at, "
                "updated_at, version) VALUES "
                "(:id, :uid, :pid, 'active', 'inv-7', now(), now(), 1)"
            ),
            {"id": uuid4(), "uid": user_id, "pid": pkg_id},
        )
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
        raw = migration_connection.execute(
            text(
                "SELECT repo_grants FROM ghrm_repo_membership WHERE package_id = :pid"
            ),
            {"pid": pkg_id},
        ).scalar()
        repo_grants = raw if isinstance(raw, list) else json.loads(raw)
        assert repo_grants == [
            {
                "owner": "acme",
                "repo": "rep-repo",
                "status": "active",
                "invitation_id": "inv-7",
            }
        ]

    def test_downgrade_restores_constraint_and_drops_columns(
        self, migration_connection
    ):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
        pkg_columns = _column_names(migration_connection, PKG_TABLE)
        assert "package_kind" not in pkg_columns
        assert "bundle_repos" not in pkg_columns
        assert NEW_MEMBERSHIP_COLUMN not in _column_names(
            migration_connection, MEMBERSHIP_TABLE
        )
        assert UNIQUE_CONSTRAINT in _unique_constraint_names(
            migration_connection, PKG_TABLE
        )

    def test_up_down_up_is_clean(self, migration_connection):
        context = MigrationContext.configure(migration_connection)
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
        pkg_columns = _column_names(migration_connection, PKG_TABLE)
        assert "package_kind" in pkg_columns
        assert "bundle_repos" in pkg_columns
        assert UNIQUE_CONSTRAINT not in _unique_constraint_names(
            migration_connection, PKG_TABLE
        )


class TestBundleCrudRoundTrip:
    def test_persists_bundle_kind_and_repos(self, db):
        plan = _make_plan(db, "Bundle Plan")
        repo = GhrmSoftwarePackageRepository(db.session)
        bundle_repos = [
            {"owner": "acme", "repo": "alpha"},
            {"owner": "acme", "repo": "beta"},
        ]
        package = GhrmSoftwarePackage(
            tariff_plan_id=plan.id,
            name="Bundle",
            slug=f"bundle-{uuid4().hex[:6]}",
            github_owner="acme",
            github_repo="showcase",
            package_kind="bundle",
            bundle_repos=bundle_repos,
        )
        repo.save(package)
        reloaded = repo.find_by_slug(package.slug)
        assert reloaded.package_kind == "bundle"
        assert reloaded.bundle_repos == bundle_repos
        assert reloaded.repo_targets() == [("acme", "alpha"), ("acme", "beta")]

    def test_single_defaults_persist(self, db):
        plan = _make_plan(db, "Single Plan")
        repo = GhrmSoftwarePackageRepository(db.session)
        package = GhrmSoftwarePackage(
            tariff_plan_id=plan.id,
            name="Single",
            slug=f"single-{uuid4().hex[:6]}",
            github_owner="acme",
            github_repo=f"solo-{uuid4().hex[:6]}",
        )
        repo.save(package)
        reloaded = repo.find_by_slug(package.slug)
        assert reloaded.package_kind == "single"
        assert reloaded.bundle_repos == []

    def test_two_packages_may_share_a_repo(self, db):
        """D4: UNIQUE(owner, repo) is dropped — overlap is allowed."""
        plan_a = _make_plan(db, "Plan A")
        plan_b = _make_plan(db, "Plan B")
        repo = GhrmSoftwarePackageRepository(db.session)
        shared_repo = f"shared-{uuid4().hex[:6]}"
        repo.save(
            GhrmSoftwarePackage(
                tariff_plan_id=plan_a.id,
                name="A",
                slug=f"a-{uuid4().hex[:6]}",
                github_owner="acme",
                github_repo=shared_repo,
            )
        )
        repo.save(
            GhrmSoftwarePackage(
                tariff_plan_id=plan_b.id,
                name="B",
                slug=f"b-{uuid4().hex[:6]}",
                github_owner="acme",
                github_repo=shared_repo,
            )
        )
        # No IntegrityError -> the unique constraint is gone.
        db.session.flush()

    def test_tariff_plan_id_uniqueness_still_enforced(self, db):
        from sqlalchemy.exc import IntegrityError

        plan = _make_plan(db, "Dup Plan")
        repo = GhrmSoftwarePackageRepository(db.session)
        repo.save(
            GhrmSoftwarePackage(
                tariff_plan_id=plan.id,
                name="First",
                slug=f"first-{uuid4().hex[:6]}",
                github_owner="acme",
                github_repo=f"r1-{uuid4().hex[:6]}",
            )
        )
        with pytest.raises(IntegrityError):
            repo.save(
                GhrmSoftwarePackage(
                    tariff_plan_id=plan.id,
                    name="Second",
                    slug=f"second-{uuid4().hex[:6]}",
                    github_owner="acme",
                    github_repo=f"r2-{uuid4().hex[:6]}",
                )
            )
            db.session.flush()
