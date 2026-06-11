"""Integration: ``ghrm_packages`` plan link is slug-portable (S63).

The package's link to its subscription ``TarifPlan`` must travel by the plan's
**slug**, not the instance-local ``tariff_plan_id`` UUID, so a package exported
from one instance re-attaches to the correct local plan after a cross-instance
import (plans upsert by slug and mint fresh UUIDs on the target).

* **Portability round-trip** — the exported row carries ``tariff_plan_slug`` and
  no ``tariff_plan_id``; importing into a session where the same-slug plan has a
  **different** UUID links the package to the **local** plan id.
* **Missing plan** — a row whose ``tariff_plan_slug`` resolves to no plan yields
  one error row; the import does not raise and other rows still apply.
* **Secrets** — ``sync_api_key`` / ``github_installation_id`` stay stripped.
* **Bundle** — ``package_kind="bundle"`` + ``bundle_repos`` round-trip verbatim.

Data is seeded through the ORM session (no raw SQL); the shared ``db`` fixture
creates + drops the test DB.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID/DI/
DRY; Liskov (missing referent → skip-with-error, never a crash); no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import uuid

from vbwd.services.data_exchange.envelope import build_envelope
from vbwd.services.data_exchange.port import ExportSelector
from vbwd.models.enums import BillingPeriod
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from plugins.ghrm.src.services.data_exchange.ghrm_exchangers import (
    build_ghrm_exchangers,
)
from plugins.subscription.subscription.models.tarif_plan import TarifPlan


def _exchanger(session):
    return build_ghrm_exchangers(session)[0]


def _seed_plan(db, slug):
    plan = TarifPlan(
        slug=slug,
        name="Plan",
        price_float=9.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add(plan)
    db.session.commit()
    return plan


def _seed_package(db, plan, **overrides):
    package = GhrmSoftwarePackage(
        tariff_plan_id=plan.id,
        name="My Package",
        slug=f"pkg-{uuid.uuid4().hex[:8]}",
        github_owner="acme",
        github_repo="widget",
        github_installation_id="install-123",
        collaborator_permission="push",
        **overrides,
    )
    db.session.add(package)
    db.session.commit()
    return package


class TestPlanLinkSlugPortable:
    def test_export_row_carries_plan_slug_not_uuid(self, db):
        plan = _seed_plan(db, slug=f"plan-{uuid.uuid4().hex[:8]}")
        package = _seed_package(db, plan)
        exchanger = _exchanger(db.session)

        rows = exchanger.export(
            ExportSelector(ids=[package.slug]), include_pii=False
        ).rows

        assert rows and rows[0]["slug"] == package.slug
        assert rows[0]["tariff_plan_slug"] == plan.slug
        assert "tariff_plan_id" not in rows[0]
        # Secrets stay stripped.
        assert "sync_api_key" not in rows[0]
        assert "github_installation_id" not in rows[0]

    def test_import_relinks_to_local_plan_with_different_uuid(self, db):
        # Source instance: plan + package.
        source_plan = _seed_plan(db, slug=f"plan-{uuid.uuid4().hex[:8]}")
        package = _seed_package(db, source_plan)
        package_slug = package.slug
        plan_slug = source_plan.slug
        exchanger = _exchanger(db.session)

        rows = exchanger.export(
            ExportSelector(ids=[package_slug]), include_pii=False
        ).rows

        # Simulate the target instance: drop the package and the source plan,
        # then recreate a same-slug plan with a DIFFERENT uuid (as a slug-based
        # plan import would).
        db.session.query(GhrmSoftwarePackage).filter(
            GhrmSoftwarePackage.slug == package_slug
        ).delete()
        db.session.query(TarifPlan).filter(TarifPlan.slug == plan_slug).delete()
        db.session.commit()
        local_plan = _seed_plan(db, slug=plan_slug)
        assert local_plan.id != source_plan.id

        payload = build_envelope("ghrm_packages", rows, instance="target")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)

        assert result.created == 1
        assert not result.errors
        rebuilt = (
            db.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.slug == package_slug)
            .first()
        )
        assert rebuilt is not None
        assert rebuilt.tariff_plan_id == local_plan.id
        assert rebuilt.github_owner == "acme"
        assert rebuilt.collaborator_permission == "push"

    def test_missing_plan_slug_yields_error_row_without_raising(self, db):
        present_plan = _seed_plan(db, slug=f"plan-{uuid.uuid4().hex[:8]}")
        package = _seed_package(db, present_plan)
        exchanger = _exchanger(db.session)

        good_row = exchanger.export(
            ExportSelector(ids=[package.slug]), include_pii=False
        ).rows[0]
        bad_row = dict(good_row)
        bad_row["slug"] = f"orphan-{uuid.uuid4().hex[:8]}"
        bad_row["tariff_plan_slug"] = f"nonexistent-{uuid.uuid4().hex[:8]}"

        # Drop the existing package so the good row counts as a create.
        db.session.query(GhrmSoftwarePackage).filter(
            GhrmSoftwarePackage.slug == package.slug
        ).delete()
        db.session.commit()

        payload = build_envelope("ghrm_packages", [bad_row, good_row], instance="t")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)

        assert len(result.errors) == 1
        assert result.errors[0]["row"] == 0
        # The valid row still applied.
        assert result.created == 1
        assert (
            db.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.slug == bad_row["slug"])
            .first()
            is None
        )
        assert (
            db.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.slug == package.slug)
            .first()
            is not None
        )

    def test_bundle_package_round_trips(self, db):
        plan = _seed_plan(db, slug=f"plan-{uuid.uuid4().hex[:8]}")
        bundle_repos = [
            {"owner": "acme", "repo": "alpha"},
            {"owner": "acme", "repo": "beta"},
        ]
        package = _seed_package(
            db,
            plan,
            package_kind="bundle",
            bundle_repos=bundle_repos,
        )
        package_slug = package.slug
        exchanger = _exchanger(db.session)

        rows = exchanger.export(
            ExportSelector(ids=[package_slug]), include_pii=False
        ).rows

        db.session.query(GhrmSoftwarePackage).filter(
            GhrmSoftwarePackage.slug == package_slug
        ).delete()
        db.session.commit()

        payload = build_envelope("ghrm_packages", rows, instance="target")
        exchanger.import_(payload, mode="upsert", dry_run=False)

        rebuilt = (
            db.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.slug == package_slug)
            .first()
        )
        assert rebuilt is not None
        assert rebuilt.package_kind == "bundle"
        assert rebuilt.bundle_repos == bundle_repos
