"""Create ghrm_repo_membership — per-(user, package) collaborator state (S49.1).

ADDITIVE ONLY. Creates the new ``ghrm_repo_membership`` table and does NOT
drop any column from ``ghrm_user_github_access``. The identity-model trim and
the ``deploy_token`` drop are deferred to the S49.3 wave (where the service is
rewritten atomically), so this migration stays purely additive to keep the
build green for parallel work.

Chains off ``vbwd_001`` (the monolithic core baseline that creates both
``vbwd_user`` and ``ghrm_software_package``, the two FK targets), so it
resolves standalone whenever core + ghrm are present.

Revision ID: 20260530_1000_ghrm_membership
Revises: vbwd_001
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260530_1000_ghrm_membership"
down_revision = "vbwd_001"
branch_labels = None
depends_on = None

TABLE_NAME = "ghrm_repo_membership"


def upgrade():
    op.create_table(
        TABLE_NAME,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("package_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("invitation_id", sa.String(length=64), nullable=True),
        sa.Column("invited_at", sa.DateTime(), nullable=True),
        sa.Column("grace_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["vbwd_user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["package_id"], ["ghrm_software_package.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "user_id", "package_id", name="uq_ghrm_repo_membership_user_package"
        ),
    )
    op.create_index("ix_ghrm_repo_membership_user_id", TABLE_NAME, ["user_id"])
    op.create_index("ix_ghrm_repo_membership_status", TABLE_NAME, ["status"])


def downgrade():
    op.drop_index("ix_ghrm_repo_membership_status", table_name=TABLE_NAME)
    op.drop_index("ix_ghrm_repo_membership_user_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
