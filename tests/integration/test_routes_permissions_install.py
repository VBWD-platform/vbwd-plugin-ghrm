"""Integration tests for S49.4 — GHRM routes, permission taxonomy, install.

These run against the in-process Flask test client with the real ``db`` fixture
(no external backend). They prove:

* the admin permission taxonomy (D5) — package routes gate on
  ``ghrm.packages.*`` and access routes on ``ghrm.access.*``, independently;
* ``GET /api/v1/ghrm/access`` lazily promotes an accepted INVITED membership to
  ACTIVE (D2) and surfaces an ERROR membership's ``last_error`` (D6);
* ``GET /api/v1/ghrm/packages/<slug>/install`` returns fine-grained-PAT + clone
  guidance for ACTIVE, an "accept invitation" payload for INVITED, and 403 for
  a user with no membership (D3).

The GHRM access service builds a GitHub client per request; we force the mock
(``GHRM_USE_MOCK_GITHUB=true``) so no real credentials are needed.
"""
import os
import uuid

import jwt
import pytest

os.environ["GHRM_USE_MOCK_GITHUB"] = "true"

from vbwd.extensions import db as _db  # noqa: E402
from vbwd.models.user import User  # noqa: E402
from vbwd.models.enums import UserStatus, UserRole  # noqa: E402
from vbwd.models.role import Role, Permission  # noqa: E402
from plugins.ghrm.src.models.ghrm_software_package import (  # noqa: E402
    GhrmSoftwarePackage,
)
from plugins.ghrm.src.models.ghrm_user_github_access import (  # noqa: E402
    GhrmUserGithubAccess,
)
from plugins.ghrm.src.models.ghrm_repo_membership import MembershipStatus  # noqa: E402
from plugins.ghrm.src.repositories.repo_membership_repository import (  # noqa: E402
    GhrmRepoMembershipRepository,
)
from plugins.subscription.subscription.models.tarif_plan import (  # noqa: E402
    TarifPlan,
)
from vbwd.models.enums import BillingPeriod  # noqa: E402


# ── Harness helpers ─────────────────────────────────────────────────────────


def _bearer(user: User) -> dict:
    """Mint a JWT the auth middleware accepts (same payload + key as login)."""
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
    """An ADMIN user whose ONLY admin permissions are the given ones.

    An ADMIN with an assigned access level (role) loses the legacy
    "all permissions" fallback, so the role's permission set is exactly the
    effective set — which is what lets us prove per-permission gating.
    """
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
        price_float=0.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    _db.session.add(plan)
    _db.session.flush()
    return plan


def _make_package(slug: str) -> GhrmSoftwarePackage:
    plan = _make_plan()
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


def _connect_github(user: User, username: str = "octocat") -> GhrmUserGithubAccess:
    access = GhrmUserGithubAccess(
        user_id=user.id,
        github_username=username,
        github_user_id="999",
    )
    _db.session.add(access)
    _db.session.flush()
    return access


# ── Permission taxonomy (D5) ────────────────────────────────────────────────


