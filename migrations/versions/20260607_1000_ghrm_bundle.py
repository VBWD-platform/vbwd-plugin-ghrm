"""GHRM bundle packages — package_kind + bundle_repos + per-repo repo_grants (S59).

ADDITIVE schema change on the GHRM-owned tables:
  * ``ghrm_software_package.package_kind`` (``server_default='single'``)
  * ``ghrm_software_package.bundle_repos`` (``server_default='[]'``)
  * ``ghrm_repo_membership.repo_grants`` (``server_default='[]'``)
plus dropping the ``uq_ghrm_pkg_owner_repo`` unique constraint (D4 — a repo may
legitimately appear in more than one package once bundles exist), and a data
backfill that seeds each existing membership's ``repo_grants`` from its
package's representative repo + the row's status/invitation_id.

Existing packages backfill to ``package_kind='single'`` / ``bundle_repos=[]``
via the server_defaults. Downgrade re-adds the unique constraint (only viable
when no repo currently appears twice — acceptable for a reversible additive
migration) and drops the three columns.

Chains off the current ghrm head so the graph resolves whenever core + ghrm
are present.

Revision ID: 20260607_1000_ghrm_bundle
Revises: 20260605_1000_ghrm_pkg_perm
Create Date: 2026-06-07
"""
import json

from alembic import op
import sqlalchemy as sa


revision = "20260607_1000_ghrm_bundle"
down_revision = "20260605_1000_ghrm_pkg_perm"
branch_labels = None
depends_on = None

PKG_TABLE = "ghrm_software_package"
MEMBERSHIP_TABLE = "ghrm_repo_membership"
UNIQUE_CONSTRAINT = "uq_ghrm_pkg_owner_repo"


def _columns(table):
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _unique_constraints(table):
    bind = op.get_bind()
    return {uc["name"] for uc in sa.inspect(bind).get_unique_constraints(table)}


def upgrade():
    pkg_columns = _columns(PKG_TABLE)
    if "package_kind" not in pkg_columns:
        op.add_column(
            PKG_TABLE,
            sa.Column(
                "package_kind",
                sa.String(length=16),
                nullable=False,
                server_default="single",
            ),
        )
    if "bundle_repos" not in pkg_columns:
        op.add_column(
            PKG_TABLE,
            sa.Column(
                "bundle_repos",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
        )
    if "repo_grants" not in _columns(MEMBERSHIP_TABLE):
        op.add_column(
            MEMBERSHIP_TABLE,
            sa.Column(
                "repo_grants",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            ),
        )
    if UNIQUE_CONSTRAINT in _unique_constraints(PKG_TABLE):
        op.drop_constraint(UNIQUE_CONSTRAINT, PKG_TABLE, type_="unique")

    _backfill_repo_grants()


def _backfill_repo_grants():
    """Seed each existing membership's repo_grants from its package's repo."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT membership.id AS membership_id, package.github_owner AS owner, "
            "package.github_repo AS repo, membership.status AS status, "
            "membership.invitation_id AS invitation_id "
            "FROM ghrm_repo_membership AS membership "
            "JOIN ghrm_software_package AS package "
            "ON package.id = membership.package_id"
        )
    ).fetchall()
    for row in rows:
        repo_grants = [
            {
                "owner": row.owner,
                "repo": row.repo,
                "status": row.status,
                "invitation_id": row.invitation_id,
            }
        ]
        bind.execute(
            sa.text(
                "UPDATE ghrm_repo_membership SET repo_grants = :grants "
                "WHERE id = :membership_id"
            ),
            {"grants": json.dumps(repo_grants), "membership_id": row.membership_id},
        )


def downgrade():
    if UNIQUE_CONSTRAINT not in _unique_constraints(PKG_TABLE):
        op.create_unique_constraint(
            UNIQUE_CONSTRAINT, PKG_TABLE, ["github_owner", "github_repo"]
        )
    if "repo_grants" in _columns(MEMBERSHIP_TABLE):
        op.drop_column(MEMBERSHIP_TABLE, "repo_grants")
    pkg_columns = _columns(PKG_TABLE)
    if "bundle_repos" in pkg_columns:
        op.drop_column(PKG_TABLE, "bundle_repos")
    if "package_kind" in pkg_columns:
        op.drop_column(PKG_TABLE, "package_kind")
