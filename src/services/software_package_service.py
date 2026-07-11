"""SoftwarePackageService — catalogue listing, detail, sync, and install instructions."""
import logging
import secrets
from datetime import datetime
from vbwd.utils.datetime_utils import utcnow
from typing import List, Dict, Any, Optional
from plugins.ghrm.src.models.ghrm_software_package import (
    GhrmSoftwarePackage,
    ALLOWED_COLLABORATOR_PERMISSIONS,
    DEFAULT_COLLABORATOR_PERMISSION,
    ALLOWED_PACKAGE_KINDS,
    DEFAULT_PACKAGE_KIND,
    ALLOWED_ACCESS_KINDS,
    DEFAULT_ACCESS_KIND,
)
from plugins.ghrm.src.models.ghrm_software_sync import GhrmSoftwareSync
from plugins.ghrm.src.repositories.software_package_repository import (
    GhrmSoftwarePackageRepository,
)
from plugins.ghrm.src.repositories.software_sync_repository import (
    GhrmSoftwareSyncRepository,
)
from plugins.ghrm.src.services.github_app_client import IGithubAppClient


# Copy-slug construction (make-a-copy). The slug column is UNIQUE String(64), so
# a copy's slug is derived as ``<base>-copy`` (then ``-copy-2`` ...), truncating
# the base so the whole slug always fits within the column limit.
SLUG_MAX_LENGTH = 64
COPY_SLUG_SUFFIX = "-copy"


class GhrmPackageNotFoundError(Exception):
    """Raised when a software package cannot be found."""


class GhrmSyncAuthError(Exception):
    """Raised when sync API key is invalid."""


class GhrmNotConfiguredError(Exception):
    """Raised when the GitHub App client is absent (credentials not configured)."""


class GhrmSubscriptionRequiredError(Exception):
    """Raised when install instructions are requested without active subscription."""


class GhrmValidationError(Exception):
    """Raised when a package field fails validation (e.g. an unknown permission)."""


def validate_collaborator_permission(
    value: Optional[str], allow_extensive: bool
) -> str:
    """Validate and normalise a package's GitHub collaborator permission.

    Returns the least-privilege default when omitted (``None``); raises
    :class:`GhrmValidationError` for any value outside the allowed set. This is
    the single validation home reused by package create and update.

    Security guardrail (D3): when ``allow_extensive`` is ``False`` only
    ``DEFAULT_COLLABORATOR_PERMISSION`` ("pull", Read) is permitted — any
    write-and-above value (push/triage/maintain/admin) is rejected so no admin
    can grant write by mistake while the plugin flag is off.
    """
    if value is None:
        return DEFAULT_COLLABORATOR_PERMISSION
    if value not in ALLOWED_COLLABORATOR_PERMISSIONS:
        allowed = ", ".join(ALLOWED_COLLABORATOR_PERMISSIONS)
        raise GhrmValidationError(
            f"Invalid collaborator_permission '{value}'. Must be one of: {allowed}"
        )
    if not allow_extensive and value != DEFAULT_COLLABORATOR_PERMISSION:
        raise GhrmValidationError(
            f"Cannot set collaborator_permission '{value}': extensive GitHub "
            "permissions are disabled; only Read (pull) is allowed. Enable "
            "'allow_extensive_github_permissions' in the GHRM plugin settings "
            "to grant Write, Maintain or Admin access."
        )
    return value


def validate_package_kind(value: Optional[str]) -> str:
    """Validate and normalise a package's kind (S59).

    Returns the default ``"single"`` when omitted (``None``); raises
    :class:`GhrmValidationError` for anything outside
    :data:`ALLOWED_PACKAGE_KINDS`. Single validation home reused by create and
    update.
    """
    if value is None:
        return DEFAULT_PACKAGE_KIND
    if value not in ALLOWED_PACKAGE_KINDS:
        allowed = ", ".join(ALLOWED_PACKAGE_KINDS)
        raise GhrmValidationError(
            f"Invalid package_kind '{value}'. Must be one of: {allowed}"
        )
    return value


