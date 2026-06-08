"""Integration: GHRM entity exchangers (real PG) — S46.6.

* ``ghrm_packages`` round-trips by ``slug`` (export → wipe → import → equal).
* export strips the ``sync_api_key`` + ``github_installation_id`` secrets; on
  re-import the model's ``sync_api_key`` default regenerates a fresh secret.
* registration: after ``GhrmPlugin._register_data_exchangers`` the exchanger
  appears in ``data_exchange_registry`` with cluster ``sales``.

Data is seeded through the ORM session (no raw SQL); the shared ``db`` fixture
creates + drops the test DB. The package's NOT NULL FK ``tariff_plan_id`` points
at a ``subscription_tarif_plan`` row, so a plan is seeded first; the plan row is
left intact across the package wipe so the FK target survives re-import.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID/DI/
DRY; Liskov; no overengineering. Quality guard: ``bin/pre-commit-check.sh
--plugin ghrm --full``.
"""
import uuid

from vbwd.services.data_exchange.envelope import build_envelope
from vbwd.services.data_exchange.port import CLUSTER_SALES, ExportSelector
from vbwd.models.enums import BillingPeriod
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from plugins.ghrm.src.services.data_exchange.ghrm_exchangers import (
    build_ghrm_exchangers,
)
from plugins.subscription.subscription.models.tarif_plan import TarifPlan


def _exchanger(session):
    return build_ghrm_exchangers(session)[0]


def _seed_package(db):
    plan = TarifPlan(
        slug=f"plan-{uuid.uuid4().hex[:8]}",
        name="Plan",
        price_float=9.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add(plan)
    db.session.commit()
    package = GhrmSoftwarePackage(
        tariff_plan_id=plan.id,
        name="My Package",
        slug=f"pkg-{uuid.uuid4().hex[:8]}",
        github_owner="acme",
        github_repo="widget",
        github_installation_id="install-123",
    )
    db.session.add(package)
    db.session.commit()
    return package


class TestPackagesRoundTrip:
    def test_round_trip_by_slug_strips_secrets(self, db):
        package = _seed_package(db)
        slug = package.slug
        original_secret = package.sync_api_key
        exchanger = _exchanger(db.session)

        before = exchanger.export(ExportSelector(ids=[slug]), include_pii=False).rows
        assert before and before[0]["slug"] == slug
        assert "sync_api_key" not in before[0]
        assert "github_installation_id" not in before[0]
        assert before[0]["github_owner"] == "acme"

        db.session.query(GhrmSoftwarePackage).filter(
            GhrmSoftwarePackage.slug == slug
        ).delete()
        db.session.commit()

        payload = build_envelope("ghrm_packages", before, instance="test")
        result = exchanger.import_(payload, mode="upsert", dry_run=False)
        assert result.created == 1

        rebuilt = (
            db.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.slug == slug)
            .first()
        )
        assert rebuilt is not None
        assert rebuilt.name == "My Package"
        # The secret was not transported; the model default minted a fresh one.
        assert rebuilt.sync_api_key
        assert rebuilt.sync_api_key != original_secret
        # The installation id (secret) was not transported either.
        assert rebuilt.github_installation_id is None


class TestRegistration:
    def test_on_enable_registers_ghrm_exchanger(self, db):
        from vbwd.services.data_exchange.registry import data_exchange_registry
        from plugins.ghrm import GhrmPlugin

        plugin = GhrmPlugin()
        plugin.initialize({})
        plugin._register_data_exchangers()

        exchanger = data_exchange_registry.get("ghrm_packages")
        assert exchanger is not None
        assert exchanger.cluster == CLUSTER_SALES
        assert "csv" in exchanger.supported_formats
