"""Team-membership client methods — mock parity + real HTTP shape (S132).

Adding a developer to a GitHub team is instant and creates at most one org
invitation (the first join), unlike per-repo collaborator invitations. The
mock mirrors the real client's contract (same signatures, same
``GithubAppClientError`` on failure) so service unit tests need no network; the
real client is exercised against an httpx ``MockTransport`` (CI offline-green).
"""
import httpx
import pytest

from plugins.ghrm.src.services.github_app_client import (
    MockGithubAppClient,
    TeamMembershipResult,
)
from plugins.ghrm.src.services.github_app_client_real import (
    GithubAppClient,
    GithubAppClientError,
)

INSTALLATION_TOKEN = "ghs-installation-token"


def _make_client(handler) -> GithubAppClient:
    transport = httpx.MockTransport(handler)
    client = GithubAppClient(
        app_id="123",
        private_key="unused-with-mock-transport",
        installation_id="456",
        transport=transport,
    )
    client.set_installation_token(INSTALLATION_TOKEN)
    return client


class TestMockTeamMembership:
    def test_add_returns_pending_when_not_org_member(self):
        client = MockGithubAppClient()
        result = client.add_team_membership("acme-inc", "developers", "octocat")
        assert isinstance(result, TeamMembershipResult)
        assert result.state == "pending"

    def test_add_returns_active_when_already_org_member(self):
        client = MockGithubAppClient()
        client.org_members.setdefault("acme-inc", set()).add("octocat")
        result = client.add_team_membership("acme-inc", "developers", "octocat")
        assert result.state == "active"

    def test_get_returns_none_when_absent(self):
        client = MockGithubAppClient()
        assert client.get_team_membership("acme-inc", "developers", "nobody") is None

    def test_get_returns_pending_then_active_after_org_join(self):
        client = MockGithubAppClient()
        client.add_team_membership("acme-inc", "developers", "octocat")
        assert (
            client.get_team_membership("acme-inc", "developers", "octocat") == "pending"
        )
        client.org_members.setdefault("acme-inc", set()).add("octocat")
        assert (
            client.get_team_membership("acme-inc", "developers", "octocat") == "active"
        )

    def test_remove_discards_membership(self):
        client = MockGithubAppClient()
        client.add_team_membership("acme-inc", "developers", "octocat")
        assert (
            client.remove_team_membership("acme-inc", "developers", "octocat") is True
        )
        assert client.get_team_membership("acme-inc", "developers", "octocat") is None

    def test_add_failure_hook_raises_client_error(self):
        client = MockGithubAppClient()
        client.raise_on_add_team_membership = GithubAppClientError("403 forbidden")
        with pytest.raises(GithubAppClientError):
            client.add_team_membership("acme-inc", "developers", "octocat")

    def test_remove_failure_hook_raises_client_error(self):
        client = MockGithubAppClient()
        client.raise_on_remove_team_membership = GithubAppClientError("403 forbidden")
        with pytest.raises(GithubAppClientError):
            client.remove_team_membership("acme-inc", "developers", "octocat")


class TestRealAddTeamMembership:
    def test_issues_put_to_team_membership_url(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"state": "pending", "role": "member"})

        result = _make_client(handler).add_team_membership(
            "acme-inc", "developers", "octocat"
        )
        assert captured["method"] == "PUT"
        assert captured["url"] == (
            "https://api.github.com/orgs/acme-inc/teams/developers/"
            "memberships/octocat"
        )
        assert captured["auth"] == f"Bearer {INSTALLATION_TOKEN}"
        assert result.state == "pending"

    def test_active_state_maps_through(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"state": "active", "role": "member"})

        result = _make_client(handler).add_team_membership(
            "acme-inc", "developers", "octocat"
        )
        assert result.state == "active"

    def test_non_2xx_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="forbidden-detail")

        with pytest.raises(GithubAppClientError, match="forbidden-detail"):
            _make_client(handler).add_team_membership(
                "acme-inc", "developers", "octocat"
            )


class TestRealGetTeamMembership:
    def test_200_returns_state(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            return httpx.Response(200, json={"state": "active", "role": "member"})

        assert (
            _make_client(handler).get_team_membership(
                "acme-inc", "developers", "octocat"
            )
            == "active"
        )

    def test_404_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        assert (
            _make_client(handler).get_team_membership(
                "acme-inc", "developers", "octocat"
            )
            is None
        )

    def test_other_status_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(GithubAppClientError):
            _make_client(handler).get_team_membership(
                "acme-inc", "developers", "octocat"
            )


class TestRealRemoveTeamMembership:
    def test_issues_delete_and_tolerates_404(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            return httpx.Response(404)

        assert (
            _make_client(handler).remove_team_membership(
                "acme-inc", "developers", "octocat"
            )
            is True
        )
        assert captured["method"] == "DELETE"
        assert captured["url"] == (
            "https://api.github.com/orgs/acme-inc/teams/developers/"
            "memberships/octocat"
        )

    def test_other_status_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(GithubAppClientError):
            _make_client(handler).remove_team_membership(
                "acme-inc", "developers", "octocat"
            )
