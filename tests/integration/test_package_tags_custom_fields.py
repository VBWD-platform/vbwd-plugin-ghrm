"""S77 — GHRM registers ghrm_software_package and appends tags + custom fields.

The public package detail (``GET /api/v1/ghrm/packages/<slug>``) appends
``tags`` / ``custom_fields`` (+ ``custom_field_defs``) via the core helper — no
model import, no extra round trip. The package edit page + card read those keys.
"""
import os
import uuid

# GHRM integration tests run against the mock GitHub client (no real creds).
os.environ["GHRM_USE_MOCK_GITHUB"] = "true"

from vbwd.models.enums import BillingPeriod  # noqa: E402
from plugins.subscription.subscription.models.tarif_plan import (  # noqa: E402
    TarifPlan,
)
from plugins.ghrm.src.models.ghrm_software_package import (  # noqa: E402
    GhrmSoftwarePackage,
)
from vbwd.services.entity_type_registry import (  # noqa: E402
    get_entity_type,
    is_registered,
)


def test_ghrm_package_entity_type_registered(app):
    assert is_registered("ghrm_software_package")
    registration = get_entity_type("ghrm_software_package")
    assert registration is not None
    assert registration.manage_permission == "ghrm.packages.manage"


def _seed_package(db):
    plan = TarifPlan(
        slug=f"plan-{uuid.uuid4().hex[:8]}",
        name="Plan",
        price=9.0,
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


def test_package_detail_appends_empty_tags_and_custom_fields(db, client):
    package = _seed_package(db)

    body = client.get(f"/api/v1/ghrm/packages/{package.slug}").get_json()

    assert body["tags"] == []
    assert body["custom_fields"] == {}
    assert "custom_field_defs" in body


def test_package_detail_appends_attached_tags(app, db, client):
    package = _seed_package(db)

    with app.app_context():
        app.container.tags_and_custom_fields().set_tags(
            "ghrm_software_package", package.id, ["cli"]
        )

    body = client.get(f"/api/v1/ghrm/packages/{package.slug}").get_json()

    assert body["tags"] == ["cli"]
