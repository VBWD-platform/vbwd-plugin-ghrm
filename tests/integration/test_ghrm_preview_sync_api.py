"""Integration tests for GHRM partial-sync (preview + sync field) API.

Tests the two new admin endpoints:
    GET  /api/v1/admin/ghrm/packages/<id>/preview/<field>
    POST /api/v1/admin/ghrm/packages/<id>/sync/<field>

No live GitHub calls — the preview/sync methods require GitHub App credentials
which are not present in CI, so the tests exercise the API contract (status
codes, response shape) and fall back gracefully to 503 when GitHub is absent.

Run with:
    docker compose --profile test-integration run --rm test-integration \\
        pytest plugins/ghrm/tests/integration/test_ghrm_preview_sync_api.py -v
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
    r = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=10,
    )
    assert r.status_code == 200, f"Admin login failed: {r.text}"
    data = r.json()
    return data.get("token") or data.get("access_token")


@pytest.fixture(scope="module")
def auth(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_plan(auth: dict, name: str) -> dict:
    r = requests.post(
        f"{BASE_URL}/admin/tarif-plans",
        headers=auth,
        json={"name": name, "price": "0.00"},
        timeout=10,
    )
    assert r.status_code == 201, f"Plan create failed: {r.text}"
    return r.json()["plan"]


def _create_ghrm_pkg(auth: dict, plan_id: str, slug: str) -> dict:
    r = requests.post(
        f"{BASE_URL}/admin/ghrm/packages",
        headers=auth,
        json={
            "tariff_plan_id": plan_id,
            "name": f"Preview Test Pkg {slug}",
            "slug": slug,
            "github_owner": "test-org",
            "github_repo": slug,
        },
        timeout=10,
    )
    assert r.status_code == 201, f"GHRM package create failed: {r.text}"
    return r.json()


def _delete_plan(auth: dict, plan_id: str) -> None:
    requests.delete(f"{BASE_URL}/admin/tarif-plans/{plan_id}", headers=auth, timeout=10)


def _delete_ghrm_pkg(auth: dict, pkg_id: str) -> None:
    requests.delete(
        f"{BASE_URL}/admin/ghrm/packages/{pkg_id}", headers=auth, timeout=10
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestPreviewAndSyncFieldApi:
    """API contract tests for preview + sync field endpoints."""

    @pytest.fixture(autouse=True)
    def setup_package(self, auth):
        uid = uuid4().hex[:8]
        self.plan = _create_plan(auth, f"Preview Sync Plan {uid}")
        self.pkg = _create_ghrm_pkg(auth, self.plan["id"], f"preview-pkg-{uid}")
        self.pkg_id = self.pkg["id"]
        yield
        _delete_ghrm_pkg(auth, self.pkg_id)
        _delete_plan(auth, self.plan["id"])

    # ── Invalid field ──────────────────────────────────────────────────────────

    def test_preview_invalid_field_returns_400(self, auth):
        r = requests.get(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/preview/invalid_field",
            headers=auth,
            timeout=10,
        )
        assert r.status_code == 400
        assert "error" in r.json()

    def test_sync_invalid_field_returns_400(self, auth):
        r = requests.post(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/sync/invalid_field",
            headers=auth,
            timeout=10,
        )
        assert r.status_code == 400
        assert "error" in r.json()

    # ── Not found ─────────────────────────────────────────────────────────────

    def test_preview_unknown_package_returns_404_or_503(self, auth):
        fake_id = str(uuid4())
        r = requests.get(
            f"{BASE_URL}/admin/ghrm/packages/{fake_id}/preview/readme",
            headers=auth,
            timeout=10,
        )
        # 404 when package not found; 503 when GitHub not configured (checked first)
        assert r.status_code in (404, 503)

    def test_sync_unknown_package_returns_404_or_503(self, auth):
        fake_id = str(uuid4())
        r = requests.post(
            f"{BASE_URL}/admin/ghrm/packages/{fake_id}/sync/readme",
            headers=auth,
            timeout=10,
        )
        assert r.status_code in (404, 503)

    # ── GitHub not configured → 503 ───────────────────────────────────────────

    def test_preview_readme_returns_503_when_github_not_configured(self, auth):
        """Without GitHub App credentials, preview returns 503."""
        r = requests.get(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/preview/readme",
            headers=auth,
            timeout=10,
        )
        # Either 503 (no GitHub config) or 200 (credentials configured in env)
        assert r.status_code in (
            200,
            503,
        ), f"Unexpected status: {r.status_code} — {r.text}"
        if r.status_code == 503:
            assert "error" in r.json()

    def test_sync_readme_returns_503_or_200_when_github_not_configured(self, auth):
        r = requests.post(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/sync/readme",
            headers=auth,
            timeout=10,
        )
        assert r.status_code in (
            200,
            503,
        ), f"Unexpected status: {r.status_code} — {r.text}"

    # ── Response shape when GitHub IS configured ───────────────────────────────

    def test_preview_readme_response_shape_when_available(self, auth):
        r = requests.get(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/preview/readme",
            headers=auth,
            timeout=10,
        )
        if r.status_code == 503:
            pytest.skip("GitHub App not configured in this environment")
        assert r.status_code == 200
        data = r.json()
        assert "content" in data
        assert isinstance(data["content"], str)

    def test_preview_changelog_response_shape_when_available(self, auth):
        r = requests.get(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/preview/changelog",
            headers=auth,
            timeout=10,
        )
        if r.status_code == 503:
            pytest.skip("GitHub App not configured in this environment")
        assert r.status_code == 200
        data = r.json()
        assert "content" in data
        # changelog may be None if no CHANGELOG.md
        assert data["content"] is None or isinstance(data["content"], str)

    def test_preview_screenshots_response_shape_when_available(self, auth):
        r = requests.get(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/preview/screenshots",
            headers=auth,
            timeout=10,
        )
        if r.status_code == 503:
            pytest.skip("GitHub App not configured in this environment")
        assert r.status_code == 200
        data = r.json()
        assert "urls" in data
        assert isinstance(data["urls"], list)

    def test_sync_readme_response_shape_when_available(self, auth):
        r = requests.post(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/sync/readme",
            headers=auth,
            timeout=10,
        )
        if r.status_code == 503:
            pytest.skip("GitHub App not configured in this environment")
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert "sync" in data
        assert isinstance(data["sync"], dict)

    # ── Unauthenticated requests ───────────────────────────────────────────────

    def test_preview_requires_auth(self):
        r = requests.get(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/preview/readme",
            timeout=10,
        )
        assert r.status_code in (401, 403)

    def test_sync_field_requires_auth(self):
        r = requests.post(
            f"{BASE_URL}/admin/ghrm/packages/{self.pkg_id}/sync/readme",
            timeout=10,
        )
        assert r.status_code in (401, 403)
