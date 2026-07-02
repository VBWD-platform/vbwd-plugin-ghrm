"""Vendor self-service GHRM tags + custom-fields routes — gated, ownership-checked.

Mirrors the shop vendor tags/custom-fields pattern for a vendor's OWN GHRM
software packages: the routes resolve the core ``tags_and_custom_fields()`` port
(so vendor + admin never diverge), enforce ownership via the linked plan's
``vendor_id`` (404 missing / 403 not owned), and validate payload shape (400).

No stock, no images for GHRM — only tags + custom fields.
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


def _create_package(client, token, name="Vendor Package"):
    body = _package_body(name)
    resp = client.post(VENDOR_PACKAGES_PATH, json=body, headers=_auth(token))
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["package"]


# ─── Tags ────────────────────────────────────────────────────────────────────


def test_vendor_get_tags_empty_for_owned_package(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gvt-g-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "Taggable")

    resp = client.get(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/tags", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["tags"] == []


def test_vendor_put_tags_replaces_and_returns(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gvt-p-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "Taggable Put")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/tags",
        json={"tags": ["cli", "backend"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_json()
    assert set(resp.get_json()["tags"]) == {"cli", "backend"}

    # Persisted: a follow-up GET reflects the same set.
    read = client.get(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/tags", headers=_auth(token)
    )
    assert set(read.get_json()["tags"]) == {"cli", "backend"}


def test_vendor_put_tags_non_list_is_400(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gvt-b-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "Bad Tags")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/tags",
        json={"tags": "not-a-list"},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.get_json()


def test_vendor_get_tags_403_when_not_owned(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"gvt-o-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"gvt-x-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, owner_token, "Owned Tags")

    resp = client.get(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/tags", headers=_auth(other_token)
    )
    assert resp.status_code == 403, resp.get_json()


def test_vendor_put_tags_403_when_not_owned(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"gvt-uo-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"gvt-ux-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, owner_token, "Owned Tags Put")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/tags",
        json={"tags": ["cli"]},
        headers=_auth(other_token),
    )
    assert resp.status_code == 403, resp.get_json()


def test_vendor_get_tags_blocked_when_marketplace_disabled(
    app, db, client, monkeypatch
):
    _user, token = _make_vendor(app, db, f"gvt-off-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "Off Tags")
    _enable_marketplace(monkeypatch, False)

    resp = client.get(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/tags", headers=_auth(token)
    )
    assert resp.status_code == 403, resp.get_json()


# ─── Custom fields ───────────────────────────────────────────────────────────


def test_vendor_get_custom_fields_empty_for_owned_package(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gvc-g-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "CF Get")

    resp = client.get(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/custom-fields", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["custom_fields"] == {}


def test_vendor_put_custom_fields_empty_object_ok(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gvc-p-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "CF Put")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/custom-fields",
        json={"custom_fields": {}},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["custom_fields"] == {}


def test_vendor_put_custom_fields_non_object_is_400(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gvc-b-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "CF Bad")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/custom-fields",
        json={"custom_fields": ["not", "an", "object"]},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.get_json()


def test_vendor_put_custom_fields_unknown_key_is_400(app, db, client, monkeypatch):
    _user, token = _make_vendor(app, db, f"gvc-u-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "CF Unknown")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/custom-fields",
        json={"custom_fields": {"no_such_field": "x"}},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.get_json()


def test_vendor_get_custom_fields_403_when_not_owned(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"gvc-o-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"gvc-x-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, owner_token, "Owned CF")

    resp = client.get(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/custom-fields",
        headers=_auth(other_token),
    )
    assert resp.status_code == 403, resp.get_json()


def test_vendor_put_custom_fields_403_when_not_owned(app, db, client, monkeypatch):
    _owner, owner_token = _make_vendor(app, db, f"gvc-uo-{uuid4().hex[:6]}@example.com")
    _other, other_token = _make_vendor(app, db, f"gvc-ux-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, owner_token, "Owned CF Put")

    resp = client.put(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/custom-fields",
        json={"custom_fields": {}},
        headers=_auth(other_token),
    )
    assert resp.status_code == 403, resp.get_json()


def test_vendor_custom_fields_blocked_when_marketplace_disabled(
    app, db, client, monkeypatch
):
    _user, token = _make_vendor(app, db, f"gvc-off-{uuid4().hex[:6]}@example.com")
    _enable_marketplace(monkeypatch, True)
    created = _create_package(client, token, "Off CF")
    _enable_marketplace(monkeypatch, False)

    resp = client.get(
        f"{VENDOR_PACKAGES_PATH}/{created['id']}/custom-fields", headers=_auth(token)
    )
    assert resp.status_code == 403, resp.get_json()
