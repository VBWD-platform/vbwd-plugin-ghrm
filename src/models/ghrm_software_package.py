"""GhrmSoftwarePackage model — software package tied to a tariff plan."""
from typing import Any, Iterable, List, Optional, Tuple

from vbwd.extensions import db
from vbwd.models.base import BaseModel
import secrets


# Single source of truth for the GitHub collaborator permission levels a
# package may grant. Stored as the raw GitHub permission string (extensible)
# and validated against this set. ``pull`` (read) is the least-privilege
# default. See GitHub's repository-collaborators permission model.
ALLOWED_COLLABORATOR_PERMISSIONS = ("pull", "triage", "push", "maintain", "admin")
DEFAULT_COLLABORATOR_PERMISSION = "pull"

# Single source of truth for the package discriminator (S59): a ``single`` repo
# (today's behaviour) or a ``bundle`` resolving to many curated repos. Reused by
# the validation helpers.
ALLOWED_PACKAGE_KINDS = ("single", "bundle")
DEFAULT_PACKAGE_KIND = "single"


def _dedupe(pairs: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Return the pairs with duplicates removed, preserving first-seen order."""
    seen: List[Tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.append(pair)
    return seen


def resolve_effective_permission(package: Any, allow_extensive: bool) -> str:
    """Return the GitHub permission a grant should actually use (D3 clamp).

    The single, pure home for the "what permission do we grant" decision so
    both the access service and any future caller agree (DRY). When extensive
    permissions are disabled the effective permission is always
    ``DEFAULT_COLLABORATOR_PERMISSION`` ("pull") regardless of the package's
    stored level — this defends against a write+ value persisted while the
    flag was on, then turned off. When enabled the package's configured level
    is honoured, falling back to the least-privilege default when unset.
    """
    if not allow_extensive:
        return DEFAULT_COLLABORATOR_PERMISSION
    stored: Optional[str] = getattr(package, "collaborator_permission", None)
    return stored or DEFAULT_COLLABORATOR_PERMISSION


class GhrmSoftwarePackage(BaseModel):
    __tablename__ = "ghrm_software_package"

    tariff_plan_id = db.Column(
        db.UUID,
        db.ForeignKey("subscription_tarif_plan.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(64), unique=True, nullable=False, index=True)
    author_name = db.Column(db.String(255), nullable=True)
    icon_url = db.Column(db.String(512), nullable=True)
    github_owner = db.Column(db.String(128), nullable=False)
    github_repo = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    github_protected_branch = db.Column(
        db.String(64), nullable=False, default="release"
    )
    github_installation_id = db.Column(db.String(64), nullable=True)
    sync_api_key = db.Column(
        db.String(128), nullable=False, default=lambda: secrets.token_urlsafe(32)
    )
    tech_specs = db.Column(db.JSON, nullable=True, default=dict)
    related_slugs = db.Column(db.JSON, nullable=True, default=list)
    download_counter = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    collaborator_permission = db.Column(
        db.String(16), nullable=False, default=DEFAULT_COLLABORATOR_PERMISSION
    )
    # S59: discriminator + curated repo list. ``github_owner/github_repo`` stays
    # the representative repo (detail/sync) in both modes; a bundle additionally
    # grants every repo in ``bundle_repos``. UNIQUE(owner, repo) is dropped (D4)
    # because a repo may legitimately appear in more than one package.
    package_kind = db.Column(
        db.String(16),
        nullable=False,
        default=DEFAULT_PACKAGE_KIND,
        server_default=DEFAULT_PACKAGE_KIND,
    )
    bundle_repos = db.Column(db.JSON, nullable=False, default=list, server_default="[]")

    def repo_targets(self) -> List[Tuple[str, str]]:
        """The ``(owner, repo)`` pairs a grant must cover (the only repo seam).

        Single -> the one representative repo; bundle -> the curated
        ``bundle_repos`` list, deduped and order-preserving. Grant/revoke loop
        this so single and bundle are the same code path (Open/Closed).
        """
        if self.package_kind == "bundle":
            return _dedupe(
                (entry["owner"], entry["repo"]) for entry in (self.bundle_repos or [])
            )
        return [(self.github_owner, self.github_repo)]

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tariff_plan_id": str(self.tariff_plan_id),
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "author_name": self.author_name,
            "icon_url": self.icon_url,
            "github_owner": self.github_owner,
            "github_repo": self.github_repo,
            "github_protected_branch": self.github_protected_branch,
            "github_installation_id": self.github_installation_id,
            "sync_api_key": self.sync_api_key,
            "tech_specs": self.tech_specs,
            "related_slugs": self.related_slugs,
            "download_counter": self.download_counter,
            "is_active": self.is_active,
            "sort_order": self.sort_order,
            "collaborator_permission": self.collaborator_permission,
            "package_kind": self.package_kind,
            "bundle_repos": self.bundle_repos,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
