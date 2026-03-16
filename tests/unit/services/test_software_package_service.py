"""Unit tests for SoftwarePackageService."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

from plugins.ghrm.src.services.software_package_service import (
    SoftwarePackageService,
    GhrmPackageNotFoundError,
    GhrmSyncAuthError,
    GhrmNotConfiguredError,
    GhrmSubscriptionRequiredError,
)
from plugins.ghrm.src.services.github_app_client import MockGithubAppClient, ReleaseDTO, ReleaseAsset


def _make_service(package_repo=None, sync_repo=None, github=None):
    """Build SoftwarePackageService with mock dependencies."""
    return SoftwarePackageService(
        package_repo=package_repo or MagicMock(),
        sync_repo=sync_repo or MagicMock(),
        github=github or MockGithubAppClient(),
        software_category_slugs=["backend", "fe-user"],
    )


def _make_package(pkg_id="pkg-1", slug="my-pkg", owner="acme", repo="my-repo", branch="release", related_slugs=None):
    """Build a mock GhrmSoftwarePackage-like object."""
    pkg = MagicMock()
    pkg.id = pkg_id
    pkg.slug = slug
    pkg.github_owner = owner
    pkg.github_repo = repo
    pkg.github_protected_branch = branch
    pkg.related_slugs = related_slugs or []
    pkg.sync_api_key = "valid-key"
    pkg.is_active = True
    pkg.to_dict.return_value = {
        "id": pkg_id,
        "slug": slug,
        "github_owner": owner,
        "github_repo": repo,
        "github_protected_branch": branch,
    }
    return pkg


def _make_sync(pkg_id="pkg-1", cached_readme="# Cached", override_readme=None, cached_changelog="# Changelog", cached_releases=None, admin_screenshots=None, cached_screenshots=None):
    """Build a mock GhrmSoftwareSync-like object."""
    sync = MagicMock()
    sync.software_package_id = pkg_id
    sync.cached_readme = cached_readme
    sync.override_readme = override_readme
    sync.cached_changelog = cached_changelog
    sync.override_changelog = None
    sync.cached_docs = None
    sync.override_docs = None
    sync.cached_releases = cached_releases or []
    sync.admin_screenshots = admin_screenshots or []
    sync.cached_screenshots = cached_screenshots or []
    sync.latest_version = "1.0.0"
    sync.latest_released_at = None
    sync.last_synced_at = None
    sync.to_dict.return_value = {"software_package_id": pkg_id}
    return sync


class TestListPackages:
    def test_filters_by_category_slug(self):
        """list_packages passes category_slug to find_all."""
        package_repo = MagicMock()
        package_repo.find_all.return_value = {"items": [], "total": 0, "page": 1, "per_page": 20, "pages": 1}

        svc = _make_service(package_repo=package_repo)
        svc.list_packages(category_slug="backend")

        package_repo.find_all.assert_called_once_with(
            page=1, per_page=20, category_slug="backend", query=None
        )


class TestGetPackage:
    def test_merges_override_readme_over_cached(self):
        """When override_readme is set, get_package returns it instead of cached_readme."""
        pkg = _make_package()
        sync = _make_sync(cached_readme="# Cached README", override_readme="# Admin Override")

        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = sync

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo)
        result = svc.get_package("my-pkg")

        assert result["readme"] == "# Admin Override"

    def test_uses_cached_readme_when_no_override(self):
        """When override_readme is None, get_package returns cached_readme."""
        pkg = _make_package()
        sync = _make_sync(cached_readme="# Cached README", override_readme=None)

        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = sync

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo)
        result = svc.get_package("my-pkg")

        assert result["readme"] == "# Cached README"

    def test_increments_download_counter(self):
        """get_package calls increment_downloads on the repository."""
        pkg = _make_package()

        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo)
        svc.get_package("my-pkg")

        package_repo.increment_downloads.assert_called_once_with("my-pkg")

    def test_raises_when_not_found(self):
        """get_package raises GhrmPackageNotFoundError for unknown slug."""
        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = None

        svc = _make_service(package_repo=package_repo)

        with pytest.raises(GhrmPackageNotFoundError):
            svc.get_package("unknown-pkg")


class TestGetInstallInstructions:
    def test_requires_active_subscription(self):
        """get_install_instructions raises GhrmSubscriptionRequiredError with no deploy_token."""
        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        svc = _make_service(package_repo=package_repo)

        with pytest.raises(GhrmSubscriptionRequiredError):
            svc.get_install_instructions("my-pkg", "user-1", deploy_token=None)

    def test_returns_install_commands_with_token(self):
        """get_install_instructions returns dict with npm/pip/git/composer commands."""
        pkg = _make_package(owner="acme", repo="my-repo", branch="release")
        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        svc = _make_service(package_repo=package_repo)
        result = svc.get_install_instructions("my-pkg", "user-1", deploy_token="my-token")

        assert result["deploy_token"] == "my-token"
        assert "my-token@github.com/acme/my-repo" in result["npm"]
        assert "my-token@github.com/acme/my-repo" in result["pip"]
        assert "my-token@github.com/acme/my-repo" in result["git"]


class TestGetRelated:
    def test_returns_curated_list(self):
        """get_related returns packages matching pkg.related_slugs."""
        pkg = _make_package(related_slugs=["other-pkg"])
        other_pkg = _make_package(pkg_id="pkg-2", slug="other-pkg")

        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg
        package_repo.find_by_slugs.return_value = [other_pkg]

        svc = _make_service(package_repo=package_repo)
        result = svc.get_related("my-pkg")

        package_repo.find_by_slugs.assert_called_once_with(["other-pkg"])
        assert len(result) == 1

    def test_returns_empty_when_no_related_slugs(self):
        """get_related returns [] when pkg.related_slugs is empty."""
        pkg = _make_package(related_slugs=[])
        package_repo = MagicMock()
        package_repo.find_by_slug.return_value = pkg

        svc = _make_service(package_repo=package_repo)
        result = svc.get_related("my-pkg")

        assert result == []
        package_repo.find_by_slugs.assert_not_called()


class TestSyncPackage:
    def test_verifies_api_key(self):
        """sync_package raises GhrmSyncAuthError for invalid API key."""
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = None

        svc = _make_service(package_repo=package_repo)

        with pytest.raises(GhrmSyncAuthError):
            svc.sync_package("bad-key")

    def test_pulls_and_stores_github_data(self):
        """sync_package fetches readme/changelog/releases and saves sync record."""
        github = MockGithubAppClient()
        github.readme_content = "# Fresh README"
        github.changelog_content = "# Fresh Changelog"
        github.releases = [
            ReleaseDTO(tag="v2.0.0", date="2026-01-01T00:00:00", notes="New release", assets=[
                ReleaseAsset(name="dist.zip", url="https://example.com/dist.zip")
            ])
        ]

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        saved_sync = None

        def capture_save(s):
            nonlocal saved_sync
            saved_sync = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = capture_save

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo, github=github)
        svc.sync_package("valid-key")

        assert saved_sync is not None
        assert saved_sync.cached_readme == "# Fresh README"
        assert saved_sync.cached_changelog == "# Fresh Changelog"
        assert saved_sync.latest_version == "v2.0.0"
        assert len(saved_sync.cached_releases) == 1
        assert saved_sync.cached_releases[0]["tag"] == "v2.0.0"
        assert saved_sync.cached_releases[0]["assets"][0]["name"] == "dist.zip"

    def test_raises_not_configured_error_when_github_not_configured(self):
        """sync_package raises GhrmNotConfiguredError when github is None."""
        package_repo = MagicMock()
        svc = SoftwarePackageService(
            package_repo=package_repo,
            sync_repo=MagicMock(),
            github=None,
        )
        with pytest.raises(GhrmNotConfiguredError):
            svc.sync_package("any-key")

    def test_does_not_overwrite_admin_overrides(self):
        """sync_package preserves existing override_readme and override_changelog."""
        github = MockGithubAppClient()
        github.readme_content = "# GitHub README"

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_sync_key.return_value = pkg

        existing_sync = _make_sync(
            cached_readme="# Old Cached",
            override_readme="# Admin Override — keep me",
        )
        existing_sync.override_readme = "# Admin Override — keep me"
        existing_sync.override_changelog = "# Admin Changelog Override"

        saved_sync = None

        def capture_save(s):
            nonlocal saved_sync
            saved_sync = s
            s.to_dict.return_value = {}
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = existing_sync
        sync_repo.save.side_effect = capture_save

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo, github=github)
        svc.sync_package("valid-key")

        # cached_readme was updated to github content
        assert saved_sync.cached_readme == "# GitHub README"
        # override_readme was NOT touched by sync
        assert saved_sync.override_readme == "# Admin Override — keep me"
        assert saved_sync.override_changelog == "# Admin Changelog Override"


class TestPreviewReadme:
    def test_returns_readme_from_github(self):
        github = MockGithubAppClient()
        github.readme_content = "# Live README"

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(package_repo=package_repo, github=github)
        result = svc.preview_readme("pkg-1")

        assert result == "# Live README"

    def test_raises_not_found_when_package_missing(self):
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = None

        svc = _make_service(package_repo=package_repo)

        with pytest.raises(GhrmPackageNotFoundError):
            svc.preview_readme("missing-id")

    def test_raises_sync_auth_error_when_github_not_configured(self):
        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = SoftwarePackageService(
            package_repo=package_repo,
            sync_repo=MagicMock(),
            github=None,
        )

        with pytest.raises(GhrmNotConfiguredError):
            svc.preview_readme("pkg-1")


class TestPreviewChangelog:
    def test_returns_changelog_from_github(self):
        github = MockGithubAppClient()
        github.changelog_content = "## Changes"

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(package_repo=package_repo, github=github)
        result = svc.preview_changelog("pkg-1")

        assert result == "## Changes"

    def test_returns_none_when_changelog_absent(self):
        github = MockGithubAppClient()
        github.changelog_content = None

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(package_repo=package_repo, github=github)
        result = svc.preview_changelog("pkg-1")

        assert result is None


class TestPreviewScreenshots:
    def test_returns_url_list(self):
        github = MockGithubAppClient()
        github.screenshot_urls = ["https://example.com/a.png", "https://example.com/b.png"]

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(package_repo=package_repo, github=github)
        result = svc.preview_screenshots("pkg-1")

        assert result == ["https://example.com/a.png", "https://example.com/b.png"]


class TestSyncField:
    def _make_captured_sync_svc(self, github, pkg, existing_sync=None):
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        captured = {}

        def capture_save(s):
            captured["sync"] = s
            s.to_dict.return_value = {"software_package_id": pkg.id}
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = existing_sync
        sync_repo.save.side_effect = capture_save

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo, github=github)
        return svc, captured

    def test_syncs_readme(self):
        github = MockGithubAppClient()
        github.readme_content = "# New README"

        pkg = _make_package()
        existing_sync = _make_sync()
        svc, captured = self._make_captured_sync_svc(github, pkg, existing_sync)

        svc.sync_field("pkg-1", "readme")

        assert captured["sync"].cached_readme == "# New README"

    def test_syncs_changelog(self):
        github = MockGithubAppClient()
        github.changelog_content = "## v1.1"

        pkg = _make_package()
        existing_sync = _make_sync()
        svc, captured = self._make_captured_sync_svc(github, pkg, existing_sync)

        svc.sync_field("pkg-1", "changelog")

        assert captured["sync"].cached_changelog == "## v1.1"

    def test_syncs_screenshots(self):
        github = MockGithubAppClient()
        github.screenshot_urls = ["https://example.com/s1.png"]

        pkg = _make_package()
        existing_sync = _make_sync()
        svc, captured = self._make_captured_sync_svc(github, pkg, existing_sync)

        svc.sync_field("pkg-1", "screenshots")

        assert captured["sync"].cached_screenshots == [{"url": "https://example.com/s1.png", "caption": ""}]

    def test_raises_value_error_for_unknown_field(self):
        pkg = _make_package()
        svc, _ = self._make_captured_sync_svc(MockGithubAppClient(), pkg)

        with pytest.raises(ValueError):
            svc.sync_field("pkg-1", "unknown_field")

    def test_creates_sync_record_when_none_exists(self):
        github = MockGithubAppClient()
        github.readme_content = "# Fresh"

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        captured = {}

        def capture_save(s):
            captured["sync"] = s
            return s

        sync_repo = MagicMock()
        sync_repo.find_by_package_id.return_value = None
        sync_repo.save.side_effect = capture_save

        svc = _make_service(package_repo=package_repo, sync_repo=sync_repo, github=github)
        svc.sync_field("pkg-1", "readme")

        assert captured["sync"] is not None
        assert captured["sync"].cached_readme == "# Fresh"
