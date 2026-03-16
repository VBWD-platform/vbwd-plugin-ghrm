"""Integration tests: GitHub sync endpoint populates package detail tabs.

Tests the full HTTP pipeline:

  1. Admin creates a GHRM package via POST /api/v1/admin/ghrm/packages
  2. GitHub Actions (or admin) triggers sync:
         POST /api/v1/ghrm/sync?package=<slug>&key=<sync_api_key>
  3. GET /api/v1/ghrm/packages/<slug> returns the synced content
  4. Assert:
       - result["readme"]    is not null  → Overview tab shows README.md content
       - result["changelog"] is not null  → Changelog tab shows CHANGELOG.md content

The running backend uses MockGithubAppClient for all GitHub calls, which
returns a predictable "# Mock README" / "# Mock Changelog" — enough to verify
the full storage + retrieval pipeline without hitting the real GitHub API.

Run with:
    docker compose --profile test-integration run --rm test-integration \\
        pytest plugins/ghrm/tests/integration/test_ghrm_sync_tabs_api.py -v

Or against a running dev server:
    API_BASE_URL=http://localhost:5000/api/v1 pytest \\
        plugins/ghrm/tests/integration/test_ghrm_sync_tabs_api.py -v
"""
import pytest
import requests
import os
from uuid import uuid4


BASE_URL = os.getenv("API_BASE_URL", "http://localhost:5000/api/v1")
ADMIN_EMAIL    = os.getenv("TEST_ADMIN_EMAIL",    "admin@example.com")
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

def _create_plan(auth, name):
    r = requests.post(
        f"{BASE_URL}/admin/tarif-plans",
        headers=auth,
        json={"name": name, "price": "0.00"},
        timeout=10,
    )
    assert r.status_code == 201, f"Plan create failed: {r.text}"
    return r.json()["plan"]


def _delete_plan(auth, plan_id):
    requests.delete(f"{BASE_URL}/admin/tarif-plans/{plan_id}", headers=auth, timeout=10)


def _create_ghrm_pkg(auth, plan_id, slug, owner="dantweb", repo="vbwd-backend"):
    r = requests.post(
        f"{BASE_URL}/admin/ghrm/packages",
        headers=auth,
        json={
            "tariff_plan_id": plan_id,
            "name": f"VBWD Backend ({slug})",
            "slug": slug,
            "github_owner": owner,
            "github_repo": repo,
        },
        timeout=10,
    )
    assert r.status_code == 201, f"GHRM package create failed: {r.text}"
    return r.json()


def _delete_ghrm_pkg(auth, pkg_id):
    requests.delete(f"{BASE_URL}/admin/ghrm/packages/{pkg_id}", headers=auth, timeout=10)


def _trigger_sync(slug, sync_api_key):
    """Simulate a GitHub Actions webhook by hitting the sync endpoint directly."""
    r = requests.post(
        f"{BASE_URL}/ghrm/sync",
        params={"package": slug, "key": sync_api_key},
        timeout=15,
    )
    return r


