"""Vendor self-service GHRM package route — gated, permission-checked.

When ``marketplace_enabled`` is False the vendor surface is invisible (403);
when True a user holding ``marketplace.vendor`` can sell a GHRM software package
as a subscription. The created package is linked to a subscription plan the
caller OWNS (``vendor_id`` = their user id) — so subscription's existing
checkout stamp attributes the sale to the vendor automatically on purchase.
"""
from uuid import uuid4

import pytest

from plugins.ghrm.src import routes


VENDOR_PACKAGES_PATH = "/api/v1/ghrm/vendor/packages"


@pytest.fixture
def client(app):
    return app.test_client()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(app, email):
    from vbwd.extensions import db
    from vbwd.repositories.user_repository import UserRepository

    user_repository = UserRepository(db.session)
    auth_service = app.container.auth_service()
    if user_repository.find_by_email(email) is None:
        auth_service.register(email=email, password="Vendor123@")
        db.session.commit()
    user = user_repository.find_by_email(email)
    login = auth_service.login(email=email, password="Vendor123@")
    return user, login.token


def _grant_vendor_permission(db, user):
    """Attach a user access level carrying ``marketplace.vendor`` to ``user``."""
    from vbwd.models.role import Permission
    from vbwd.models.user_access_level import UserAccessLevel

    permission = (
        db.session.query(Permission).filter_by(name="marketplace.vendor").first()
    )
    if permission is None:
        permission = Permission(
            id=uuid4(),
            name="marketplace.vendor",
            description="Sell as a vendor",
            resource="marketplace",
            action="vendor",
        )
        db.session.add(permission)
    suffix = uuid4().hex[:8]
    level = UserAccessLevel(
        id=uuid4(),
        slug=f"vendor-{suffix}",
        name=f"Vendor {suffix}",
    )
    level.permissions.append(permission)
    user.assigned_user_access_levels.append(level)
    db.session.commit()


def _make_vendor(app, db, email):
    user, token = _register(app, email)
    _grant_vendor_permission(db, user)
    return user, token


def _enable_marketplace(monkeypatch, enabled):
    monkeypatch.setattr(routes, "marketplace_enabled", lambda: enabled)


def _package_body(name="Vendor Package"):
    return {
        "name": name,
        "slug": f"vp-{uuid4().hex[:8]}",
        "description": "A software package sold as a subscription",
        "github_owner": "acme",
        "github_repo": "widget",
        "price": 19.0,
        "billing_period": "MONTHLY",
    }


