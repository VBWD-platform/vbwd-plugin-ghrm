"""Integration tests — admin create/update accept the S132 access-kind fields.

S132 Inc 5 (backend half): the ADMIN package create/update routes must accept
``access_kind`` (``repo`` | ``team``, default ``repo``) plus ``github_org`` and
``github_team_slug``, persisting them so GET/``to_dict`` echoes them back. When
the effective ``access_kind`` is ``team`` both ``github_org`` and
``github_team_slug`` are REQUIRED (non-blank); a violation is a 400 with an
``{"error": ...}`` body (same shape as the existing ``GhrmValidationError``
handling). ``access_kind`` is an operator-level decision so it is wired into the
admin routes ONLY — never the vendor routes.

Runs in-process against the Flask test client with the real ``db`` fixture (no
external backend), mirroring ``test_ghrm_admin_get_single_package.py``. The GHRM
access service builds a GitHub client per request, so we force the mock
(``GHRM_USE_MOCK_GITHUB=true``) — no real credentials are needed.

Engineering requirements (binding, restated): TDD-first (these reds land before
the route change); DevOps-first (local + CI from cold start); SOLID/DI/DRY
(``access_kind`` validated through the single ``validate_access_kind`` home,
mirroring ``validate_package_kind``); Liskov (team-requires-org+slug enforced on
EFFECTIVE values so a partial update never persists an inconsistent state); no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import os
import uuid

import jwt

os.environ["GHRM_USE_MOCK_GITHUB"] = "true"

from vbwd.extensions import db as _db  # noqa: E402
from vbwd.models.user import User  # noqa: E402
from vbwd.models.enums import UserStatus, UserRole, BillingPeriod  # noqa: E402
from vbwd.models.role import Role, Permission  # noqa: E402
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


def _create_body(plan_id, slug, **overrides) -> dict:
    body = {
        "tariff_plan_id": str(plan_id),
        "name": f"Package {slug}",
        "slug": slug,
        "github_owner": "acme",
        "github_repo": slug,
    }
    body.update(overrides)
    return body


class TestAdminCreateAccessKind:
    def test_create_team_with_org_and_slug_persists(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage", "ghrm.packages.view")
        plan = _make_plan()
        _db.session.commit()
        slug = f"team-{uuid.uuid4().hex[:8]}"

        response = client.post(
            "/api/v1/admin/ghrm/packages",
            headers=_bearer(admin),
            json=_create_body(
                plan.id,
                slug,
                access_kind="team",
                github_org="acme-inc",
                github_team_slug="core-team",
            ),
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body["access_kind"] == "team"
        assert body["github_org"] == "acme-inc"
        assert body["github_team_slug"] == "core-team"

        # Persisted: a fresh GET echoes the same values.
        get_response = client.get(
            f"/api/v1/admin/ghrm/packages/{body['id']}", headers=_bearer(admin)
        )
        assert get_response.status_code == 200, get_response.get_data(as_text=True)
        fetched = get_response.get_json()
        assert fetched["access_kind"] == "team"
        assert fetched["github_org"] == "acme-inc"
        assert fetched["github_team_slug"] == "core-team"

    def test_create_team_missing_org_is_400(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        plan = _make_plan()
        _db.session.commit()

        response = client.post(
            "/api/v1/admin/ghrm/packages",
            headers=_bearer(admin),
            json=_create_body(
                plan.id,
                f"team-{uuid.uuid4().hex[:8]}",
                access_kind="team",
                github_team_slug="core-team",
            ),
        )

        assert response.status_code == 400, response.get_data(as_text=True)
        assert "error" in response.get_json()

    def test_create_team_missing_slug_is_400(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        plan = _make_plan()
        _db.session.commit()

        response = client.post(
            "/api/v1/admin/ghrm/packages",
            headers=_bearer(admin),
            json=_create_body(
                plan.id,
                f"team-{uuid.uuid4().hex[:8]}",
                access_kind="team",
                github_org="acme-inc",
            ),
        )

        assert response.status_code == 400, response.get_data(as_text=True)
        assert "error" in response.get_json()

    def test_create_defaults_to_repo(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        plan = _make_plan()
        _db.session.commit()

        response = client.post(
            "/api/v1/admin/ghrm/packages",
            headers=_bearer(admin),
            json=_create_body(plan.id, f"repo-{uuid.uuid4().hex[:8]}"),
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body["access_kind"] == "repo"
        assert body["github_org"] is None
        assert body["github_team_slug"] is None

    def test_create_invalid_access_kind_is_400(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        plan = _make_plan()
        _db.session.commit()

        response = client.post(
            "/api/v1/admin/ghrm/packages",
            headers=_bearer(admin),
            json=_create_body(
                plan.id,
                f"bogus-{uuid.uuid4().hex[:8]}",
                access_kind="bogus",
            ),
        )

        assert response.status_code == 400, response.get_data(as_text=True)
        assert "error" in response.get_json()


class TestAdminUpdateAccessKind:
    def _create_repo_package(self, admin, client):
        plan = _make_plan()
        _db.session.commit()
        slug = f"repo-{uuid.uuid4().hex[:8]}"
        response = client.post(
            "/api/v1/admin/ghrm/packages",
            headers=_bearer(admin),
            json=_create_body(plan.id, slug),
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        return response.get_json()

    def test_update_repo_to_team_with_org_and_slug(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        pkg = self._create_repo_package(admin, client)

        response = client.put(
            f"/api/v1/admin/ghrm/packages/{pkg['id']}",
            headers=_bearer(admin),
            json={
                "access_kind": "team",
                "github_org": "acme-inc",
                "github_team_slug": "core-team",
            },
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["access_kind"] == "team"
        assert body["github_org"] == "acme-inc"
        assert body["github_team_slug"] == "core-team"

    def test_update_to_team_omitting_org_is_400(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        pkg = self._create_repo_package(admin, client)

        # Package has no github_org yet; switching to team without supplying one
        # leaves the effective org blank -> 400.
        response = client.put(
            f"/api/v1/admin/ghrm/packages/{pkg['id']}",
            headers=_bearer(admin),
            json={"access_kind": "team", "github_team_slug": "core-team"},
        )

        assert response.status_code == 400, response.get_data(as_text=True)
        assert "error" in response.get_json()

    def test_update_invalid_access_kind_is_400(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        pkg = self._create_repo_package(admin, client)

        response = client.put(
            f"/api/v1/admin/ghrm/packages/{pkg['id']}",
            headers=_bearer(admin),
            json={"access_kind": "bogus"},
        )

        assert response.status_code == 400, response.get_data(as_text=True)
        assert "error" in response.get_json()
