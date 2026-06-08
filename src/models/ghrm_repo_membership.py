"""GhrmRepoMembership — per-(user, package) collaborator lifecycle state (S49.1).

Tracks a user's GitHub collaborator status on one software package's repo, so
a user who owns two packages can be ACTIVE on one and INVITED on another, and
so failures are recorded per repo. The identity/OAuth record
(``GhrmUserGithubAccess``) stays one-per-user; this table is one-per
(user, package).
"""
import enum

from vbwd.extensions import db
from vbwd.models.base import BaseModel

# Imported so the ``package`` relationship below can resolve the target class
# whenever this module is loaded in isolation (e.g. model-only unit tests).
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage

STATUS_COLUMN_LENGTH = 16


class MembershipStatus(str, enum.Enum):
    """Lifecycle states for a user's collaborator membership on a repo."""

    INVITED = "invited"
    ACTIVE = "active"
    GRACE = "grace"
    REVOKED = "revoked"
    ERROR = "error"


class GhrmRepoMembership(BaseModel):
    __tablename__ = "ghrm_repo_membership"

    user_id = db.Column(
        db.UUID,
        db.ForeignKey("vbwd_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    package_id = db.Column(
        db.UUID,
        db.ForeignKey("ghrm_software_package.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = db.Column(
        db.String(STATUS_COLUMN_LENGTH),
        nullable=False,
        default=MembershipStatus.INVITED.value,
        index=True,
    )
    invitation_id = db.Column(db.String(64), nullable=True)
    invited_at = db.Column(db.DateTime, nullable=True)
    grace_expires_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    # S59: per-repo invite/grant for a bundle package
    # ``[{"owner","repo","status","invitation_id"}]``. Row-level
    # ``status``/``invitation_id`` stay the representative-repo rollup.
    repo_grants = db.Column(db.JSON, nullable=False, default=list, server_default="[]")

    package = db.relationship(GhrmSoftwarePackage, lazy="joined")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "package_id", name="uq_ghrm_repo_membership_user_package"
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "package_id": str(self.package_id),
            "package_slug": self.package.slug if self.package else None,
            "package_name": self.package.name if self.package else None,
            "status": self.status,
            "invitation_id": self.invitation_id,
            "invited_at": self.invited_at.isoformat() if self.invited_at else None,
            "grace_expires_at": self.grace_expires_at.isoformat()
            if self.grace_expires_at
            else None,
            "last_error": self.last_error,
            "repo_grants": self.repo_grants or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
