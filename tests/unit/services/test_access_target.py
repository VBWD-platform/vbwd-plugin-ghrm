"""Unit tests for the AccessTarget value objects (S132).

``RepoTarget`` wraps today's collaborator calls verbatim; ``TeamTarget`` wraps
the new team-membership calls. Each knows how to ``grant`` / ``revoke`` /
``is_active`` so the service loops targets without branching on kind (OCP).
GitHub team membership takes a team ROLE, not a repo permission, so
``TeamTarget.grant`` ignores the ``permission`` argument.
"""
import pytest

from plugins.ghrm.src.services.access_target import (
    GrantResult,
    RepoTarget,
    TeamTarget,
    access_targets_for_package,
    target_from_grant,
)
from plugins.ghrm.src.services.github_app_client import MockGithubAppClient
from plugins.ghrm.src.services.github_app_client_real import GithubAppClientError


class TestRepoTarget:
    def test_grant_invites_and_returns_invited(self):
        client = MockGithubAppClient()
        result = RepoTarget("acme", "alpha").grant(client, "octocat", "pull")
        assert isinstance(result, GrantResult)
        assert result.state == "invited"
        assert result.invitation_id is not None
        assert "octocat" in client.collaborators.get(("acme", "alpha"), set())

    def test_grant_already_member_returns_active(self):
        client = MockGithubAppClient()
        client.members_already.add(("acme", "alpha", "octocat"))
        result = RepoTarget("acme", "alpha").grant(client, "octocat", "pull")
        assert result.state == "active"
        assert result.invitation_id is None

    def test_grant_passes_permission_through(self):
        client = MockGithubAppClient()
        captured = []
        original = client.add_collaborator

        def spy(owner, repo, username, permission="pull"):
            captured.append(permission)
            return original(owner, repo, username, permission)

        client.add_collaborator = spy
        RepoTarget("acme", "alpha").grant(client, "octocat", "push")
        assert captured == ["push"]

    def test_revoke_invited_cancels_invitation(self):
        client = MockGithubAppClient()
        client.invitations[("acme", "alpha")] = [{"id": 42}]
        RepoTarget("acme", "alpha").revoke(client, "octocat", "invited", "42")
        assert client.invitations[("acme", "alpha")] == []

    def test_revoke_active_removes_collaborator(self):
        client = MockGithubAppClient()
        client.collaborators[("acme", "alpha")] = {"octocat"}
        RepoTarget("acme", "alpha").revoke(client, "octocat", "active", None)
        assert "octocat" not in client.collaborators[("acme", "alpha")]

    def test_is_active_reflects_collaborator_acceptance(self):
        client = MockGithubAppClient()
        target = RepoTarget("acme", "alpha")
        assert target.is_active(client, "octocat") is False
        client.accepted.add(("acme", "alpha", "octocat"))
        assert target.is_active(client, "octocat") is True

    def test_descriptor_and_key(self):
        target = RepoTarget("acme", "alpha")
        assert target.descriptor() == {
            "kind": "repo",
            "owner": "acme",
            "repo": "alpha",
        }
        assert target.key() == ("repo", "acme", "alpha")


