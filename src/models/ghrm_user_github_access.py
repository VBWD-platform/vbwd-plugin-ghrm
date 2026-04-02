"""GhrmUserGithubAccess — stores verified OAuth identity and deploy token per user."""
from vbwd.extensions import db
from vbwd.models.base import BaseModel


class AccessStatus:
    ACTIVE = "active"
    GRACE = "grace"
    REVOKED = "revoked"


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
    oauth_token = db.Column(db.Text, nullable=True)  # encrypted
    oauth_scope = db.Column(db.String(256), nullable=True)
    deploy_token = db.Column(db.Text, nullable=True)  # encrypted
    token_expires_at = db.Column(db.DateTime, nullable=True)
    access_status = db.Column(
        db.String(32), nullable=False, default=AccessStatus.ACTIVE
    )
    grace_expires_at = db.Column(db.DateTime, nullable=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "github_username": self.github_username,
            "github_user_id": self.github_user_id,
            "oauth_scope": self.oauth_scope,
            "access_status": self.access_status,
            "grace_expires_at": self.grace_expires_at.isoformat()
            if self.grace_expires_at
            else None,
            "last_synced_at": self.last_synced_at.isoformat()
            if self.last_synced_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
