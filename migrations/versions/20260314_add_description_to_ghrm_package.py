"""add description to ghrm_software_package

Revision ID: ghrm_add_description
Revises: ghrm_initial
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

revision = 'ghrm_add_description'
down_revision = '20260311_ghrm_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ghrm_software_package', sa.Column('description', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('ghrm_software_package', 'description')
