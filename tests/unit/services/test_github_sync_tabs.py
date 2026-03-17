"""Unit tests: GitHub sync populates package detail tabs correctly.

Verifies the full pipeline:
  sync_package(api_key)          ← GitHub fetches README.md + CHANGELOG.md
      │
      ▼
  GhrmSoftwareSync.cached_readme   / cached_changelog   stored
      │
      ▼
  get_package(slug)
      │
      ▼
  result["readme"]  → Overview tab content
  result["changelog"] → Changelog tab content

These tests use the in-process service layer (no HTTP).
The MockGithubAppClient simulates GitHub; its `readme_content` / `changelog_content`
attributes represent what would come from README.md / CHANGELOG.md on GitHub.
"""
from unittest.mock import MagicMock

from plugins.ghrm.src.services.software_package_service import (
    SoftwarePackageService,
)
from plugins.ghrm.src.services.github_app_client import (
    MockGithubAppClient,
    ReleaseDTO,
    ReleaseAsset,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_package(
    pkg_id="pkg-1",
    slug="vbwd-backend",
    owner="dantweb",
    repo="vbwd-backend",
    sync_api_key="test-sync-key",
):
    pkg = MagicMock()
    pkg.id = pkg_id
    pkg.slug = slug
    pkg.github_owner = owner
    pkg.github_repo = repo
    pkg.github_protected_branch = "release"
    pkg.related_slugs = []
    pkg.sync_api_key = sync_api_key
    pkg.is_active = True
    pkg.to_dict.return_value = {
        "id": pkg_id,
        "slug": slug,
        "github_owner": owner,
        "github_repo": repo,
        "github_protected_branch": "release",
    }
    return pkg


def _make_service(package_repo=None, sync_repo=None, github=None):
    return SoftwarePackageService(
        package_repo=package_repo or MagicMock(),
        sync_repo=sync_repo or MagicMock(),
        github=github or MockGithubAppClient(),
        software_category_slugs=["backend", "fe-user", "fe-admin"],
    )


# ── Suite ──────────────────────────────────────────────────────────────────────


class TestSyncPopulatesOverviewTab:
    """Overview tab shows README.md content fetched from GitHub."""

    def test_sync_stores_readme_as_cached_readme(self):
        """After sync, GhrmSoftwareSync.cached_readme equals the README.md content."""
        readme_content = "# vbwd-backend\n\nFlask API for the vbwd SaaS platform.\n"

        github = MockGithubAppClient()
        github.readme_content = readme_content

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        captured_sync = {}

        def capture_save(s):
            captured_sync["obj"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = capture_save

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        assert "obj" in captured_sync, "sync_repo.save was not called"
        assert captured_sync["obj"].cached_readme == readme_content

    def test_get_package_returns_readme_as_readme_field(self):
        """get_package exposes cached_readme under the 'readme' key (Overview tab content)."""
        readme_content = "# vbwd-backend\n\nPython backend.\n"

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        sync = MagicMock()
        sync.cached_readme = readme_content
        sync.override_readme = None  # no admin override → use cached
        sync.cached_changelog = None
        sync.override_changelog = None
        sync.cached_docs = None
        sync.override_docs = None
        sync.cached_releases = []
        sync.admin_screenshots = []
        sync.cached_screenshots = []
        sync.latest_version = None
        sync.latest_released_at = None
        sync.last_synced_at = None

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = sync

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo)
        result = svc.get_package("vbwd-backend")

        assert (
            result["readme"] == readme_content
        ), "Overview tab content should equal README.md from GitHub"

    def test_full_sync_then_get_pipeline_readme(self):
        """End-to-end: sync writes readme, then get_package reads it back."""
        readme = "# vbwd-backend\n\nThe main backend repository.\n\n## Installation\n\n```bash\nmake up\n```\n"

        github = MockGithubAppClient()
        github.readme_content = readme
        github.changelog_content = None  # no changelog for this test

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg
        package_repo.find_by_slug.return_value = pkg

        # Sync creates a new GhrmSoftwareSync; we capture the actual object
        # so we can feed it back into get_package.
        # Capture the GhrmSoftwareSync object written by sync_package
        saved = {}

        def capture_save(s):
            saved["sync"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None  # first call: no existing sync
        sync_repo.save.side_effect = capture_save

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        # Feed the saved sync object back into get_package
        sync_repo.find_by_package_id.return_value = saved["sync"]
        result = svc.get_package("vbwd-backend")

        assert (
            result["readme"] == readme
        ), "README.md content must appear in Overview tab"
        assert result["changelog"] is None  # no changelog file in this test


class TestSyncPopulatesChangelogTab:
    """Changelog tab shows CHANGELOG.md content fetched from GitHub."""

    def test_sync_stores_changelog_as_cached_changelog(self):
        """After sync, GhrmSoftwareSync.cached_changelog equals the CHANGELOG.md content."""
        changelog_content = (
            "# Changelog\n\n"
            "## [1.2.0] - 2026-01-15\n"
            "### Added\n"
            "- New subscription lifecycle hooks\n\n"
            "## [1.1.0] - 2025-12-01\n"
            "### Fixed\n"
            "- JWT refresh race condition\n"
        )

        github = MockGithubAppClient()
        github.changelog_content = changelog_content

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        captured_sync = {}

        def capture_save(s):
            captured_sync["obj"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = capture_save

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        assert "obj" in captured_sync
        assert captured_sync["obj"].cached_changelog == changelog_content

    def test_get_package_returns_changelog_as_changelog_field(self):
        """get_package exposes cached_changelog under 'changelog' key (Changelog tab content)."""
        changelog_content = (
            "# Changelog\n\n## [1.0.0] - 2026-01-01\n- Initial release\n"
        )

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        sync = MagicMock()
        sync.cached_readme = None
        sync.override_readme = None
        sync.cached_changelog = changelog_content
        sync.override_changelog = None  # no admin override → use cached
        sync.cached_docs = None
        sync.override_docs = None
        sync.cached_releases = []
        sync.admin_screenshots = []
        sync.cached_screenshots = []
        sync.latest_version = None
        sync.latest_released_at = None
        sync.last_synced_at = None

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = sync

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo)
        result = svc.get_package("vbwd-backend")

        assert (
            result["changelog"] == changelog_content
        ), "Changelog tab content should equal CHANGELOG.md from GitHub"

    def test_no_changelog_file_returns_none(self):
        """If GitHub repo has no CHANGELOG.md, changelog field is None (tab shows empty state)."""
        github = MockGithubAppClient()
        github.changelog_content = None  # simulates missing CHANGELOG.md

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        captured = {}

        def capture_save(s):
            captured["obj"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = capture_save

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        assert captured["obj"].cached_changelog is None


class TestSyncPopulatesBothTabsTogether:
    """Both README.md and CHANGELOG.md are synced in a single sync_package() call."""

    def test_readme_and_changelog_stored_in_single_sync(self):
        """A single sync call fetches and stores both README.md and CHANGELOG.md."""
        readme = "# vbwd-backend\n\nVBWD SaaS backend built with Flask.\n"
        changelog = "# Changelog\n\n## [2.0.0]\n- Token system rewrite\n"

        github = MockGithubAppClient()
        github.readme_content = readme
        github.changelog_content = changelog

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        captured = {}

        def capture_save(s):
            captured["obj"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = capture_save

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        assert captured["obj"].cached_readme == readme
        assert captured["obj"].cached_changelog == changelog

    def test_both_tabs_available_after_sync(self):
        """After sync, both 'readme' and 'changelog' keys present in get_package result."""
        readme = "# vbwd-backend\n\nBackend."
        changelog = "# Changelog\n\n## [1.0.0]\n- Initial."

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        sync = MagicMock()
        sync.cached_readme = readme
        sync.override_readme = None
        sync.cached_changelog = changelog
        sync.override_changelog = None
        sync.cached_docs = None
        sync.override_docs = None
        sync.cached_releases = []
        sync.admin_screenshots = []
        sync.cached_screenshots = []
        sync.latest_version = "v1.0.0"
        sync.latest_released_at = None
        sync.last_synced_at = None

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = sync

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo)
        result = svc.get_package("vbwd-backend")

        assert result["readme"] == readme, "Overview tab must show README.md"
        assert result["changelog"] == changelog, "Changelog tab must show CHANGELOG.md"

    def test_github_fetch_called_with_correct_owner_and_repo(self):
        """sync_package passes the package's github_owner + github_repo to the GitHub client."""
        github = MockGithubAppClient()

        pkg = _make_package(owner="dantweb", repo="vbwd-backend")
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = lambda s: s.__setattr__("to_dict", lambda: {}) or s

        # Spy on github methods
        original_fetch_readme = github.fetch_readme
        original_fetch_changelog = github.fetch_changelog
        calls_readme = []
        calls_changelog = []

        def spy_readme(owner, repo):
            calls_readme.append((owner, repo))
            return original_fetch_readme(owner, repo)

        def spy_changelog(owner, repo):
            calls_changelog.append((owner, repo))
            return original_fetch_changelog(owner, repo)

        github.fetch_readme = spy_readme
        github.fetch_changelog = spy_changelog

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        assert calls_readme == [
            ("dantweb", "vbwd-backend")
        ], "fetch_readme must be called with the package's github_owner and github_repo"
        assert calls_changelog == [
            ("dantweb", "vbwd-backend")
        ], "fetch_changelog must be called with the package's github_owner and github_repo"


class TestAdminOverrideTakesPrecedenceOverGitHub:
    """Admin override_readme / override_changelog take precedence over GitHub-fetched content.

    Sync updates cached_* fields only.  If an admin has set override_*, those
    values are never touched by sync and always win in get_package().
    """

    def test_overview_uses_admin_override_not_github_readme(self):
        """When override_readme is set, Overview tab shows admin content, not README.md."""
        github_readme = "# vbwd-backend (GitHub version)"
        admin_readme = "# vbwd-backend (Admin custom overview)"

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        sync = MagicMock()
        sync.cached_readme = github_readme
        sync.override_readme = admin_readme  # ← admin override
        sync.cached_changelog = None
        sync.override_changelog = None
        sync.cached_docs = None
        sync.override_docs = None
        sync.cached_releases = []
        sync.admin_screenshots = []
        sync.cached_screenshots = []
        sync.latest_version = None
        sync.latest_released_at = None
        sync.last_synced_at = None

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = sync

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo)
        result = svc.get_package("vbwd-backend")

        assert (
            result["readme"] == admin_readme
        ), "Admin override_readme must take precedence over GitHub cached_readme"
        assert result["readme"] != github_readme

    def test_sync_updates_cached_but_not_override(self):
        """sync_package updates cached_readme but never touches override_readme."""
        new_github_readme = "# vbwd-backend NEW version from GitHub"
        existing_override = "# Custom admin overview — do not overwrite"

        github = MockGithubAppClient()
        github.readme_content = new_github_readme

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        # Existing sync record with admin override set
        existing_sync = MagicMock()
        existing_sync.cached_readme = "# Old GitHub version"
        existing_sync.override_readme = existing_override
        existing_sync.override_changelog = None
        existing_sync.cached_changelog = None
        existing_sync.cached_docs = None
        existing_sync.override_docs = None
        existing_sync.cached_releases = []
        existing_sync.admin_screenshots = []
        existing_sync.cached_screenshots = []
        existing_sync.latest_version = None

        saved = {}

        def capture_save(s):
            saved["obj"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = existing_sync
        sync_repo.save.side_effect = capture_save

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        # cached_readme was updated by sync
        assert saved["obj"].cached_readme == new_github_readme
        # override_readme was NOT touched by sync
        assert saved["obj"].override_readme == existing_override


class TestVersionsTabFromReleases:
    """Versions tab is populated from GitHub releases fetched during sync."""

    def test_sync_stores_releases_in_cached_releases(self):
        """sync_package stores release tags, dates, notes, and assets."""
        releases = [
            ReleaseDTO(
                tag="v2.1.0",
                date="2026-03-01T10:00:00",
                notes="## What's Changed\n- Added plugin bundles\n- Token refund flow",
                assets=[
                    ReleaseAsset(
                        name="vbwd-backend-v2.1.0.tar.gz",
                        url="https://github.com/dantweb/vbwd-backend/releases/download/v2.1.0/vbwd-backend-v2.1.0.tar.gz",  # noqa: E501
                    ),
                ],
            ),
            ReleaseDTO(
                tag="v2.0.0",
                date="2026-01-15T08:00:00",
                notes="## What's Changed\n- Token system rewrite",
                assets=[],
            ),
        ]

        github = MockGithubAppClient()
        github.releases = releases

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        captured = {}

        def capture_save(s):
            captured["obj"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = capture_save

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        stored = captured["obj"].cached_releases
        assert len(stored) == 2
        assert stored[0]["tag"] == "v2.1.0"
        assert stored[0]["date"] == "2026-03-01T10:00:00"
        assert stored[1]["tag"] == "v2.0.0"

        # Assets serialised correctly
        assert stored[0]["assets"][0]["name"] == "vbwd-backend-v2.1.0.tar.gz"
        assert "vbwd-backend-v2.1.0.tar.gz" in stored[0]["assets"][0]["url"]

    def test_latest_version_updated_from_first_release(self):
        """sync_package sets latest_version to the tag of the most-recent release."""
        github = MockGithubAppClient()
        github.releases = [
            ReleaseDTO(
                tag="v3.0.0", date="2026-03-10T00:00:00", notes="Latest", assets=[]
            ),
            ReleaseDTO(
                tag="v2.0.0", date="2026-01-01T00:00:00", notes="Older", assets=[]
            ),
        ]

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        captured = {}

        def capture_save(s):
            captured["obj"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = capture_save

        svc = _make_service(
            package_repo=package_repo, sync_repo=sync_repo, github=github
        )
        svc.sync_package("test-sync-key")

        assert captured["obj"].latest_version == "v3.0.0"
