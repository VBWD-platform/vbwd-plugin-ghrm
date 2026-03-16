"""Integration tests for GHRM packages API — category filtering via tariff plan.

A GHRM package is linked to a tariff plan.  That plan belongs to one or more
TarifPlanCategory rows.  The public catalogue endpoint filters packages by
category through this relationship — no separate category_slug field exists
on the package itself.

Run with:
    docker compose --profile test-integration run --rm test-integration \\
        pytest plugins/ghrm/tests/integration/test_ghrm_packages_api.py -v

Or via the nginx proxy (port 8080):
    API_BASE_URL=http://localhost:8080/api/v1 pytest ...
"""
import pytest
import requests
import os
from uuid import uuid4


BASE_URL = os.getenv("API_BASE_URL", "http://localhost:5000/api/v1")
ADMIN_EMAIL = os.getenv("TEST_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.getenv("TEST_ADMIN_PASSWORD", "AdminPass123@")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def backend_available():
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        if r.status_code != 200:
            pytest.skip("Backend not healthy")
    except requests.exceptions.ConnectionError:
        pytest.skip("Backend not reachable")


@pytest.fixture(scope="module")
def admin_token(backend_available):
    r = requests.post(f"{BASE_URL}/auth/login",
                      json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                      timeout=10)
    assert r.status_code == 200, f"Admin login failed: {r.text}"
    data = r.json()
    return data.get("token") or data.get("access_token")


@pytest.fixture(scope="module")
def auth(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_plan(auth: dict, name: str) -> dict:
    r = requests.post(f"{BASE_URL}/admin/tarif-plans",
                      headers=auth,
                      json={"name": name, "price": "0.00"},
                      timeout=10)
    assert r.status_code == 201, f"Plan create failed: {r.text}"
    return r.json()["plan"]


def _get_or_create_category(auth: dict, slug: str, name: str) -> dict:
    """Return an existing category by slug, or create it."""
    r = requests.get(f"{BASE_URL}/admin/tarif-plan-categories", headers=auth, timeout=10)
    assert r.status_code == 200
    for cat in r.json()["categories"]:
        if cat["slug"] == slug:
            return cat
    r = requests.post(f"{BASE_URL}/admin/tarif-plan-categories",
                      headers=auth,
                      json={"name": name, "slug": slug},
                      timeout=10)
    assert r.status_code == 201, f"Category create failed: {r.text}"
    return r.json()["category"]


def _attach_plan_to_category(auth: dict, cat_id: str, plan_id: str) -> None:
    r = requests.post(f"{BASE_URL}/admin/tarif-plan-categories/{cat_id}/attach-plans",
                      headers=auth,
                      json={"plan_ids": [plan_id]},
                      timeout=10)
    assert r.status_code == 200, f"Attach failed: {r.text}"


def _detach_plan_from_category(auth: dict, cat_id: str, plan_id: str) -> None:
    requests.post(f"{BASE_URL}/admin/tarif-plan-categories/{cat_id}/detach-plans",
                  headers=auth,
                  json={"plan_ids": [plan_id]},
                  timeout=10)


def _create_ghrm_pkg(auth: dict, plan_id: str, slug: str) -> dict:
    r = requests.post(f"{BASE_URL}/admin/ghrm/packages",
                      headers=auth,
                      json={
                          "tariff_plan_id": plan_id,
                          "name": f"Test Pkg {slug}",
                          "slug": slug,
                          "github_owner": "test-org",
                          "github_repo": slug,
                      },
                      timeout=10)
    assert r.status_code == 201, f"GHRM package create failed: {r.text}"
    return r.json()


def _delete_plan(auth: dict, plan_id: str) -> None:
    requests.delete(f"{BASE_URL}/admin/tarif-plans/{plan_id}", headers=auth, timeout=10)


def _delete_ghrm_pkg(auth: dict, pkg_id: str) -> None:
    requests.delete(f"{BASE_URL}/admin/ghrm/packages/{pkg_id}", headers=auth, timeout=10)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestGhrmCategoryFilter:
    """Packages are filtered by the tariff plan's category, not a separate field."""

    @pytest.fixture(autouse=True)
    def setup_packages(self, auth):
        uid = uuid4().hex[:8]

        # Two plans, each attached to a different category
        self.plan_fe = _create_plan(auth, f"FE-User Plan {uid}")
        self.plan_be = _create_plan(auth, f"Backend Plan {uid}")

        self.cat_fe = _get_or_create_category(auth, "fe-user", "FE User")
        self.cat_be = _get_or_create_category(auth, "backend", "Backend")

        _attach_plan_to_category(auth, self.cat_fe["id"], self.plan_fe["id"])
        _attach_plan_to_category(auth, self.cat_be["id"], self.plan_be["id"])

        self.pkg_fe = _create_ghrm_pkg(auth, self.plan_fe["id"], f"fe-pkg-{uid}")
        self.pkg_be = _create_ghrm_pkg(auth, self.plan_be["id"], f"be-pkg-{uid}")

        yield

        _delete_ghrm_pkg(auth, self.pkg_fe["id"])
        _delete_ghrm_pkg(auth, self.pkg_be["id"])
        _detach_plan_from_category(auth, self.cat_fe["id"], self.plan_fe["id"])
        _detach_plan_from_category(auth, self.cat_be["id"], self.plan_be["id"])
        _delete_plan(auth, self.plan_fe["id"])
        _delete_plan(auth, self.plan_be["id"])

    def test_fe_user_filter_returns_fe_package(self):
        r = requests.get(f"{BASE_URL}/ghrm/packages",
                         params={"category_slug": "fe-user", "page": "1"},
                         timeout=10)
        assert r.status_code == 200, r.text
        slugs = [p["slug"] for p in r.json()["items"]]
        assert self.pkg_fe["slug"] in slugs, "fe-user package missing from fe-user results"
        assert self.pkg_be["slug"] not in slugs, "backend package leaked into fe-user results"

    def test_backend_filter_returns_backend_package(self):
        r = requests.get(f"{BASE_URL}/ghrm/packages",
                         params={"category_slug": "backend", "page": "1"},
                         timeout=10)
        assert r.status_code == 200, r.text
        slugs = [p["slug"] for p in r.json()["items"]]
        assert self.pkg_be["slug"] in slugs, "backend package missing from backend results"
        assert self.pkg_fe["slug"] not in slugs, "fe-user package leaked into backend results"

    def test_no_filter_returns_all_packages(self):
        r = requests.get(f"{BASE_URL}/ghrm/packages", params={"page": "1"}, timeout=10)
        assert r.status_code == 200, r.text
        slugs = [p["slug"] for p in r.json()["items"]]
        assert self.pkg_fe["slug"] in slugs
        assert self.pkg_be["slug"] in slugs

    def test_unknown_category_returns_empty(self):
        r = requests.get(f"{BASE_URL}/ghrm/packages",
                         params={"category_slug": "nonexistent-xyz"},
                         timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_fe_admin_category_does_not_include_other_packages(self, auth):
        _get_or_create_category(auth, "fe-admin", "FE Admin")
        r = requests.get(f"{BASE_URL}/ghrm/packages",
                         params={"category_slug": "fe-admin"},
                         timeout=10)
        assert r.status_code == 200
        slugs = [p["slug"] for p in r.json()["items"]]
        assert self.pkg_fe["slug"] not in slugs
        assert self.pkg_be["slug"] not in slugs

    def test_response_shape(self):
        r = requests.get(f"{BASE_URL}/ghrm/packages",
                         params={"category_slug": "fe-user"},
                         timeout=10)
        assert r.status_code == 200
        data = r.json()
        for field in ("items", "total", "page", "per_page", "pages"):
            assert field in data, f"Missing '{field}'"
        assert data["items"]
        for field in ("id", "slug", "name", "download_counter"):
            assert field in data["items"][0], f"Missing item field '{field}'"

    def test_reassigning_plan_category_changes_filter(self, auth):
        """Detaching a plan from fe-user and attaching to fe-admin moves its package."""
        _detach_plan_from_category(auth, self.cat_fe["id"], self.plan_fe["id"])

        cat_fa = _get_or_create_category(auth, "fe-admin", "FE Admin")
        _attach_plan_to_category(auth, cat_fa["id"], self.plan_fe["id"])

        # No longer in fe-user
        r = requests.get(f"{BASE_URL}/ghrm/packages",
                         params={"category_slug": "fe-user"}, timeout=10)
        assert self.pkg_fe["slug"] not in [p["slug"] for p in r.json()["items"]]

        # Now in fe-admin
        r = requests.get(f"{BASE_URL}/ghrm/packages",
                         params={"category_slug": "fe-admin"}, timeout=10)
        assert self.pkg_fe["slug"] in [p["slug"] for p in r.json()["items"]]

        # Restore
        _detach_plan_from_category(auth, cat_fa["id"], self.plan_fe["id"])
        _attach_plan_to_category(auth, self.cat_fe["id"], self.plan_fe["id"])


class TestGhrmCategories:

    @pytest.fixture(autouse=True)
    def check_available(self, backend_available):
        pass

    def test_categories_endpoint_returns_200(self):
        r = requests.get(f"{BASE_URL}/ghrm/categories", timeout=10)
        assert r.status_code == 200

    def test_categories_have_slug_and_label(self):
        data = requests.get(f"{BASE_URL}/ghrm/categories", timeout=10).json()
        assert "categories" in data
        for cat in data["categories"]:
            assert "slug" in cat and "label" in cat

    def test_configured_categories_present(self):
        data = requests.get(f"{BASE_URL}/ghrm/categories", timeout=10).json()
        slugs = {c["slug"] for c in data["categories"]}
        for expected in ("backend", "fe-user", "fe-admin"):
            assert expected in slugs, f"'{expected}' missing from /ghrm/categories"
