"""Integration test — GET /api/v1/admin/ghrm/packages/<id> (admin GET-single).

The fe-admin GHRM packages catalogue needs a GET-single admin endpoint to seed
its editor form (there was previously only a list, create, update, delete). The
single endpoint returns the SAME serialization the list uses — including the
``sync_api_key`` — and 404s when the package does not exist.

Runs in-process against the Flask test client with the real ``db`` fixture (no
external backend). Mirrors ``test_routes_permissions_install.py``: the GHRM
access service builds a GitHub client per request, so we force the mock
(``GHRM_USE_MOCK_GITHUB=true``) — no real credentials are needed.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID/DI/
DRY; Liskov (missing referent → 404, never a crash); no overengineering. Quality
guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
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


def _bearer(user: User) -> dict:
    from vbwd.config import get_config

    token = jwt.encode(
        {"user_id": str(user.id), "email": user.email},
        get_config().SECRET_KEY,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _make_user(role: UserRole = UserRole.USER) -> User:
    user = User(
        email=f"ghrm-{uuid.uuid4().hex[:10]}@example.com",
        password_hash="x",
        status=UserStatus.ACTIVE,
        role=role,
    )
    _db.session.add(user)
    _db.session.flush()
    return user


def _admin_with_permissions(*permission_names: str) -> User:
    admin = _make_user(role=UserRole.ADMIN)
    role = Role(
        name=f"ghrm-test-{uuid.uuid4().hex[:8]}",
        slug=f"ghrm-test-{uuid.uuid4().hex[:8]}",
    )
    for name in permission_names:
        resource, _, action = name.rpartition(".")
        role.permissions.append(
            Permission(name=name, resource=resource or name, action=action or "view")
        )
    admin.assigned_roles.append(role)
    _db.session.add(role)
    _db.session.flush()
    return admin


def _make_package(slug: str) -> GhrmSoftwarePackage:
    plan = TarifPlan(
        name=f"Plan {uuid.uuid4().hex[:8]}",
        slug=f"plan-{uuid.uuid4().hex[:8]}",
        price=0.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    _db.session.add(plan)
    _db.session.flush()
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


class TestAdminGetSinglePackage:
    def test_returns_package_admin_dict_including_sync_api_key(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.view")
        package = _make_package(f"single-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.get(
            f"/api/v1/admin/ghrm/packages/{package.id}", headers=_bearer(admin)
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["id"] == str(package.id)
        assert body["slug"] == package.slug
        assert body["github_owner"] == "acme"
        # The admin serialization exposes the sync key (same as the list).
        assert "sync_api_key" in body
        assert body["sync_api_key"] == package.sync_api_key

    def test_returns_404_for_unknown_package(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.view")
        _db.session.commit()

        response = client.get(
            f"/api/v1/admin/ghrm/packages/{uuid.uuid4()}", headers=_bearer(admin)
        )

        assert response.status_code == 404
        assert "error" in response.get_json()

    def test_requires_packages_view_permission(self, db, client):
        # An admin lacking ghrm.packages.view is forbidden.
        admin = _admin_with_permissions("ghrm.access.view")
        package = _make_package(f"gated-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.get(
            f"/api/v1/admin/ghrm/packages/{package.id}", headers=_bearer(admin)
        )

        assert response.status_code == 403


@pytest.fixture(autouse=True)
def _rollback(db):
    """Each test commits its own fixtures; clean up afterwards."""
    yield
    db.session.rollback()
