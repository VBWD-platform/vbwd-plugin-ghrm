"""GhrmUserGithubAccess — verified GitHub OAuth identity, one row per user.

Identity/OAuth only. The per-(user, package) collaborator lifecycle
(INVITED/ACTIVE/GRACE/REVOKED/ERROR, grace expiry, invitation id) lives in
``ghrm_repo_membership`` (S49.1); deploy tokens were removed (S49.2/S49.3 —
access is collaborator-based, no per-user token). A user is "connected" iff an
identity row exists.
"""
from vbwd.extensions import db
from vbwd.models.base import BaseModel
from vbwd.utils.crypto import EncryptedString


class GhrmUserGithubAccess(BaseModel):
    __tablename__ = "ghrm_user_github_access"

    user_id = db.Column(
        db.UUID,
        db.ForeignKey("vbwd_user.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    github_username = db.Column(db.String(128), nullable=False)
    github_user_id = db.Column(db.String(32), nullable=False)
    # S05 — tokens are encrypted at rest via the EncryptedString TypeDecorator.
    # Stored column type stays Text; the cipher key resolves per-app from
    # VBWD_TOKEN_ENCRYPTION_KEY (required in prod).
    oauth_token = db.Column(EncryptedString(), nullable=True)
    oauth_scope = db.Column(db.String(256), nullable=True)

    @property
    def connected(self) -> bool:
        """A persisted identity row means the user has connected GitHub."""
        return self.github_username is not None

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "github_username": self.github_username,
            "github_user_id": self.github_user_id,
            "oauth_scope": self.oauth_scope,
            "connected": self.connected,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
