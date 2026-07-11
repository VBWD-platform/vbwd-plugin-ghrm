"""Integration test — PUT /api/v1/admin/ghrm/packages/<id> honours tariff_plan_id.

Bug fixed here: the PUT update route ignored ``tariff_plan_id`` in the body, so
a package created (or copied) unlinked could never be linked to a plan, and
changing the plan on an existing package was silently dropped. This proves the
PUT now links / unlinks the plan and respects the UNIQUE 1:1 constraint on
``ghrm_software_package.tariff_plan_id``:

  * a new plan id links the package (GET afterwards shows the new plan),
  * ``tariff_plan_id: null`` unlinks (tariff_plan_id → None),
  * a plan already linked to ANOTHER package → HTTP 409,
  * re-sending the package's OWN current plan id succeeds (no false 409).

Runs in-process against the Flask test client with the real ``db`` fixture (no
external backend), mirroring ``test_ghrm_admin_get_single_package.py``.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID/DI/
DRY; Liskov (idempotent self-assign is not a conflict); no overengineering.
Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import os
import uuid

import jwt

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


def _make_package(slug: str, plan: TarifPlan = None) -> GhrmSoftwarePackage:
    package = GhrmSoftwarePackage(
        tariff_plan_id=plan.id if plan is not None else None,
        name=f"Package {slug}",
        slug=slug,
        github_owner="acme",
        github_repo=slug,
        github_protected_branch="release",
    )
    _db.session.add(package)
    _db.session.flush()
    return package


class TestAdminUpdatePackageTariffPlan:
    def test_put_links_new_tariff_plan(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage", "ghrm.packages.view")
        package = _make_package(
            f"unlinked-{uuid.uuid4().hex[:8]}"
        )  # tariff_plan_id None
        plan = _make_plan()
        _db.session.commit()

        response = client.put(
            f"/api/v1/admin/ghrm/packages/{package.id}",
            headers=_bearer(admin),
            json={"tariff_plan_id": str(plan.id)},
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["tariff_plan_id"] == str(plan.id)

        # GET afterwards shows the newly-linked plan.
        follow_up = client.get(
            f"/api/v1/admin/ghrm/packages/{package.id}", headers=_bearer(admin)
        )
        assert follow_up.get_json()["tariff_plan_id"] == str(plan.id)

    def test_put_null_unlinks_tariff_plan(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage", "ghrm.packages.view")
        plan = _make_plan()
        package = _make_package(f"linked-{uuid.uuid4().hex[:8]}", plan=plan)
        _db.session.commit()

        response = client.put(
            f"/api/v1/admin/ghrm/packages/{package.id}",
            headers=_bearer(admin),
            json={"tariff_plan_id": None},
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["tariff_plan_id"] is None

        follow_up = client.get(
            f"/api/v1/admin/ghrm/packages/{package.id}", headers=_bearer(admin)
        )
        assert follow_up.get_json()["tariff_plan_id"] is None

    def test_put_empty_string_unlinks_tariff_plan(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage", "ghrm.packages.view")
        plan = _make_plan()
        package = _make_package(f"linked-{uuid.uuid4().hex[:8]}", plan=plan)
        _db.session.commit()

        response = client.put(
            f"/api/v1/admin/ghrm/packages/{package.id}",
            headers=_bearer(admin),
            json={"tariff_plan_id": ""},
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["tariff_plan_id"] is None

    def test_put_plan_linked_to_another_package_returns_409(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage", "ghrm.packages.view")
        taken_plan = _make_plan()
        _make_package(f"owner-{uuid.uuid4().hex[:8]}", plan=taken_plan)
        target = _make_package(f"target-{uuid.uuid4().hex[:8]}")  # unlinked
        _db.session.commit()

        response = client.put(
            f"/api/v1/admin/ghrm/packages/{target.id}",
            headers=_bearer(admin),
            json={"tariff_plan_id": str(taken_plan.id)},
        )

        assert response.status_code == 409, response.get_data(as_text=True)
        assert "error" in response.get_json()

    def test_put_resending_own_current_plan_is_not_a_conflict(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage", "ghrm.packages.view")
        plan = _make_plan()
        package = _make_package(f"own-{uuid.uuid4().hex[:8]}", plan=plan)
        _db.session.commit()

        response = client.put(
            f"/api/v1/admin/ghrm/packages/{package.id}",
            headers=_bearer(admin),
            json={"tariff_plan_id": str(plan.id)},
        )

        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()["tariff_plan_id"] == str(plan.id)
