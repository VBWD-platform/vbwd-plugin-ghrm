"""GHRM access-kind — access_kind + github_org + github_team_slug (S132).

ADDITIVE schema change on the GHRM-owned ``ghrm_software_package`` table only:
  * ``access_kind`` (``server_default='repo'``, NOT NULL) — how a package grants
    access. ``repo`` (default) keeps today's per-repo collaborator behaviour;
    ``team`` maps the package to one GitHub team.
  * ``github_org`` (nullable) — the org a ``team`` package lives under.
  * ``github_team_slug`` (nullable) — the team a ``team`` package maps to.

Existing packages backfill to ``access_kind='repo'`` via the server_default, so
behaviour is unchanged until an operator flips a package to ``team``. The
membership table is untouched — the ``repo_grants`` JSON column is reused (each
entry gains a ``"kind"`` key, read backward-compatibly as ``repo`` when absent).

Chains off the current ghrm head so the graph resolves whenever core + ghrm are
present. Reversible downgrade.

Revision ID: 20260711_1000_ghrm_access_kind
Revises: 20260709_1000_ghrm_plan_nullable
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa


revision = "20260711_1000_ghrm_access_kind"
down_revision = "20260709_1000_ghrm_plan_nullable"
branch_labels = None
depends_on = None

PKG_TABLE = "ghrm_software_package"


def _columns(table):
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade():
    pkg_columns = _columns(PKG_TABLE)
    if "access_kind" not in pkg_columns:
        op.add_column(
            PKG_TABLE,
            sa.Column(
                "access_kind",
                sa.String(length=16),
                nullable=False,
                server_default="repo",
            ),
        )
    if "github_org" not in pkg_columns:
        op.add_column(
            PKG_TABLE,
            sa.Column("github_org", sa.String(length=128), nullable=True),
        )
    if "github_team_slug" not in pkg_columns:
        op.add_column(
            PKG_TABLE,
            sa.Column("github_team_slug", sa.String(length=128), nullable=True),
        )


def downgrade():
    pkg_columns = _columns(PKG_TABLE)
    if "github_team_slug" in pkg_columns:
        op.drop_column(PKG_TABLE, "github_team_slug")
    if "github_org" in pkg_columns:
        op.drop_column(PKG_TABLE, "github_org")
    if "access_kind" in pkg_columns:
        op.drop_column(PKG_TABLE, "access_kind")
