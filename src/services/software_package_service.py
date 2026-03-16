"""SoftwarePackageService — catalogue listing, detail, sync, and install instructions."""
import logging
import secrets
from datetime import datetime
from src.utils.datetime_utils import utcnow
from typing import List, Dict, Any, Optional
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from plugins.ghrm.src.models.ghrm_software_sync import GhrmSoftwareSync
from plugins.ghrm.src.repositories.software_package_repository import GhrmSoftwarePackageRepository
from plugins.ghrm.src.repositories.software_sync_repository import GhrmSoftwareSyncRepository
from plugins.ghrm.src.services.github_app_client import IGithubAppClient


class GhrmPackageNotFoundError(Exception):
    """Raised when a software package cannot be found."""


class GhrmSyncAuthError(Exception):
    """Raised when sync API key is invalid."""


class GhrmNotConfiguredError(Exception):
    """Raised when the GitHub App client is absent (credentials not configured)."""


class GhrmSubscriptionRequiredError(Exception):
    """Raised when install instructions are requested without active subscription."""


class SoftwarePackageService:
    """Manages software package catalogue and GitHub data sync."""

    def __init__(
        self,
        package_repo: GhrmSoftwarePackageRepository,
        sync_repo: GhrmSoftwareSyncRepository,
        github: Optional[IGithubAppClient],
        software_category_slugs: Optional[List[str]] = None,
    ) -> None:
        self._package_repo = package_repo
        self._sync_repo = sync_repo
        self._github = github
        self._category_slugs = software_category_slugs or []

    def list_packages(self, page: int = 1, per_page: int = 20, category_slug: Optional[str] = None, query: Optional[str] = None) -> Dict[str, Any]:
        """List active packages, optionally filtered by category slug or search query."""
        result = self._package_repo.find_all(page=page, per_page=per_page, category_slug=category_slug, query=query)
        result["items"] = [p.to_dict() for p in result["items"]]
        return result

    def get_package(self, slug: str) -> Dict[str, Any]:
        """Get package detail with merged cached+override sync data."""
        pkg = self._package_repo.find_by_slug(slug)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{slug}' not found")
        self._package_repo.increment_downloads(slug)
        data = pkg.to_dict()
        sync = self._sync_repo.find_by_package_id(str(pkg.id))
        if sync:
            data["readme"] = sync.override_readme or sync.cached_readme
            data["changelog"] = sync.override_changelog or sync.cached_changelog
            data["docs"] = sync.override_docs or sync.cached_docs
            data["cached_releases"] = sync.cached_releases or []
            screenshots = list(sync.admin_screenshots or []) + list(sync.cached_screenshots or [])
            data["screenshots"] = screenshots
            data["latest_version"] = sync.latest_version
            data["latest_released_at"] = sync.latest_released_at.isoformat() if sync.latest_released_at else None
            data["last_synced_at"] = sync.last_synced_at.isoformat() if sync.last_synced_at else None
        else:
            data["readme"] = None
            data["changelog"] = None
            data["docs"] = None
            data["cached_releases"] = []
            data["screenshots"] = []
            data["latest_version"] = None
            data["latest_released_at"] = None
            data["last_synced_at"] = None
        return data

    def get_related(self, slug: str) -> List[Dict[str, Any]]:
        """Return manually curated related packages."""
        pkg = self._package_repo.find_by_slug(slug)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{slug}' not found")
        related_slugs = pkg.related_slugs or []
        if not related_slugs:
            return []
        packages = self._package_repo.find_by_slugs(related_slugs)
        return [p.to_dict() for p in packages]

    def get_versions(self, slug: str) -> List[Dict[str, Any]]:
        """Return version list from cached releases."""
        pkg = self._package_repo.find_by_slug(slug)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{slug}' not found")
        sync = self._sync_repo.find_by_package_id(str(pkg.id))
        if not sync or not sync.cached_releases:
            return []
        return sync.cached_releases

    def get_install_instructions(self, slug: str, user_id: str, deploy_token: Optional[str] = None) -> Dict[str, Any]:
        """Return install instructions for a subscriber. Raises if no active subscription."""
        pkg = self._package_repo.find_by_slug(slug)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{slug}' not found")
        if not deploy_token:
            raise GhrmSubscriptionRequiredError("Active subscription and GitHub connection required")
        token = deploy_token
        owner, repo, branch = pkg.github_owner, pkg.github_repo, pkg.github_protected_branch
        return {
            "package_slug": slug,
            "deploy_token": token,
            "npm": f"npm install git+https://{token}@github.com/{owner}/{repo}.git#{branch}",
            "composer": f"composer require {owner}/{repo}:dev-{branch} --prefer-source",
            "pip": f"pip install git+https://{token}@github.com/{owner}/{repo}.git@{branch}",
            "git": f"git clone -b {branch} https://{token}@github.com/{owner}/{repo}.git",
        }

    def sync_package(self, api_key: str) -> Dict[str, Any]:
        """Verify API key, pull data from GitHub, update sync record. Returns sync dict."""
        if self._github is None:
            raise GhrmNotConfiguredError("GitHub App not configured — sync unavailable")
        pkg = self._package_repo.find_by_sync_key(api_key)
        if not pkg:
            raise GhrmSyncAuthError("Invalid sync API key")

        readme = self._github.fetch_readme(pkg.github_owner, pkg.github_repo)
        changelog = self._github.fetch_changelog(pkg.github_owner, pkg.github_repo)
        docs = self._github.fetch_docs_readme(pkg.github_owner, pkg.github_repo)
        releases = self._github.fetch_releases(pkg.github_owner, pkg.github_repo)
        screenshot_urls = self._github.fetch_screenshot_urls(pkg.github_owner, pkg.github_repo)

        sync = self._sync_repo.find_by_package_id(str(pkg.id))
        if not sync:
            sync = GhrmSoftwareSync(software_package_id=str(pkg.id))

        # Only overwrite cached fields — never touch admin overrides
        sync.cached_readme = readme
        sync.cached_changelog = changelog
        sync.cached_docs = docs
        sync.cached_releases = [
            {"tag": r.tag, "date": r.date, "notes": r.notes, "assets": [{"name": a.name, "url": a.url} for a in r.assets]}
            for r in releases
        ]
        sync.cached_screenshots = [{"url": u, "caption": ""} for u in screenshot_urls]
        if releases:
            sync.latest_version = releases[0].tag
            try:
                sync.latest_released_at = datetime.fromisoformat(releases[0].date)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Could not parse release date '%s': %s", releases[0].date, exc
                )
        sync.last_synced_at = utcnow()
        self._sync_repo.save(sync)

        return sync.to_dict()

    def preview_readme(self, package_id: str) -> str:
        if self._github is None:
            raise GhrmNotConfiguredError("GitHub App not configured — sync unavailable")
        pkg = self._package_repo.find_by_id(package_id)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{package_id}' not found")
        return self._github.fetch_readme(pkg.github_owner, pkg.github_repo)

    def preview_changelog(self, package_id: str) -> Optional[str]:
        if self._github is None:
            raise GhrmNotConfiguredError("GitHub App not configured — sync unavailable")
        pkg = self._package_repo.find_by_id(package_id)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{package_id}' not found")
        return self._github.fetch_changelog(pkg.github_owner, pkg.github_repo)

    def preview_screenshots(self, package_id: str) -> List[str]:
        if self._github is None:
            raise GhrmNotConfiguredError("GitHub App not configured — sync unavailable")
        pkg = self._package_repo.find_by_id(package_id)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{package_id}' not found")
        return self._github.fetch_screenshot_urls(pkg.github_owner, pkg.github_repo)

    def sync_field(self, package_id: str, field: str) -> Dict[str, Any]:
        valid_fields = {"readme", "changelog", "screenshots"}
        if field not in valid_fields:
            raise ValueError(f"Unknown field '{field}'. Must be one of: {', '.join(sorted(valid_fields))}")
        if self._github is None:
            raise GhrmNotConfiguredError("GitHub App not configured — sync unavailable")
        pkg = self._package_repo.find_by_id(package_id)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{package_id}' not found")

        sync = self._sync_repo.find_by_package_id(package_id)
        if not sync:
            sync = GhrmSoftwareSync(software_package_id=package_id)

        if field == "readme":
            sync.cached_readme = self._github.fetch_readme(pkg.github_owner, pkg.github_repo)
        elif field == "changelog":
            sync.cached_changelog = self._github.fetch_changelog(pkg.github_owner, pkg.github_repo)
        elif field == "screenshots":
            urls = self._github.fetch_screenshot_urls(pkg.github_owner, pkg.github_repo)
            sync.cached_screenshots = [{"url": u, "caption": ""} for u in urls]

        sync.last_synced_at = utcnow()
        self._sync_repo.save(sync)
        return sync.to_dict()

    def get_by_tariff_plan_id(self, plan_id: str) -> Optional[GhrmSoftwarePackage]:
        return self._package_repo.find_by_tariff_plan_id(plan_id)

    def rotate_api_key(self, pkg_id: str) -> str:
        """Regenerate sync_api_key for a package. Returns new key."""
        pkg = self._package_repo.find_by_id(pkg_id)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{pkg_id}' not found")
        pkg.sync_api_key = secrets.token_urlsafe(32)
        self._package_repo.save(pkg)
        return pkg.sync_api_key
