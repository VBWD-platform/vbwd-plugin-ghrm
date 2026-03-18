"""GhrmSoftwarePackage model — software package tied to a tariff plan."""
from vbwd.extensions import db
from vbwd.models.base import BaseModel
import secrets


class GhrmSoftwarePackage(BaseModel):
    __tablename__ = "ghrm_software_package"

    tariff_plan_id = db.Column(
        db.UUID,
        db.ForeignKey("tarif_plan.id", ondelete="CASCADE"),
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

    __table_args__ = (
        db.UniqueConstraint(
            "github_owner", "github_repo", name="uq_ghrm_pkg_owner_repo"
        ),
    )

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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
