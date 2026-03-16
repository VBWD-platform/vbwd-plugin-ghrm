"""Create GHRM plugin tables: ghrm_software_package, ghrm_software_sync, ghrm_user_github_access, ghrm_access_log.

Revision ID: 20260311_ghrm_tables
Revises: 20260308_cms_page_source_css
Create Date: 2026-03-11 10:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260311_ghrm_tables"
down_revision: Union[str, None] = "20260308_cms_page_source_css"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ghrm_software_package ─────────────────────────────────────────────────
    op.create_table(
        "ghrm_software_package",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "tariff_plan_id",
            sa.UUID(),
            sa.ForeignKey("tarif_plan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("icon_url", sa.String(512), nullable=True),
        sa.Column("github_owner", sa.String(128), nullable=False),
        sa.Column("github_repo", sa.String(128), nullable=False),
        sa.Column(
            "github_protected_branch",
            sa.String(64),
            nullable=False,
            server_default="release",
        ),
        sa.Column("github_installation_id", sa.String(64), nullable=True),
        sa.Column("sync_api_key", sa.String(128), nullable=False),
        sa.Column("tech_specs", sa.JSON(), nullable=True),
        sa.Column("related_slugs", sa.JSON(), nullable=True),
        sa.Column("download_counter", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tariff_plan_id", name="uq_ghrm_pkg_tariff_plan"),
        sa.UniqueConstraint(
            "github_owner", "github_repo", name="uq_ghrm_pkg_owner_repo"
        ),
    )
    op.create_index(
        "ix_ghrm_software_package_slug", "ghrm_software_package", ["slug"], unique=True
    )

    # ── ghrm_software_sync ────────────────────────────────────────────────────
    op.create_table(
        "ghrm_software_sync",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "software_package_id",
            sa.UUID(),
            sa.ForeignKey("ghrm_software_package.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("latest_version", sa.String(64), nullable=True),
        sa.Column("latest_released_at", sa.DateTime(), nullable=True),
        sa.Column("cached_readme", sa.Text(), nullable=True),
        sa.Column("cached_changelog", sa.Text(), nullable=True),
        sa.Column("cached_docs", sa.Text(), nullable=True),
        sa.Column("cached_releases", sa.JSON(), nullable=True),
        sa.Column("cached_screenshots", sa.JSON(), nullable=True),
        sa.Column("override_readme", sa.Text(), nullable=True),
        sa.Column("override_changelog", sa.Text(), nullable=True),
        sa.Column("override_docs", sa.Text(), nullable=True),
        sa.Column("admin_screenshots", sa.JSON(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("software_package_id", name="uq_ghrm_sync_package"),
    )
    op.create_index(
        "ix_ghrm_software_sync_package_id",
        "ghrm_software_sync",
        ["software_package_id"],
        unique=True,
    )

    # ── ghrm_user_github_access ───────────────────────────────────────────────
    op.create_table(
        "ghrm_user_github_access",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("github_username", sa.String(128), nullable=False),
        sa.Column("github_user_id", sa.String(32), nullable=False),
        sa.Column("oauth_token", sa.Text(), nullable=True),
        sa.Column("oauth_scope", sa.String(256), nullable=True),
        sa.Column("deploy_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "access_status", sa.String(32), nullable=False, server_default="active"
        ),
        sa.Column("grace_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_ghrm_access_user"),
    )
    op.create_index(
        "ix_ghrm_user_github_access_user_id",
        "ghrm_user_github_access",
        ["user_id"],
        unique=True,
    )

    # ── ghrm_access_log ───────────────────────────────────────────────────────
    op.create_table(
        "ghrm_access_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "package_id",
            sa.UUID(),
            sa.ForeignKey("ghrm_software_package.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("triggered_by", sa.String(64), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ghrm_access_log_user_id", "ghrm_access_log", ["user_id"], unique=False
    )
    op.create_index(
        "ix_ghrm_access_log_package_id", "ghrm_access_log", ["package_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("ghrm_access_log")
    op.drop_table("ghrm_user_github_access")
    op.drop_table("ghrm_software_sync")
    op.drop_table("ghrm_software_package")