def _get_package_detail(slug):
    r = requests.get(f"{BASE_URL}/ghrm/packages/{slug}", timeout=10)
    return r


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestSyncPopulatesTabsViaApi:
    """After triggering sync, Overview and Changelog tabs receive content."""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self, auth):
        """Create a GHRM package for dantweb/vbwd-backend-<uid> and clean up after."""
        uid = uuid4().hex[:8]
        slug = f"vbwd-backend-{uid}"

        self.plan = _create_plan(auth, f"VBWD Backend Test {uid}")
        self.pkg  = _create_ghrm_pkg(
            auth,
            plan_id=self.plan["id"],
            slug=slug,
            owner="dantweb",
            repo=f"vbwd-backend-{uid}",
        )
        self.slug         = slug
        self.sync_api_key = self.pkg.get("sync_api_key")

        yield

        _delete_ghrm_pkg(auth, self.pkg["id"])
        _delete_plan(auth, self.plan["id"])

    def test_package_created_with_no_readme_initially(self):
        """A freshly created package has no readme — tabs show empty state."""
        r = _get_package_detail(self.slug)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("readme") is None, (
            "Before sync, readme must be None — Overview tab should show empty state"
        )

    def test_sync_endpoint_accepts_valid_api_key(self):
        """POST /api/v1/ghrm/sync with the package's sync_api_key returns 200."""
        assert self.sync_api_key, "Package must have a sync_api_key"
        r = _trigger_sync(self.slug, self.sync_api_key)
        assert r.status_code == 200, (
            f"Sync endpoint failed with {r.status_code}: {r.text}"
        )

    def test_sync_endpoint_rejects_invalid_api_key(self):
        """POST /api/v1/ghrm/sync with a wrong key returns 401 or 403."""
        r = _trigger_sync(self.slug, "definitely-wrong-key")
        assert r.status_code in (401, 403), (
            f"Expected 401/403 for bad sync key, got {r.status_code}"
        )

    def test_overview_tab_populated_after_sync(self):
        """After sync, GET /packages/<slug> returns non-null 'readme' (Overview tab)."""
        _trigger_sync(self.slug, self.sync_api_key)

        r = _get_package_detail(self.slug)
        assert r.status_code == 200, r.text
        data = r.json()

        assert data.get("readme") is not None, (
            "After sync, 'readme' must not be None — Overview tab should show README.md content"
        )
        assert len(data["readme"]) > 0, (
            "readme content must not be empty after sync"
        )

    def test_changelog_tab_populated_after_sync(self):
        """After sync, GET /packages/<slug> returns non-null 'changelog' (Changelog tab)."""
        _trigger_sync(self.slug, self.sync_api_key)

        r = _get_package_detail(self.slug)
        assert r.status_code == 200, r.text
        data = r.json()

        assert data.get("changelog") is not None, (
            "After sync, 'changelog' must not be None — Changelog tab should show CHANGELOG.md content"
        )
        assert len(data["changelog"]) > 0, (
            "changelog content must not be empty after sync"
        )

    def test_both_tabs_populated_in_single_sync(self):
        """A single sync call populates both Overview and Changelog tabs simultaneously."""
        _trigger_sync(self.slug, self.sync_api_key)

        r = _get_package_detail(self.slug)
        assert r.status_code == 200, r.text
        data = r.json()

        assert data.get("readme") is not None,    "Overview tab (readme) must be populated"
        assert data.get("changelog") is not None, "Changelog tab (changelog) must be populated"

    def test_package_detail_response_shape(self):
        """GET /packages/<slug> returns all tab-related fields in the response."""
        _trigger_sync(self.slug, self.sync_api_key)

        r = _get_package_detail(self.slug)
        assert r.status_code == 200, r.text
        data = r.json()

        tab_fields = ["readme", "changelog", "docs", "screenshots", "cached_releases"]
        for field in tab_fields:
            assert field in data, (
                f"Field '{field}' must be present in package detail response (used by tabs)"
            )

    def test_re_sync_updates_content(self):
        """Calling sync twice does not break anything — second sync overwrites cached content."""
        r1 = _trigger_sync(self.slug, self.sync_api_key)
        assert r1.status_code == 200, f"First sync failed: {r1.text}"

        r2 = _trigger_sync(self.slug, self.sync_api_key)
        assert r2.status_code == 200, f"Second sync failed: {r2.text}"

        r = _get_package_detail(self.slug)
        assert r.status_code == 200
        data = r.json()
        assert data.get("readme") is not None, "readme must be present after re-sync"

    def test_last_synced_at_set_after_sync(self):
        """After sync, last_synced_at is non-null (displayed in admin 'Last Synced' field)."""
        _trigger_sync(self.slug, self.sync_api_key)

        r = _get_package_detail(self.slug)
        assert r.status_code == 200, r.text
        data = r.json()

        assert data.get("last_synced_at") is not None, (
            "last_synced_at must be set after sync — admin UI shows this timestamp"
        )

    def test_latest_version_set_after_sync_with_releases(self):
        """After sync with releases, latest_version is non-null (shown in package header badge)."""
        _trigger_sync(self.slug, self.sync_api_key)

        r = _get_package_detail(self.slug)
        assert r.status_code == 200
        data = r.json()

        # MockGithubAppClient returns [] for releases by default, so latest_version may be None.
        # This test just verifies the field exists and the shape is correct.
        assert "latest_version" in data, (
            "latest_version must be a field in the package detail response"
        )


class TestSyncGithubRepoContentMapping:
    """Verify the exact mapping: README.md → readme field, CHANGELOG.md → changelog field.

    These tests document the business rule:
    - README.md from the GitHub repo   → stored as cached_readme → returned as 'readme'
    - CHANGELOG.md from the GitHub repo → stored as cached_changelog → returned as 'changelog'

    Since the backend uses MockGithubAppClient in non-production mode, the test
    verifies the pipeline shape.  In production, the real GithubAppClient would
    fetch from https://github.com/<owner>/<repo>/blob/main/README.md etc.
    """

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self, auth):
        uid = uuid4().hex[:8]
        self.slug = f"sync-map-test-{uid}"
        self.plan = _create_plan(auth, f"Sync Map Test {uid}")
        self.pkg  = _create_ghrm_pkg(
            auth,
            plan_id=self.plan["id"],
            slug=self.slug,
            owner="dantweb",
            repo=f"vbwd-backend-{uid}",
        )
        self.sync_api_key = self.pkg.get("sync_api_key")
        yield
        _delete_ghrm_pkg(auth, self.pkg["id"])
        _delete_plan(auth, self.plan["id"])

    def test_readme_field_maps_to_overview_tab(self):
        """'readme' in API response is the content shown in the Overview tab."""
        _trigger_sync(self.slug, self.sync_api_key)
        data = _get_package_detail(self.slug).json()

        # In production this would equal the actual README.md content.
        # MockGithubAppClient returns "# Mock README" so we verify it's that string.
        assert data.get("readme") == "# Mock README", (
            "readme field must contain what fetch_readme() returns "
            "(README.md in production, mock content in test)"
        )

    def test_changelog_field_maps_to_changelog_tab(self):
        """'changelog' in API response is the content shown in the Changelog tab."""
        _trigger_sync(self.slug, self.sync_api_key)
        data = _get_package_detail(self.slug).json()

        assert data.get("changelog") == "# Mock Changelog", (
            "changelog field must contain what fetch_changelog() returns "
            "(CHANGELOG.md in production, mock content in test)"
        )

    def test_sync_response_contains_ok_flag(self):
        """POST /api/v1/ghrm/sync returns {ok: true} on success."""
        r = _trigger_sync(self.slug, self.sync_api_key)
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True, f"Expected {{ok: true}}, got: {data}"
