"""Drop deploy-token + per-user collaborator-state columns from ghrm_user_github_access.

The identity model is trimmed to identity/OAuth only (S49.3 wave). Deploy
tokens are removed for good (access is collaborator-based, no per-user token);
the per-user lifecycle columns move to ``ghrm_repo_membership`` (S49.1).

Chains off the membership migration so the graph resolves whenever core + ghrm
are present. Downgrade re-adds the columns (nullable / with a server default)
so the migration is reversible on an empty or populated table.

Revision ID: 20260604_1000_ghrm_drop_token
Revises: 20260530_1000_ghrm_membership
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa


revision = "20260604_1000_ghrm_drop_token"
down_revision = "20260530_1000_ghrm_membership"
branch_labels = None
depends_on = None

TABLE_NAME = "ghrm_user_github_access"

DROPPED_COLUMNS = (
    "deploy_token",
    "token_expires_at",
    "access_status",
    "grace_expires_at",
    "last_synced_at",
)


def _existing_columns():
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE_NAME)}


def upgrade():
    existing = _existing_columns()
    for column_name in DROPPED_COLUMNS:
        if column_name in existing:
            op.drop_column(TABLE_NAME, column_name)


def downgrade():
    existing = _existing_columns()

    def _add(column):
        if column.name not in existing:
            op.add_column(TABLE_NAME, column)

    _add(sa.Column("deploy_token", sa.Text(), nullable=True))
    _add(sa.Column("token_expires_at", sa.DateTime(), nullable=True))
    _add(
        sa.Column(
            "access_status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        )
    )
    _add(sa.Column("grace_expires_at", sa.DateTime(), nullable=True))
    _add(sa.Column("last_synced_at", sa.DateTime(), nullable=True))
