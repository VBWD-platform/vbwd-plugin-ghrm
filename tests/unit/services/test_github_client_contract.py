"""Contract tests for the real GithubAppClient (S49.2).

Exercises the invitation-aware collaborator surface against an httpx
``MockTransport`` so the exact HTTP requests + status-code mapping are
asserted with no network access (CI offline-green).
"""
import httpx
import pytest

from plugins.ghrm.src.services.github_app_client import AddCollaboratorResult
from plugins.ghrm.src.services.github_app_client_real import (
    GithubAppClient,
    GithubAppClientError,
)

INSTALLATION_TOKEN = "ghs-installation-token"


def _make_client(handler) -> GithubAppClient:
    """Build a real client whose HTTP layer is a MockTransport.

    The installation token is pre-seeded so collaborator calls never trigger
    a token-mint round trip — the handler only sees the call under test.
    """
    transport = httpx.MockTransport(handler)
    client = GithubAppClient(
        app_id="123",
        private_key="unused-with-mock-transport",
        installation_id="456",
        transport=transport,
    )
    client.set_installation_token(INSTALLATION_TOKEN)
    return client


class TestAddCollaborator:
    def test_issues_put_with_push_permission_and_headers(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("Authorization")
            captured["api_version"] = request.headers.get("X-GitHub-Api-Version")
            captured["body"] = request.content
            return httpx.Response(201, json={"id": 555})

        client = _make_client(handler)
        result = client.add_collaborator("acme", "widget", "octocat", "push")

        assert captured["method"] == "PUT"
        assert (
            captured["url"]
            == "https://api.github.com/repos/acme/widget/collaborators/octocat"
        )
        assert captured["auth"] == f"Bearer {INSTALLATION_TOKEN}"
        assert captured["api_version"] == "2022-11-28"
        assert b'"permission"' in captured["body"]
        assert b'"push"' in captured["body"]
        assert result == AddCollaboratorResult(state="invited", invitation_id="555")

    def test_201_maps_to_invited_with_invitation_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"id": 9001})

        result = _make_client(handler).add_collaborator(
            "acme", "widget", "octocat", "push"
        )
        assert result.state == "invited"
        assert result.invitation_id == "9001"

    def test_204_maps_to_active_with_no_invitation_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        result = _make_client(handler).add_collaborator(
            "acme", "widget", "octocat", "push"
        )
        assert result.state == "active"
        assert result.invitation_id is None

    def test_403_raises_with_body_in_message(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="forbidden-detail")

        with pytest.raises(GithubAppClientError, match="forbidden-detail"):
            _make_client(handler).add_collaborator("acme", "widget", "octocat", "push")

    def test_404_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        with pytest.raises(GithubAppClientError):
            _make_client(handler).add_collaborator("acme", "widget", "octocat", "push")


class TestIsCollaborator:
    def test_204_is_true(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert (
                str(request.url)
                == "https://api.github.com/repos/acme/widget/collaborators/octocat"
            )
            return httpx.Response(204)

        assert (
            _make_client(handler).is_collaborator("acme", "widget", "octocat") is True
        )

    def test_404_is_false(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        assert (
            _make_client(handler).is_collaborator("acme", "widget", "octocat") is False
        )

    def test_other_status_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(GithubAppClientError):
            _make_client(handler).is_collaborator("acme", "widget", "octocat")


class TestListRepoInvitations:
    def test_returns_invitation_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert (
                str(request.url)
                == "https://api.github.com/repos/acme/widget/invitations"
            )
            return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

        result = _make_client(handler).list_repo_invitations("acme", "widget")
        assert [item["id"] for item in result] == [1, 2]


class TestCancelInvitation:
    def test_issues_delete_to_invitation_url(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            return httpx.Response(204)

        _make_client(handler).cancel_invitation("acme", "widget", "777")
        assert captured["method"] == "DELETE"
        assert (
            captured["url"]
            == "https://api.github.com/repos/acme/widget/invitations/777"
        )

    def test_404_is_tolerated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        # Should not raise — already gone is fine.
        _make_client(handler).cancel_invitation("acme", "widget", "777")

    def test_other_status_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(GithubAppClientError):
            _make_client(handler).cancel_invitation("acme", "widget", "777")