class TestTeamTarget:
    def test_grant_pending_when_user_not_org_member(self):
        client = MockGithubAppClient()
        result = TeamTarget("acme-inc", "developers").grant(client, "octocat", "pull")
        assert isinstance(result, GrantResult)
        assert result.state == "invited"
        assert result.invitation_id is None

    def test_grant_active_when_user_already_org_member(self):
        client = MockGithubAppClient()
        client.org_members.setdefault("acme-inc", set()).add("octocat")
        result = TeamTarget("acme-inc", "developers").grant(client, "octocat", "pull")
        assert result.state == "active"

    def test_grant_ignores_repo_permission(self):
        client = MockGithubAppClient()
        captured = []
        original = client.add_team_membership

        def spy(org, team_slug, username):
            captured.append((org, team_slug, username))
            return original(org, team_slug, username)

        client.add_team_membership = spy
        # A repo permission is irrelevant for a team grant — it must not appear
        # in the call and must not raise.
        TeamTarget("acme-inc", "developers").grant(client, "octocat", "admin")
        assert captured == [("acme-inc", "developers", "octocat")]

    def test_revoke_removes_team_membership(self):
        client = MockGithubAppClient()
        client.add_team_membership("acme-inc", "developers", "octocat")
        TeamTarget("acme-inc", "developers").revoke(client, "octocat", "active", None)
        assert client.get_team_membership("acme-inc", "developers", "octocat") is None

    def test_is_active_only_when_membership_active(self):
        client = MockGithubAppClient()
        target = TeamTarget("acme-inc", "developers")
        client.add_team_membership("acme-inc", "developers", "octocat")
        assert target.is_active(client, "octocat") is False  # pending
        client.org_members.setdefault("acme-inc", set()).add("octocat")
        assert target.is_active(client, "octocat") is True

    def test_descriptor_and_key(self):
        target = TeamTarget("acme-inc", "developers")
        assert target.descriptor() == {
            "kind": "team",
            "org": "acme-inc",
            "team_slug": "developers",
        }
        assert target.key() == ("team", "acme-inc", "developers")


class _StubPackage:
    def __init__(self, access_kind, **kwargs):
        self.access_kind = access_kind
        self.github_owner = kwargs.get("github_owner", "acme")
        self.github_repo = kwargs.get("github_repo", "repo")
        self.package_kind = kwargs.get("package_kind", "single")
        self.bundle_repos = kwargs.get("bundle_repos", [])
        self.github_org = kwargs.get("github_org")
        self.github_team_slug = kwargs.get("github_team_slug")

    def repo_targets(self):
        if self.package_kind == "bundle":
            return [(entry["owner"], entry["repo"]) for entry in self.bundle_repos]
        return [(self.github_owner, self.github_repo)]


class TestAccessTargetsForPackage:
    def test_repo_kind_returns_repo_targets(self):
        package = _StubPackage("repo", github_owner="acme", github_repo="solo")
        assert access_targets_for_package(package) == [RepoTarget("acme", "solo")]

    def test_missing_access_kind_defaults_to_repo(self):
        package = _StubPackage(None, github_owner="acme", github_repo="solo")
        assert access_targets_for_package(package) == [RepoTarget("acme", "solo")]

    def test_team_kind_returns_team_target(self):
        package = _StubPackage(
            "team", github_org="acme-inc", github_team_slug="developers"
        )
        assert access_targets_for_package(package) == [
            TeamTarget("acme-inc", "developers")
        ]


class TestTargetFromGrant:
    def test_repo_entry_round_trips(self):
        entry = {"kind": "repo", "owner": "acme", "repo": "alpha"}
        assert target_from_grant(entry) == RepoTarget("acme", "alpha")

    def test_legacy_entry_without_kind_is_repo(self):
        entry = {"owner": "acme", "repo": "alpha"}
        assert target_from_grant(entry) == RepoTarget("acme", "alpha")

    def test_team_entry_round_trips(self):
        entry = {"kind": "team", "org": "acme-inc", "team_slug": "developers"}
        assert target_from_grant(entry) == TeamTarget("acme-inc", "developers")


class TestFailurePropagation:
    def test_repo_grant_failure_raises_client_error(self):
        client = MockGithubAppClient()
        client.raise_on_add_collaborator = GithubAppClientError("403")
        with pytest.raises(GithubAppClientError):
            RepoTarget("acme", "alpha").grant(client, "octocat", "pull")

    def test_team_grant_failure_raises_client_error(self):
        client = MockGithubAppClient()
        client.raise_on_add_team_membership = GithubAppClientError("403")
        with pytest.raises(GithubAppClientError):
            TeamTarget("acme-inc", "developers").grant(client, "octocat", "pull")
