"""Integration test — POST /api/v1/admin/ghrm/packages/bulk/copy (make-a-copy).

The fe-admin GHRM catalogue needs a bulk "make a copy" action. A copied
``GhrmSoftwarePackage`` lands UNLINKED from any tariff plan (``tariff_plan_id =
NULL``) because the plan link is UNIQUE 1:1 and a copy must not steal or
duplicate the source's plan. Postgres permits many NULLs under a UNIQUE column,
so copying the same package twice succeeds. A copy is always inactive, gets a
brand-new sync secret, a reset download counter, and a fresh unique slug.

Runs in-process against the Flask test client with the real ``db`` fixture (no
external backend). Mirrors ``test_ghrm_admin_get_single_package.py``. Requires
the plan-nullable migration to have been applied to the shared ``*_test`` DB
(``alembic upgrade heads``) so the ``NOT NULL`` constraint is dropped.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID/DI/
DRY; Liskov (unknown id skipped, never a crash); no overengineering. Quality
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
from plugins.ghrm.src.repositories.software_package_repository import (  # noqa: E402
    GhrmSoftwarePackageRepository,
)
from plugins.subscription.subscription.models.tarif_plan import (  # noqa: E402
    TarifPlan,
)


BULK_COPY_URL = "/api/v1/admin/ghrm/packages/bulk/copy"


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


def _make_package(slug: str, **overrides) -> GhrmSoftwarePackage:
    plan = TarifPlan(
        name=f"Plan {uuid.uuid4().hex[:8]}",
        slug=f"plan-{uuid.uuid4().hex[:8]}",
        price=0.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    _db.session.add(plan)
    _db.session.flush()
    fields = dict(
        tariff_plan_id=plan.id,
        name=f"Package {slug}",
        slug=slug,
        github_owner="acme",
        github_repo=slug,
        github_protected_branch="release",
    )
    fields.update(overrides)
    package = GhrmSoftwarePackage(**fields)
    _db.session.add(package)
    _db.session.flush()
    return package


def _count_packages() -> int:
    return _db.session.query(GhrmSoftwarePackage).count()


class TestBulkCopyPackages:
    def test_copy_is_inactive_and_unlinked(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        package = _make_package(f"src-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.post(
            BULK_COPY_URL, json={"ids": [str(package.id)]}, headers=_bearer(admin)
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body["count"] == 1
        created = body["packages"][0]
        assert created["is_active"] is False
        assert created["tariff_plan_id"] is None
        assert created["name"] == f"{package.name} (Copy)"
        assert created["download_counter"] == 0

    def test_copying_twice_yields_two_null_linked_copies_with_distinct_slugs(
        self, db, client
    ):
        admin = _admin_with_permissions("ghrm.packages.manage")
        package = _make_package(f"twice-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        first = client.post(
            BULK_COPY_URL, json={"ids": [str(package.id)]}, headers=_bearer(admin)
        )
        second = client.post(
            BULK_COPY_URL, json={"ids": [str(package.id)]}, headers=_bearer(admin)
        )

        assert first.status_code == 201, first.get_data(as_text=True)
        assert second.status_code == 201, second.get_data(as_text=True)
        first_copy = first.get_json()["packages"][0]
        second_copy = second.get_json()["packages"][0]
        # Two unlinked copies coexist (many NULLs under a UNIQUE column).
        assert first_copy["tariff_plan_id"] is None
        assert second_copy["tariff_plan_id"] is None
        assert first_copy["slug"] != second_copy["slug"]

    def test_copy_slug_stays_within_64_chars(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        long_slug = ("a" * 55) + uuid.uuid4().hex[:7]  # 62 chars, near the limit
        package = _make_package(long_slug)
        _db.session.commit()

        response = client.post(
            BULK_COPY_URL, json={"ids": [str(package.id)]}, headers=_bearer(admin)
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        created_slug = response.get_json()["packages"][0]["slug"]
        assert len(created_slug) <= 64
        assert created_slug.endswith("-copy")

    def test_copy_regenerates_sync_api_key(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        package = _make_package(f"secret-{uuid.uuid4().hex[:8]}")
        source_key = package.sync_api_key
        _db.session.commit()

        response = client.post(
            BULK_COPY_URL, json={"ids": [str(package.id)]}, headers=_bearer(admin)
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        copy_slug = response.get_json()["packages"][0]["slug"]
        copy = GhrmSoftwarePackageRepository(_db.session).find_by_slug(copy_slug)
        assert copy.sync_api_key
        assert copy.sync_api_key != source_key

    def test_copying_a_bundle_keeps_repos_and_creates_exactly_one_package(
        self, db, client
    ):
        admin = _admin_with_permissions("ghrm.packages.manage")
        bundle_repos = [
            {"owner": "acme", "repo": "alpha"},
            {"owner": "acme", "repo": "beta"},
        ]
        package = _make_package(
            f"bundle-{uuid.uuid4().hex[:8]}",
            package_kind="bundle",
            bundle_repos=bundle_repos,
        )
        _db.session.commit()
        count_before = _count_packages()

        response = client.post(
            BULK_COPY_URL, json={"ids": [str(package.id)]}, headers=_bearer(admin)
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        created = response.get_json()["packages"][0]
        assert created["package_kind"] == "bundle"
        assert created["bundle_repos"] == bundle_repos
        # A bundle's members are an embedded JSON list, not child rows — the copy
        # adds exactly one package row and touches no member repos.
        assert _count_packages() == count_before + 1

    def test_unknown_id_is_skipped_not_fatal(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.manage")
        package = _make_package(f"mixed-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.post(
            BULK_COPY_URL,
            json={"ids": [str(uuid.uuid4()), str(package.id)]},
            headers=_bearer(admin),
        )

        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body["count"] == 1

    def test_unlinked_copy_is_not_in_public_catalogue(self, db, client):
        # Not purchasable/downloadable: the public catalogue only lists active
        # packages, and a copy is always inactive + unlinked from any plan.
        admin = _admin_with_permissions("ghrm.packages.manage")
        package = _make_package(f"public-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        client.post(
            BULK_COPY_URL, json={"ids": [str(package.id)]}, headers=_bearer(admin)
        )

        listing = client.get("/api/v1/ghrm/packages?page=1")
        assert listing.status_code == 200, listing.get_data(as_text=True)
        slugs = [item["slug"] for item in listing.get_json()["items"]]
        assert f"{package.slug}-copy" not in slugs

    def test_requires_packages_manage_permission(self, db, client):
        admin = _admin_with_permissions("ghrm.packages.view")  # view is not enough
        package = _make_package(f"gated-{uuid.uuid4().hex[:8]}")
        _db.session.commit()

        response = client.post(
            BULK_COPY_URL, json={"ids": [str(package.id)]}, headers=_bearer(admin)
        )

        assert response.status_code == 403


@pytest.fixture(autouse=True)
def _rollback(db):
    """Each test commits its own fixtures; clean up afterwards."""
    yield
    db.session.rollback()
