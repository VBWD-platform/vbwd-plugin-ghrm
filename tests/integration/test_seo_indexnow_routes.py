"""S154 D4 — the admin software-package routes trigger the IndexNow ping.

End-to-end through the real Flask route (not just the ``seo_indexnow`` unit),
so the wiring from ``routes.py`` into ``notify_package_changed`` is exercised.
The cms import is monkeypatched at ``seo_indexnow._import_ping_indexnow_urls``
(no real cms/network needed), and the fe-admin "indexable" switch + catalogue
base are driven through the real ``config_store``. Auth mirrors
``test_ghrm_admin_get_single_package.py`` (real JWT + role/permission rows).

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
"""
import os
import uuid
from unittest.mock import MagicMock

import jwt

os.environ["GHRM_USE_MOCK_GITHUB"] = "true"

from vbwd.extensions import db as _db  # noqa: E402
from vbwd.models.enums import BillingPeriod, UserRole, UserStatus  # noqa: E402
from vbwd.models.role import Permission, Role  # noqa: E402
from vbwd.models.user import User  # noqa: E402
from plugins.ghrm.src.models.ghrm_software_package import (  # noqa: E402
    GhrmSoftwarePackage,
)
from plugins.subscription.subscription.models.tarif_plan import TarifPlan  # noqa: E402


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


class _IndexableConfigStore:
    """A fake ``config_store`` driving ghrm's "indexable" switch + catalogue base."""

    def __init__(self, packages_on, catalogue_page_slug="software"):
        self._config = {
            "seo_index_packages": packages_on,
            "software_catalogue_cms_page_slug": catalogue_page_slug,
        }

    def get_config(self, plugin_name):
        return self._config if plugin_name == "ghrm" else {}


def _fake_ping(monkeypatch):
    from plugins.ghrm.src.services import seo_indexnow

    ping = MagicMock()
    monkeypatch.setattr(seo_indexnow, "_import_ping_indexnow_urls", lambda: ping)
    return ping


def test_create_package_pings_indexnow_when_switch_on(app, db, client, monkeypatch):
    admin = _admin_with_permissions("ghrm.packages.manage")
    plan = _make_plan()
    _db.session.commit()
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(app, "config_store", _IndexableConfigStore(True))

    response = client.post(
        "/api/v1/admin/ghrm/packages",
        json={
            "name": "My Tool",
            "slug": "my-tool",
            "github_owner": "acme",
            "github_repo": "my-tool",
            "tariff_plan_id": str(plan.id),
        },
        headers=_bearer(admin),
    )

    assert response.status_code == 201, response.get_data(as_text=True)
    ping.assert_called_once_with(["/software/my-tool"])


def test_create_package_does_not_ping_when_switch_off(app, db, client, monkeypatch):
    admin = _admin_with_permissions("ghrm.packages.manage")
    plan = _make_plan()
    _db.session.commit()
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(app, "config_store", _IndexableConfigStore(False))

    response = client.post(
        "/api/v1/admin/ghrm/packages",
        json={
            "name": "Other Tool",
            "slug": "other-tool",
            "github_owner": "acme",
            "github_repo": "other-tool",
            "tariff_plan_id": str(plan.id),
        },
        headers=_bearer(admin),
    )

    assert response.status_code == 201, response.get_data(as_text=True)
    ping.assert_not_called()


def test_update_package_pings_indexnow(app, db, client, monkeypatch):
    admin = _admin_with_permissions("ghrm.packages.manage")
    package = _make_package("update-pkg")
    _db.session.commit()
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(app, "config_store", _IndexableConfigStore(True))

    response = client.put(
        f"/api/v1/admin/ghrm/packages/{package.id}",
        json={"description": "Updated"},
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    ping.assert_called_once_with(["/software/update-pkg"])


def test_delete_package_pings_indexnow_with_its_slug(app, db, client, monkeypatch):
    admin = _admin_with_permissions("ghrm.packages.manage")
    package = _make_package("delete-pkg")
    _db.session.commit()
    ping = _fake_ping(monkeypatch)
    monkeypatch.setattr(
        app, "config_store", _IndexableConfigStore(True, catalogue_page_slug="store")
    )

    response = client.delete(
        f"/api/v1/admin/ghrm/packages/{package.id}",
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    ping.assert_called_once_with(["/store/delete-pkg"])
