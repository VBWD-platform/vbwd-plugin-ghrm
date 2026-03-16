"""GhrmAccessLog — audit log for collaborator lifecycle events."""
from src.extensions import db
from src.models.base import BaseModel


class SyncAction:
    ADD_COLLABORATOR = "add_collaborator"
    REMOVE_COLLABORATOR = "remove_collaborator"
    GRACE_STARTED = "grace_started"
    TOKEN_ROTATED = "token_rotated"


class GhrmAccessLog(BaseModel):
    __tablename__ = "ghrm_access_log"

    user_id = db.Column(
        db.UUID,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    package_id = db.Column(
        db.UUID,
        db.ForeignKey("ghrm_software_package.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action = db.Column(db.String(32), nullable=False)
    triggered_by = db.Column(
        db.String(64), nullable=False
    )  # subscription_event | manual | scheduler | sync
    subscription_id = db.Column(db.UUID, nullable=True)
    meta = db.Column(db.JSON, nullable=True, default=dict)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "package_id": str(self.package_id) if self.package_id else None,
            "action": self.action,
            "triggered_by": self.triggered_by,
            "subscription_id": str(self.subscription_id)
            if self.subscription_id
            else None,
            "meta": self.meta,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
