"""Add per-package GitHub collaborator permission level to ghrm_software_package.

The granted GitHub permission is configurable per package (GHRM's per-plan
entity) so different tariff plans can grant different access levels (S51).
ADDITIVE: the column is added with ``server_default='pull'`` so existing rows
backfill to the least-privilege read level; downgrade drops it.

Chains off the current ghrm head so the graph resolves whenever core + ghrm
are present.

Revision ID: 20260605_1000_ghrm_pkg_perm
Revises: 20260604_1000_ghrm_drop_token
Create Date: 2026-06-05
"""
from alembic import op
import sqlalchemy as sa


revision = "20260605_1000_ghrm_pkg_perm"
down_revision = "20260604_1000_ghrm_drop_token"
branch_labels = None
depends_on = None

TABLE_NAME = "ghrm_software_package"
COLUMN_NAME = "collaborator_permission"
DEFAULT_PERMISSION = "pull"


def _existing_columns():
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE_NAME)}


def upgrade():
    if COLUMN_NAME not in _existing_columns():
        op.add_column(
            TABLE_NAME,
            sa.Column(
                COLUMN_NAME,
                sa.String(length=16),
                nullable=False,
                server_default=DEFAULT_PERMISSION,
            ),
        )


def downgrade():
    if COLUMN_NAME in _existing_columns():
        op.drop_column(TABLE_NAME, COLUMN_NAME)