def validate_access_kind(value: Optional[str]) -> str:
    """Validate and normalise a package's access kind (S132).

    Returns the default ``"repo"`` when omitted (``None``); raises
    :class:`GhrmValidationError` for anything outside
    :data:`ALLOWED_ACCESS_KINDS`. Single validation home reused by admin create
    and update (``access_kind`` is an operator-level decision — never exposed on
    the vendor routes). Mirrors :func:`validate_package_kind`.
    """
    if value is None:
        return DEFAULT_ACCESS_KIND
    if value not in ALLOWED_ACCESS_KINDS:
        allowed = ", ".join(ALLOWED_ACCESS_KINDS)
        raise GhrmValidationError(
            f"Invalid access_kind '{value}'. Must be one of: {allowed}"
        )
    return value


def validate_team_fields(
    kind: str, github_org: Optional[str], github_team_slug: Optional[str]
) -> None:
    """Require a non-blank org + team slug when the effective kind is ``team``.

    Only meaningful for the ``team`` access kind: a team grant maps the package
    to ONE GitHub team, so both the org and the team slug must be present.
    Raises :class:`GhrmValidationError` when either is blank. A no-op for the
    ``repo`` kind (org/slug stay optional/nullable).
    """
    if kind != "team":
        return
    if not (github_org or "").strip() or not (github_team_slug or "").strip():
        raise GhrmValidationError(
            "A team access package requires a non-blank github_org and "
            "github_team_slug."
        )


def validate_bundle_repos(value: Any, *, kind: str) -> List[Dict[str, str]]:
    """Validate and normalise a package's curated bundle repo list (S59, D2).

    For ``kind == "single"`` the list is forced to ``[]`` regardless of input.
    For ``kind == "bundle"`` the list must be non-empty; each entry must carry a
    non-blank ``owner`` and ``repo`` (trimmed); duplicates are deduped while
    preserving first-seen order. Raises :class:`GhrmValidationError` otherwise.
    """
    if kind != "bundle":
        return []
    if not isinstance(value, list) or not value:
        raise GhrmValidationError(
            "A bundle package requires a non-empty bundle_repos list of "
            "{owner, repo} entries."
        )
    deduped: List[Dict[str, str]] = []
    seen: set = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise GhrmValidationError(
                "Each bundle_repos entry must be a {owner, repo} object."
            )
        owner = str(entry.get("owner", "")).strip()
        repo = str(entry.get("repo", "")).strip()
        if not owner or not repo:
            raise GhrmValidationError(
                "Each bundle_repos entry must have a non-blank owner and repo."
            )
        pair = (owner, repo)
        if pair in seen:
            continue
        seen.add(pair)
        deduped.append({"owner": owner, "repo": repo})
    return deduped