class TestPermissionTaxonomy:
    def test_packages_manage_can_create_but_not_access_sync(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        plan = _make_plan()
        _db.session.commit()
        headers = _bearer(admin)

        create = client.post(
            "/api/v1/admin/ghrm/packages",
            headers=headers,
            json={
                "name": "Pkg",
                "slug": f"pkg-{uuid.uuid4().hex[:8]}",
                "github_owner": "acme",
                "github_repo": f"widget-{uuid.uuid4().hex[:8]}",
                "tariff_plan_id": str(plan.id),
            },
        )
        assert create.status_code == 201, create.get_data(as_text=True)

        sync = client.post(
            f"/api/v1/admin/ghrm/access/sync/{uuid.uuid4()}", headers=headers
        )
        assert sync.status_code == 403

    def test_access_manage_can_sync_but_not_create_package(self, db, client):
        admin = _admin_with_permissions("ghrm.access.manage")
        headers = _bearer(admin)

        sync = client.post(
            f"/api/v1/admin/ghrm/access/sync/{uuid.uuid4()}", headers=headers
        )
        assert sync.status_code == 200, sync.get_data(as_text=True)

        create = client.post(
            "/api/v1/admin/ghrm/packages",
            headers=headers,
            json={
                "name": "Pkg",
                "slug": f"pkg-{uuid.uuid4().hex[:8]}",
                "github_owner": "acme",
                "github_repo": "widget",
                "tariff_plan_id": str(uuid.uuid4()),
            },
        )
        assert create.status_code == 403

    def test_access_view_can_read_access_log_only(self, db, client):
        admin = _admin_with_permissions("ghrm.access.view")
        headers = _bearer(admin)

        log = client.get("/api/v1/admin/ghrm/access-log", headers=headers)
        assert log.status_code == 200, log.get_data(as_text=True)

        packages = client.get("/api/v1/admin/ghrm/packages", headers=headers)
        assert packages.status_code == 403

    def test_packages_view_can_read_packages_only(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.view")
        headers = _bearer(admin)

        packages = client.get("/api/v1/admin/ghrm/packages", headers=headers)
        assert packages.status_code == 200, packages.get_data(as_text=True)

        log = client.get("/api/v1/admin/ghrm/access-log", headers=headers)
        assert log.status_code == 403

    def test_superadmin_has_all(self, db, client):
        admin = _make_user(role=UserRole.SUPER_ADMIN)
        headers = _bearer(admin)
        assert (
            client.get("/api/v1/admin/ghrm/packages", headers=headers).status_code
            == 200
        )
        assert (
            client.get("/api/v1/admin/ghrm/access-log", headers=headers).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/admin/ghrm/access/sync/{uuid.uuid4()}", headers=headers
            ).status_code
            == 200
        )


# ── GET /api/v1/ghrm/access (D2/D6) ──────────────────────────────────────────


class TestAccessEndpoint:
    def test_invited_membership_promotes_to_active_when_accepted(
        self, db, client, monkeypatch
    ):
        from plugins.ghrm.src.services import github_app_client

        # Simulate the user having accepted the invitation on GitHub.
        monkeypatch.setattr(
            github_app_client.MockGithubAppClient,
            "is_collaborator",
            lambda self, owner, repo, username: True,
        )

        user = _make_user()
        _connect_github(user)
        package = _make_package(f"accepted-{uuid.uuid4().hex[:8]}")
        GhrmRepoMembershipRepository(_db.session).upsert(
            user.id, package.id, status=MembershipStatus.INVITED.value
        )
        _db.session.commit()

        response = client.get("/api/v1/ghrm/access", headers=_bearer(user))
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["connected"] is True
        statuses = {m["package_slug"]: m["status"] for m in body["memberships"]}
        assert statuses[package.slug] == MembershipStatus.ACTIVE.value

    def test_error_membership_surfaces_last_error(self, db, client):
        user = _make_user()
        _connect_github(user)
        package = _make_package(f"errored-{uuid.uuid4().hex[:8]}")
        GhrmRepoMembershipRepository(_db.session).upsert(
            user.id,
            package.id,
            status=MembershipStatus.ERROR.value,
            last_error="GitHub 403: Administration permission required",
        )
        _db.session.commit()

        response = client.get("/api/v1/ghrm/access", headers=_bearer(user))
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        membership = next(
            m for m in body["memberships"] if m["package_slug"] == package.slug
        )
        assert membership["status"] == MembershipStatus.ERROR.value
        assert membership["last_error"] == (
            "GitHub 403: Administration permission required"
        )

    def test_not_connected_returns_connected_false(self, db, client):
        user = _make_user()
        _db.session.commit()
        response = client.get("/api/v1/ghrm/access", headers=_bearer(user))
        assert response.status_code == 200
        assert response.get_json() == {"connected": False}


# ── GET /api/v1/ghrm/packages/<slug>/install (D3) ────────────────────────────


class TestInstallEndpoint:
    def test_active_membership_returns_pat_and_clone_guidance(self, db, client):
        user = _make_user()
        _connect_github(user, username="octocat")
        package = _make_package(f"active-{uuid.uuid4().hex[:8]}")
        GhrmRepoMembershipRepository(_db.session).upsert(
            user.id, package.id, status=MembershipStatus.ACTIVE.value
        )
        _db.session.commit()

        response = client.get(
            f"/api/v1/ghrm/packages/{package.slug}/install", headers=_bearer(user)
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["state"] == "active"
        # Fine-grained PAT creation step is present.
        steps_text = " ".join(body["pat_steps"]).lower()
        assert "fine-grained" in steps_text
        assert "contents: read" in steps_text
        # Clone URL embeds the verified username + a PAT placeholder.
        assert body["clone_https"] == (
            f"git clone https://octocat:<PAT>@github.com/acme/{package.github_repo}.git"
        )
        assert "git@github.com:acme/" in body["clone_ssh"]
        # Deploy tokens are gone.
        assert "deploy_token" not in body

    def test_invited_membership_returns_accept_invitation(self, db, client):
        user = _make_user()
        _connect_github(user)
        package = _make_package(f"invited-{uuid.uuid4().hex[:8]}")
        GhrmRepoMembershipRepository(_db.session).upsert(
            user.id, package.id, status=MembershipStatus.INVITED.value
        )
        _db.session.commit()

        response = client.get(
            f"/api/v1/ghrm/packages/{package.slug}/install", headers=_bearer(user)
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        body = response.get_json()
        assert body["state"] == "invited"
        assert "accept" in body["message"].lower()
        assert body["invitations_url"] == "https://github.com/notifications"

    def test_no_membership_returns_403(self, db, client):
        user = _make_user()
        _connect_github(user)
        package = _make_package(f"nomember-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.get(
            f"/api/v1/ghrm/packages/{package.slug}/install", headers=_bearer(user)
        )
        assert response.status_code == 403, response.get_data(as_text=True)


@pytest.fixture(autouse=True)
def _rollback(db):
    """Each test commits its own fixtures; clean up afterwards."""
    yield
    db.session.rollback()