def test_vendor_package_blocked_when_marketplace_disabled(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gv-off-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, False)

    resp = client.post(VENDOR_PACKAGES_PATH, json=_package_body(), headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


def test_vendor_package_requires_permission(app, db, client, monkeypatch):
    # A plain user (no marketplace.vendor) is rejected even when enabled.
    _user, token = _register(app, f"gv-plain-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.post(VENDOR_PACKAGES_PATH, json=_package_body(), headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


def test_vendor_package_creates_plan_with_vendor_id(app, db, client, monkeypatch):
    from plugins.subscription.subscription.repositories.tarif_plan_repository import (
        TarifPlanRepository,
    )

    user, token = _make_vendor(app, db, f"gv-create-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    body = _package_body("My Software")
    resp = client.post(VENDOR_PACKAGES_PATH, json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.get_json()

    package = resp.get_json()["package"]
    assert package["is_active"] is True
    assert package["github_owner"] == "acme"
    assert package["github_repo"] == "widget"
    assert package["tariff_plan_id"]

    plan = TarifPlanRepository(db.session).find_by_slug(body["slug"])
    assert plan is not None
    assert str(plan.id) == package["tariff_plan_id"]
    assert str(plan.vendor_id) == str(user.id)
    assert plan.is_active is True


def test_vendor_package_missing_fields_is_400(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gv-nop-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    body = _package_body()
    del body["price"]
    resp = client.post(VENDOR_PACKAGES_PATH, json=body, headers=_auth(token))
    assert resp.status_code == 400, resp.get_json()


# ─── READ / UPDATE / DELETE (vendor self-service) ────────────────────────────


def _create_package(client, token, name="Vendor Package"):
    """POST a package and return its response JSON ``package`` dict."""
    body = _package_body(name)
    resp = client.post(VENDOR_PACKAGES_PATH, json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["package"]


def test_vendor_list_returns_only_owned_packages(app, db, client, monkeypatch):
    owner, owner_token = _make_vendor(
        app, db, f"gv-l-own-{uuid4().hex[:6]}@example.com"
    )
    other, other_token = _make_vendor(
        app, db, f"gv-l-oth-{uuid4().hex[:6]}@example.com"
    )
    _enable_marketplace(monkeypatch, True)

    mine = _create_package(client, owner_token, "Mine A")
    _create_package(client, other_token, "Theirs B")

    resp = client.get(VENDOR_PACKAGES_PATH, headers=_auth(owner_token))
    assert resp.status_code == 200, resp.get_json()
    packages = resp.get_json()["packages"]
    ids = {pkg["id"] for pkg in packages}
    assert mine["id"] in ids
    # Only the owner's package is returned.
    assert len(packages) == 1
    item = packages[0]
    for key in ("id", "name", "price", "github_owner", "github_repo", "is_active"):
        assert key in item, key
    assert item["plan_id"] == mine["tariff_plan_id"]
    assert item["price"] == 19.0


def test_vendor_list_blocked_when_marketplace_disabled(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gv-l-off-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, False)

    resp = client.get(VENDOR_PACKAGES_PATH, headers=_auth(token))
    assert resp.status_code == 403, resp.get_json()


def test_vendor_get_one_returns_owned_package(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gv-g-own-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "Get Me")

    resp = client.get(f"{VENDOR_PACKAGES_PATH}/{created['id']}", headers=_auth(token))
    assert resp.status_code == 200, resp.get_json()
    package = resp.get_json()["package"]
    assert package["id"] == created["id"]
    assert package["plan_id"] == created["tariff_plan_id"]
    assert package["price"] == 19.0


def test_vendor_get_one_404_when_missing(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gv-g-404-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.get(f"{VENDOR_PACKAGES_PATH}/{uuid4()}", headers=_auth(token))
    assert resp.status_code == 404, resp.get_json()
    assert resp.get_json()["error"] == "Package not found"


def test_vendor_get_one_403_when_not_owned(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"gv-g-o-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"gv-g-x-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, owner_token, "Not Yours")

    resp = client.get(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}", headers=_auth(other_token)
    )
    assert resp.status_code == 403, resp.get_json()
    assert resp.get_json()["error"] == "You do not own this package"


def test_vendor_update_applies_fields(app, db, client, monkeypatch):
    from plugins.subscription.subscription.repositories.tarif_plan_repository import (
        TarifPlanRepository,
    )

    _user, token = _make_vendor(app, db, f"gv-u-own-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "Before")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}",
        json={
            "name": "After",
            "price": 42.5,
            "github_owner": "newowner",
            "github_repo": "newrepo",
            "is_active": False,
            "description": "Updated description",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_json()
    package = resp.get_json()["package"]
    assert package["name"] == "After"
    assert package["github_owner"] == "newowner"
    assert package["github_repo"] == "newrepo"
    assert package["is_active"] is False
    assert package["description"] == "Updated description"
    assert package["price"] == 42.5

    plan = TarifPlanRepository(db.session).find_by_id(created["tariff_plan_id"])
    assert plan.raw_price == 42.5
    assert plan.is_active is False


def test_vendor_update_403_when_not_owned(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"gv-u-o-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"gv-u-x-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, owner_token, "Owned")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}",
        json={"name": "Hacked"},
        headers=_auth(other_token),
    )
    assert resp.status_code == 403, resp.get_json()


def test_vendor_delete_removes_package_and_plan(app, db, client, monkeypatch):
    from plugins.ghrm.src.repositories.software_package_repository import (
        GhrmSoftwarePackageRepository,
    )
    from plugins.subscription.subscription.repositories.tarif_plan_repository import (
        TarifPlanRepository,
    )

    _user, token = _make_vendor(app, db, f"gv-d-own-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "Delete Me")

    resp = client.delete(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["success"] is True

    assert GhrmSoftwarePackageRepository(db.session).find_by_id(created["id"]) is None
    assert TarifPlanRepository(db.session).find_by_id(created["tariff_plan_id"]) is None


def test_vendor_delete_404_when_missing(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gv-d-404-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)

    resp = client.delete(f"{VENDOR_PACKAGES_PATH}/{uuid4()}", headers=_auth(token))
    assert resp.status_code == 404, resp.get_json()


def test_vendor_delete_403_when_not_owned(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"gv-d-o-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"gv-d-x-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, owner_token, "Owned")

    resp = client.delete(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}", headers=_auth(other_token)
    )
    assert resp.status_code == 403, resp.get_json()
