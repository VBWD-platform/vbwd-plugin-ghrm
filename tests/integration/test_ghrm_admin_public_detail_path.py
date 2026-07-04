"""Integration test — admin package responses carry ``public_detail_path``.

The fe-admin GHRM package list + editor render a "view on frontend" link. The
backend serializes the fe-user public detail path on each admin package
serialization so the frontend does not reconstruct catalogue/category routing.

Covers the admin LIST and GET-single routes (the ones the fe-admin views load):
  * a plan attached to a configured (non-first) category → that category in the
    path (proves real resolution, not the fallback),
  * a plan in no configured category → the first configured category (fallback),
    so the link still renders and still resolves (detail fetch is by slug).

The catalogue page slug and configured category slugs are read from the running
plugin config (via the public ``/ghrm/config`` + ``/ghrm/categories`` endpoints)
so the assertions hold regardless of the environment's configured values.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID/DI/
DRY; Liskov (uncategorized plan → fallback, never a crash); no overengineering.
Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import os
import uuid

import jwt
import pytest

os.environ["GHRM_USE_MOCK_GITHUB"] = "true"

from vbwd.extensions import db as _db  # noqa: E402
from vbwd.models.user import User  # noqa: E402
from vbwd.models.enums import UserStatus, UserRole, BillingPeriod  # noqa: E402
from vbwd.models.role import Role, Permission  # noqa: E402
from plugins.ghrm.src.models.ghrm_software_package import (  # noqa: E402
    GhrmSoftwarePackage,
)
from plugins.subscription.subscription.models.tarif_plan import (  # noqa: E402
    TarifPlan,
)
from plugins.subscription.subscription.models.tarif_plan_category import (  # noqa: E402
    TarifPlanCategory,
)


def _bearer(user: User) -> dict:
    from vbwd.config import get_config

    token = jwt.encode(
        {"user_id": str(user.id), "email": user.email},
        get_config().SECRET_KEY,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _make_admin() -> User:
    admin = User(
        email=f"ghrm-{uuid.uuid4().hex[:10]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=UserRole.ADMIN,
    )
    _db.session.add(admin)
    _db.session.flush()
    role = Role(
        name=f"ghrm-test-{uuid.uuid4().hex[:8]}",
        slug=f"ghrm-test-{uuid.uuid4().hex[:8]}",
    )
    role.permissions.append(
        Permission(name="ghrm.packages.view", resource="ghrm.packages", action="view")
    )
    admin.assigned_roles.append(role)
    _db.session.add(role)
    _db.session.flush()
    return admin


def _make_plan() -> TarifPlan:
    plan = TarifPlan(
        name=f"Plan {uuid.uuid4().hex[:8]}",
        slug=f"plan-{uuid.uuid4().hex[:8]}",
        price=0.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    _db.session.add(plan)
    _db.session.flush()
    return plan


def _make_package(plan: TarifPlan, slug: str) -> GhrmSoftwarePackage:
    package = GhrmSoftwarePackage(
        tariff_plan_id=plan.id,
        name=f"Package {slug}",
        slug=slug,
        github_owner="acme",
        github_repo=slug,
        github_protected_branch="release",
    )
    _db.session.add(package)
    _db.session.flush()
    return package


def _catalogue_slug(client) -> str:
    return client.get("/api/v1/ghrm/config").get_json()["catalogue_page_slug"]


def _configured_category_slugs(client) -> list:
    body = client.get("/api/v1/ghrm/categories").get_json()
    return [category["slug"] for category in body["categories"]]


def _find_or_create_category(slug: str) -> TarifPlanCategory:
    existing = _db.session.query(TarifPlanCategory).filter_by(slug=slug).one_or_none()
    if existing:
        return existing
    category = TarifPlanCategory(name=slug.replace("-", " ").title(), slug=slug)
    _db.session.add(category)
    _db.session.flush()
    return category


class TestAdminPublicDetailPath:
    def test_get_single_uses_resolved_category_when_plan_categorized(self, db, client):
        admin = _make_admin()
        catalogue_slug = _catalogue_slug(client)
        category_slugs = _configured_category_slugs(client)
        assert category_slugs, "expected the running GHRM config to list categories"
        # Use a NON-first configured category so the assertion proves resolution
        # rather than the first-category fallback.
        target_category_slug = category_slugs[-1]

        plan = _make_plan()
        category = _find_or_create_category(target_category_slug)
        category.tarif_plans.append(plan)
        _db.session.add(category)
        package = _make_package(plan, f"cat-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.get(
            f"/api/v1/admin/ghrm/packages/{package.id}", headers=_bearer(admin)
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["public_detail_path"] == (
            f"/{catalogue_slug}/{target_category_slug}/{package.slug}"
        )

    def test_get_single_falls_back_to_first_category_for_uncategorized_plan(
        self, db, client
    ):
        admin = _make_admin()
        catalogue_slug = _catalogue_slug(client)
        first_category_slug = _configured_category_slugs(client)[0]

        plan = _make_plan()
        package = _make_package(plan, f"orphan-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.get(
            f"/api/v1/admin/ghrm/packages/{package.id}", headers=_bearer(admin)
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["public_detail_path"] == (
            f"/{catalogue_slug}/{first_category_slug}/{package.slug}"
        )

    def test_list_enriches_each_item(self, db, client):
        admin = _make_admin()
        catalogue_slug = _catalogue_slug(client)
        first_category_slug = _configured_category_slugs(client)[0]

        plan = _make_plan()
        package = _make_package(plan, f"listed-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.get("/api/v1/admin/ghrm/packages", headers=_bearer(admin))

        assert response.status_code == 200, response.get_data(as_text=True)
        items = response.get_json()["items"]
        match = next(item for item in items if item["id"] == str(package.id))
        assert match["public_detail_path"] == (
            f"/{catalogue_slug}/{first_category_slug}/{package.slug}"
        )


@pytest.fixture(autouse=True)
def _rollback(db):
    """Each test commits its own fixtures; clean up afterwards."""
    yield
    db.session.rollback()