class SoftwarePackageService:
    """Manages software package catalogue and GitHub data sync."""

    def __init__(
        self,
        package_repo: GhrmSoftwarePackageRepository,
        sync_repo: GhrmSoftwareSyncRepository,
        github: Optional[IGithubAppClient],
    ) -> None:
        self._package_repo = package_repo
        self._sync_repo = sync_repo
        self._github = github

    def list_packages(
        self,
        page: int = 1,
        per_page: int = 20,
        category_slug: Optional[str] = None,
        query: Optional[str] = None,
        kind: Optional[str] = None,
        tag_slugs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """List active packages, optionally filtered by category, search, kind or tags.

        Each item carries a ``tags`` array, populated with ONE bulk tag call over
        the page's package ids (D6, no N+1) through the repository's tags port.
        """
        result = self._package_repo.find_all(
            page=page,
            per_page=per_page,
            category_slug=category_slug,
            query=query,
            kind=kind,
            tag_slugs=tag_slugs,
        )
        packages = result["items"]
        tags_by_id = self._package_repo.list_package_tags(
            [package.id for package in packages]
        )
        prices_by_plan = self._package_repo.list_package_prices(
            [package.tariff_plan_id for package in packages]
        )
        result["items"] = [
            self._package_dict_with_tags(package, tags_by_id, prices_by_plan)
            for package in packages
        ]
        return result

    def list_package_tag_options(
        self, category_slug: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Catalogue tag-filter options: tags used by at least one ACTIVE package.

        The filter must only offer tags that can actually filter something, so a
        globally-defined tag (e.g. a blog/news tag) that no active ghrm package
        carries is dropped. When ``category_slug`` is given the used-tag set is
        scoped to the ACTIVE packages IN that category (same category→package
        resolution the list path uses), so the filter mirrors what a
        category-filtered catalogue can actually show; absent/blank ⇒ all active
        packages (unchanged). The used-slug set comes from ONE bulk tag call over
        those packages (D6, no N+1); surviving catalog rows keep their display
        metadata and order. Degrades to ``[]`` when there are no active packages
        (including an unknown/empty category), none are tagged, or the tags port
        is unavailable — never a 500.
        """
        active_ids = self._package_repo.list_active_package_ids(category_slug)
        if not active_ids:
            return []
        tags_by_id = self._package_repo.list_package_tags(active_ids)
        used_slugs = {slug for slugs in tags_by_id.values() for slug in slugs}
        if not used_slugs:
            return []
        catalog = self._package_repo.list_applicable_package_tags()
        return [tag for tag in catalog if tag.get("slug") in used_slugs]

    @staticmethod
    def _package_dict_with_tags(
        package, tags_by_id, prices_by_plan=None
    ) -> Dict[str, Any]:
        data = package.to_dict()
        data["tags"] = tags_by_id.get(package.id, [])
        prices_by_plan = prices_by_plan or {}
        data["price"] = (
            prices_by_plan.get(str(package.tariff_plan_id))
            if package.tariff_plan_id
            else None
        )
        return data

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
            screenshots = list(sync.admin_screenshots or []) + list(
                sync.cached_screenshots or []
            )
            data["screenshots"] = screenshots
            data["latest_version"] = sync.latest_version
            data["latest_released_at"] = (
                sync.latest_released_at.isoformat() if sync.latest_released_at else None
            )
            data["last_synced_at"] = (
                sync.last_synced_at.isoformat() if sync.last_synced_at else None
            )
        else:
            data["readme"] = None
            data["changelog"] = None
            data["docs"] = None
            data["cached_releases"] = []
            data["screenshots"] = []
            data["latest_version"] = None
            data["latest_released_at"] = None
            data["last_synced_at"] = None
        prices_by_plan = self._package_repo.list_package_prices([pkg.tariff_plan_id])
        data["price"] = (
            prices_by_plan.get(str(pkg.tariff_plan_id)) if pkg.tariff_plan_id else None
        )
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

    def get_install_instructions(
        self, slug: str, user_id: str, deploy_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return install instructions for a subscriber. Raises if no active subscription."""
        pkg = self._package_repo.find_by_slug(slug)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{slug}' not found")
        if not deploy_token:
            raise GhrmSubscriptionRequiredError(
                "Active subscription and GitHub connection required"
            )
        token = deploy_token
        owner, repo, branch = (
            pkg.github_owner,
            pkg.github_repo,
            pkg.github_protected_branch,
        )
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
        screenshot_urls = self._github.fetch_screenshot_urls(
            pkg.github_owner, pkg.github_repo
        )

        sync = self._sync_repo.find_by_package_id(str(pkg.id))
        if not sync:
            sync = GhrmSoftwareSync(software_package_id=str(pkg.id))

        # Only overwrite cached fields — never touch admin overrides
        sync.cached_readme = readme
        sync.cached_changelog = changelog
        sync.cached_docs = docs
        sync.cached_releases = [
            {
                "tag": r.tag,
                "date": r.date,
                "notes": r.notes,
                "assets": [{"name": a.name, "url": a.url} for a in r.assets],
            }
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
            raise ValueError(
                f"Unknown field '{field}'. Must be one of: {', '.join(sorted(valid_fields))}"
            )
        if self._github is None:
            raise GhrmNotConfiguredError("GitHub App not configured — sync unavailable")
        pkg = self._package_repo.find_by_id(package_id)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{package_id}' not found")

        sync = self._sync_repo.find_by_package_id(package_id)
        if not sync:
            sync = GhrmSoftwareSync(software_package_id=package_id)

        if field == "readme":
            sync.cached_readme = self._github.fetch_readme(
                pkg.github_owner, pkg.github_repo
            )
        elif field == "changelog":
            sync.cached_changelog = self._github.fetch_changelog(
                pkg.github_owner, pkg.github_repo
            )
        elif field == "screenshots":
            urls = self._github.fetch_screenshot_urls(pkg.github_owner, pkg.github_repo)
            sync.cached_screenshots = [{"url": u, "caption": ""} for u in urls]

        sync.last_synced_at = utcnow()
        self._sync_repo.save(sync)
        return sync.to_dict()

    def get_by_tariff_plan_id(self, plan_id: str) -> Optional[GhrmSoftwarePackage]:
        return self._package_repo.find_by_tariff_plan_id(plan_id)

    def copy_package(self, package_id: str) -> Optional[GhrmSoftwarePackage]:
        """Create and persist an UNLINKED copy of a package. ``None`` if absent.

        The copy lands with ``tariff_plan_id=None`` (the plan link is UNIQUE 1:1,
        so a copy must not steal or duplicate the source's plan), is always
        inactive, mints a brand-new ``sync_api_key`` (the source's is a secret
        never reused), resets ``download_counter`` and gets a fresh unique slug.
        For a bundle the ``bundle_repos`` JSON list is copied verbatim — a
        bundle's members are embedded JSON, not child rows, so no member repos or
        packages are touched. Returns ``None`` for an unknown id so a bulk caller
        can skip it (Liskov: never a crash).
        """
        source = self._package_repo.find_by_id(package_id)
        if source is None:
            return None
        copy = GhrmSoftwarePackage(
            tariff_plan_id=None,
            name=f"{source.name} (Copy)",
            slug=self._unique_copy_slug(source.slug),
            author_name=source.author_name,
            icon_url=source.icon_url,
            github_owner=source.github_owner,
            github_repo=source.github_repo,
            description=source.description,
            github_protected_branch=source.github_protected_branch,
            github_installation_id=source.github_installation_id,
            sync_api_key=secrets.token_urlsafe(32),
            tech_specs=dict(source.tech_specs or {}),
            related_slugs=list(source.related_slugs or []),
            download_counter=0,
            is_active=False,
            sort_order=source.sort_order,
            collaborator_permission=source.collaborator_permission,
            package_kind=source.package_kind,
            bundle_repos=[dict(entry) for entry in (source.bundle_repos or [])],
        )
        self._package_repo.save(copy)
        return copy

    def _unique_copy_slug(self, base_slug: str) -> str:
        """Return a free ``<base>-copy`` slug (then ``-copy-2`` ...) within 64 chars.

        The base is truncated so ``base + suffix`` never exceeds
        :data:`SLUG_MAX_LENGTH`, and each candidate is checked against the repo
        until one is free.
        """
        attempt = 1
        while True:
            suffix = (
                COPY_SLUG_SUFFIX if attempt == 1 else f"{COPY_SLUG_SUFFIX}-{attempt}"
            )
            truncated_base = base_slug[: SLUG_MAX_LENGTH - len(suffix)]
            candidate = f"{truncated_base}{suffix}"
            if self._package_repo.find_by_slug(candidate) is None:
                return candidate
            attempt += 1

    def rotate_api_key(self, pkg_id: str) -> str:
        """Regenerate sync_api_key for a package. Returns new key."""
        pkg = self._package_repo.find_by_id(pkg_id)
        if not pkg:
            raise GhrmPackageNotFoundError(f"Package '{pkg_id}' not found")
        pkg.sync_api_key = secrets.token_urlsafe(32)
        self._package_repo.save(pkg)
        return pkg.sync_api_key
