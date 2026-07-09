"""GHRM package plan link nullable — a copied package lands UNLINKED.

Product decision (make-a-copy bulk action): a copied ``GhrmSoftwarePackage``
must NOT steal or duplicate the source's 1:1 UNIQUE tariff plan link, so a copy
lands with ``tariff_plan_id = NULL``. This migration ONLY drops the ``NOT NULL``
constraint on ``ghrm_software_package.tariff_plan_id``; the column stays UNIQUE +
indexed + ``ON DELETE CASCADE``. A UNIQUE column in Postgres permits many NULLs,
so many unlinked copies coexist.

Chains off the current ghrm head so the graph resolves whenever core + ghrm are
present.

Revision ID: 20260709_1000_ghrm_plan_nullable
Revises: 20260607_1000_ghrm_bundle
Create Date: 2026-07-09
"""
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260709_1000_ghrm_plan_nullable"
down_revision = "20260607_1000_ghrm_bundle"
branch_labels = None
depends_on = None

TABLE = "ghrm_software_package"
COLUMN = "tariff_plan_id"


def upgrade():
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=postgresql.UUID(),
        nullable=True,
    )


def downgrade():
    # Reversible: re-adds NOT NULL (only viable when no unlinked copy exists —
    # acceptable for a reversible column-constraint migration).
    op.alter_column(
        TABLE,
        COLUMN,
        existing_type=postgresql.UUID(),
        nullable=False,
    )
