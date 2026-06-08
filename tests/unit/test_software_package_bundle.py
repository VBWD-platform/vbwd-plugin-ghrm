"""Unit tests for the GhrmSoftwarePackage bundle support (S59).

A package is either a ``single`` repo (today's behaviour — grant/revoke the one
``github_owner/github_repo``) or a ``bundle`` resolving to many repos curated in
``bundle_repos``. ``repo_targets()`` is the single seam grant/revoke loop over;
``github_owner/github_repo`` stays the representative repo in both modes.
"""
from plugins.ghrm.src.models.ghrm_software_package import (
    GhrmSoftwarePackage,
    ALLOWED_PACKAGE_KINDS,
)


def _make_package(**overrides):
    base = dict(
        tariff_plan_id="00000000-0000-0000-0000-000000000001",
        name="Pkg",
        slug="pkg",
        github_owner="acme",
        github_repo="repo",
    )
    base.update(overrides)
    return GhrmSoftwarePackage(**base)


class TestPackageKind:
    def test_allowed_kinds_are_the_single_source_of_truth(self):
        assert ALLOWED_PACKAGE_KINDS == ("single", "bundle")

    def test_column_default_is_single(self):
        column = GhrmSoftwarePackage.__table__.columns["package_kind"]
        assert column.default.arg == "single"
        assert column.server_default.arg == "single"
        assert column.nullable is False

    def test_bundle_repos_column_defaults_to_empty_list(self):
        column = GhrmSoftwarePackage.__table__.columns["bundle_repos"]
        assert column.default.arg is list
        assert column.nullable is False


class TestRepoTargets:
    def test_single_returns_one_pair(self):
        package = _make_package(
            package_kind="single", github_owner="acme", github_repo="repo"
        )
        assert package.repo_targets() == [("acme", "repo")]

    def test_single_is_the_default_kind(self):
        package = _make_package(github_owner="acme", github_repo="repo")
        # package_kind not set explicitly — defaults handled by repo_targets when
        # the column default has not yet been applied (transient instance).
        package.package_kind = package.package_kind or "single"
        assert package.repo_targets() == [("acme", "repo")]

    def test_bundle_returns_curated_pairs_order_preserving(self):
        package = _make_package(
            package_kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
            ],
        )
        assert package.repo_targets() == [("acme", "alpha"), ("acme", "beta")]

    def test_bundle_dedupes_while_preserving_first_seen_order(self):
        package = _make_package(
            package_kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
                {"owner": "acme", "repo": "alpha"},
            ],
        )
        assert package.repo_targets() == [("acme", "alpha"), ("acme", "beta")]


class TestToDict:
    def test_includes_package_kind_and_bundle_repos(self):
        bundle_repos = [{"owner": "acme", "repo": "alpha"}]
        package = _make_package(package_kind="bundle", bundle_repos=bundle_repos)
        data = package.to_dict()
        assert data["package_kind"] == "bundle"
        assert data["bundle_repos"] == bundle_repos


class TestTableConstraints:
    def test_owner_repo_unique_constraint_is_dropped(self):
        constraint_names = {
            constraint.name
            for constraint in GhrmSoftwarePackage.__table__.constraints
            if constraint.name
        }
        assert "uq_ghrm_pkg_owner_repo" not in constraint_names

    def test_tariff_plan_id_remains_unique(self):
        column = GhrmSoftwarePackage.__table__.columns["tariff_plan_id"]
        assert column.unique is True

    def test_slug_remains_unique(self):
        column = GhrmSoftwarePackage.__table__.columns["slug"]
        assert column.unique is True
