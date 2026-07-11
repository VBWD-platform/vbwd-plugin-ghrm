"""Unit tests for the GhrmSoftwarePackage access-target seam (S132).

A package grants access one of two ways (this pass): ``repo`` (today's per-repo
collaborator behaviour, the default) or ``team`` (one GitHub team). The
``access_targets()`` method generalises the existing ``repo_targets()`` seam
into a list of polymorphic ``AccessTarget`` value objects so grant/revoke is a
single Open/Closed code path that never branches on kind. ``repo_targets()``
stays verbatim (back-compat).
"""
from plugins.ghrm.src.models.ghrm_software_package import (
    GhrmSoftwarePackage,
    ALLOWED_ACCESS_KINDS,
    DEFAULT_ACCESS_KIND,
)
from plugins.ghrm.src.services.access_target import RepoTarget, TeamTarget


def _make_package(**overrides):
    base = dict(
        name="Pkg",
        slug="pkg",
        github_owner="acme",
        github_repo="repo",
    )
    base.update(overrides)
    return GhrmSoftwarePackage(**base)


class TestAccessKindConstants:
    def test_allowed_kinds_are_repo_and_team(self):
        assert ALLOWED_ACCESS_KINDS == ("repo", "team")

    def test_default_kind_is_repo(self):
        assert DEFAULT_ACCESS_KIND == "repo"


class TestAccessKindColumn:
    def test_access_kind_column_default_is_repo(self):
        column = GhrmSoftwarePackage.__table__.columns["access_kind"]
        assert column.default.arg == "repo"
        assert str(column.server_default.arg) == "repo"
        assert column.nullable is False

    def test_org_and_team_slug_columns_are_nullable(self):
        columns = GhrmSoftwarePackage.__table__.columns
        assert columns["github_org"].nullable is True
        assert columns["github_team_slug"].nullable is True


class TestAccessTargets:
    def test_unset_kind_defaults_to_repo_targets(self):
        package = _make_package()  # access_kind not set (transient -> None)
        assert package.access_targets() == [RepoTarget("acme", "repo")]

    def test_repo_kind_matches_repo_targets(self):
        package = _make_package(access_kind="repo")
        assert package.access_targets() == [RepoTarget("acme", "repo")]
        assert [
            (target.owner, target.repo) for target in package.access_targets()
        ] == package.repo_targets()

    def test_bundle_repo_kind_yields_one_repo_target_per_bundle_repo(self):
        package = _make_package(
            access_kind="repo",
            package_kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
            ],
        )
        assert package.access_targets() == [
            RepoTarget("acme", "alpha"),
            RepoTarget("acme", "beta"),
        ]

    def test_team_kind_yields_one_team_target(self):
        package = _make_package(
            access_kind="team",
            github_org="acme-inc",
            github_team_slug="developers",
        )
        assert package.access_targets() == [TeamTarget("acme-inc", "developers")]


class TestToDict:
    def test_includes_access_kind_and_team_columns(self):
        package = _make_package(
            access_kind="team",
            github_org="acme-inc",
            github_team_slug="developers",
        )
        result = package.to_dict()
        assert result["access_kind"] == "team"
        assert result["github_org"] == "acme-inc"
        assert result["github_team_slug"] == "developers"
