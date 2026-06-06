"""Mock-parity (Liskov) tests for MockGithubAppClient (S49.2).

The mock must honour the same signatures + semantics as the real client:
invitation-aware ``add_collaborator``, acceptance-aware ``is_collaborator``,
and in-memory invitation listing/cancellation. Same exception type
(``GithubAppClientError``) on failure.
"""
import pytest

from plugins.ghrm.src.services.github_app_client import (
    AddCollaboratorResult,
    MockGithubAppClient,
)
from plugins.ghrm.src.services.github_app_client_real import GithubAppClientError


class TestMockAddCollaborator:
    def test_default_returns_invited_with_synthetic_id(self):
        client = MockGithubAppClient()
        result = client.add_collaborator("acme", "widget", "octocat", "pull")
        assert isinstance(result, AddCollaboratorResult)
        assert result.state == "invited"
        assert result.invitation_id is not None

    def test_members_already_returns_active(self):
        client = MockGithubAppClient()
        client.members_already.add(("acme", "widget", "octocat"))
        result = client.add_collaborator("acme", "widget", "octocat", "pull")
        assert result.state == "active"
        assert result.invitation_id is None

    def test_raise_on_add_collaborator_raises_client_error(self):
        client = MockGithubAppClient()
        client.raise_on_add_collaborator = GithubAppClientError("403 forbidden")
        with pytest.raises(GithubAppClientError):
            client.add_collaborator("acme", "widget", "octocat", "pull")


class TestMockIsCollaborator:
    def test_false_until_accepted_then_true(self):
        client = MockGithubAppClient()
        client.add_collaborator("acme", "widget", "octocat", "pull")
        assert client.is_collaborator("acme", "widget", "octocat") is False

        client.accepted.add(("acme", "widget", "octocat"))
        assert client.is_collaborator("acme", "widget", "octocat") is True


class TestMockInvitations:
    def test_add_records_invitation_then_cancel_removes_it(self):
        client = MockGithubAppClient()
        result = client.add_collaborator("acme", "widget", "octocat", "pull")
        invitations = client.list_repo_invitations("acme", "widget")
        assert any(str(item["id"]) == result.invitation_id for item in invitations)

        client.cancel_invitation("acme", "widget", result.invitation_id)
        invitations_after = client.list_repo_invitations("acme", "widget")
        assert all(
            str(item["id"]) != result.invitation_id for item in invitations_after
        )

    def test_cancel_unknown_invitation_is_tolerated(self):
        client = MockGithubAppClient()
        client.cancel_invitation("acme", "widget", "does-not-exist")
