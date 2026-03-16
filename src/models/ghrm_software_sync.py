"""GhrmSoftwareSync — cached GitHub data for a software package."""
from src.extensions import db
from src.models.base import BaseModel


class GhrmSoftwareSync(BaseModel):
    __tablename__ = "ghrm_software_sync"

    software_package_id = db.Column(
        db.UUID,
        db.ForeignKey("ghrm_software_package.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    latest_version = db.Column(db.String(64), nullable=True)
    latest_released_at = db.Column(db.DateTime, nullable=True)
    # Cached from GitHub
    cached_readme = db.Column(db.Text, nullable=True)
    cached_changelog = db.Column(db.Text, nullable=True)
    cached_docs = db.Column(db.Text, nullable=True)
    cached_releases = db.Column(
        db.JSON, nullable=True, default=list
    )  # [{tag, date, notes, assets:[{name,url}]}]
    cached_screenshots = db.Column(
        db.JSON, nullable=True, default=list
    )  # [{url, caption}]
    # Admin overrides (None = use cached)
    override_readme = db.Column(db.Text, nullable=True)
    override_changelog = db.Column(db.Text, nullable=True)
    override_docs = db.Column(db.Text, nullable=True)
    # Admin-uploaded screenshots (merged with cached on render)
    admin_screenshots = db.Column(db.JSON, nullable=True, default=list)
    last_synced_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "software_package_id": str(self.software_package_id),
            "latest_version": self.latest_version,
            "latest_released_at": self.latest_released_at.isoformat()
            if self.latest_released_at
            else None,
            "cached_readme": self.cached_readme,
            "cached_changelog": self.cached_changelog,
            "cached_docs": self.cached_docs,
            "cached_releases": self.cached_releases,
            "cached_screenshots": self.cached_screenshots,
            "override_readme": self.override_readme,
            "override_changelog": self.override_changelog,
            "override_docs": self.override_docs,
            "admin_screenshots": self.admin_screenshots,
            "last_synced_at": self.last_synced_at.isoformat()
            if self.last_synced_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
