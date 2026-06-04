"""Gated live integration test against REAL GitHub (S49.6, decision D4).

This is the proof that closes the sprint: it drives the real
``GithubAppClient`` against an actual throwaway repo to confirm the
invite -> list -> remove collaborator lifecycle works end-to-end. It is the
one place that exercises real network + real GitHub App credentials.

It is OPT-IN and SKIPPED unless every one of the following is present:

    GHRM_LIVE_TEST=1                  explicit opt-in switch
    GHRM_LIVE_TEST_REPO              "owner/repo" of a THROWAWAY repo you own
    GHRM_LIVE_TEST_GITHUB_USER       the GitHub login to invite (a test account)
    GHRM_GITHUB_APP_ID               GitHub App numeric id
    GHRM_GITHUB_INSTALLATION_ID      installation id on the repo's owner
    GHRM_GITHUB_APP_PRIVATE_KEY_PATH path to the App's PEM (mounted secret)

Because CI has none of these, this test is SKIPPED in CI and never touches the
network there — the offline suite stays green. No secrets are committed; all
credentials come from the environment / a mounted PEM.

The test is idempotent and self-cleaning: it cancels any invitation and removes
the collaborator it created so the throwaway repo is left exactly as it was.
"""
import os

import pytest

from plugins.ghrm.src.services.github_app_client_real import GithubAppClient

LIVE_TEST_ENABLED = os.environ.get("GHRM_LIVE_TEST") == "1"
LIVE_TEST_REPO = os.environ.get("GHRM_LIVE_TEST_REPO", "")
LIVE_TEST_GITHUB_USER = os.environ.get("GHRM_LIVE_TEST_GITHUB_USER", "")
GITHUB_APP_ID = os.environ.get("GHRM_GITHUB_APP_ID", "")
GITHUB_INSTALLATION_ID = os.environ.get("GHRM_GITHUB_INSTALLATION_ID", "")
GITHUB_APP_PRIVATE_KEY_PATH = os.environ.get("GHRM_GITHUB_APP_PRIVATE_KEY_PATH", "")

_REQUIRED_PRESENT = (
    LIVE_TEST_ENABLED
    and bool(LIVE_TEST_REPO)
    and bool(LIVE_TEST_GITHUB_USER)
    and bool(GITHUB_APP_ID)
    and bool(GITHUB_INSTALLATION_ID)
    and bool(GITHUB_APP_PRIVATE_KEY_PATH)
    and os.path.isfile(GITHUB_APP_PRIVATE_KEY_PATH)
)

pytestmark = pytest.mark.skipif(
    not _REQUIRED_PRESENT,
    reason=(
        "live GitHub test is opt-in: requires GHRM_LIVE_TEST=1, a throwaway "
        "GHRM_LIVE_TEST_REPO (owner/repo), GHRM_LIVE_TEST_GITHUB_USER, and real "
        "GitHub App creds (GHRM_GITHUB_APP_ID, GHRM_GITHUB_INSTALLATION_ID, "
        "GHRM_GITHUB_APP_PRIVATE_KEY_PATH pointing at a real PEM). Skipped in CI."
    ),
)


def _split_owner_repo(owner_repo: str) -> tuple:
    owner, _, repo = owner_repo.partition("/")
    if not owner or not repo:
        raise ValueError(
            f"GHRM_LIVE_TEST_REPO must be 'owner/repo', got {owner_repo!r}"
        )
    return owner, repo


@pytest.fixture
def live_client() -> GithubAppClient:
    with open(GITHUB_APP_PRIVATE_KEY_PATH, "r") as private_key_file:
        private_key = private_key_file.read()
    return GithubAppClient(
        app_id=GITHUB_APP_ID,
        private_key=private_key,
        installation_id=GITHUB_INSTALLATION_ID,
    )


def _cleanup(client: GithubAppClient, owner: str, repo: str, username: str) -> None:
    """Remove any collaborator / pending invitation for ``username``.

    Safe to call repeatedly; leaves the throwaway repo with no trace of the run.
    """
    for invitation in client.list_repo_invitations(owner, repo):
        invitee_login = (invitation.get("invitee") or {}).get("login")
        if invitee_login == username:
            client.cancel_invitation(owner, repo, str(invitation["id"]))
    client.remove_collaborator(owner, repo, username)


def test_collaborator_invite_list_remove_lifecycle(live_client):
    """Invite -> appears in invitations -> remove -> gone, against real GitHub.

    NOTE on the accept step: turning an invitation into an active collaborator
    requires the *invited user* to accept it (or a PAT call as that user) — see
    the GHRM README "Going live" section. This automated test asserts the
    invite/list/cancel/remove half of the lifecycle that the platform controls,
    and leaves the repo clean regardless of acceptance state.
    """
    owner, repo = _split_owner_repo(LIVE_TEST_REPO)
    username = LIVE_TEST_GITHUB_USER

    # Pre-clean so a previous interrupted run can't make this flaky.
    _cleanup(live_client, owner, repo, username)
    try:
        result = live_client.add_collaborator(owner, repo, username, "push")
        assert result.state in ("invited", "active")

        if result.state == "invited":
            assert result.invitation_id is not None
            invitations = live_client.list_repo_invitations(owner, repo)
            invited_logins = {
                (item.get("invitee") or {}).get("login") for item in invitations
            }
            assert username in invited_logins
            live_client.cancel_invitation(owner, repo, result.invitation_id)
        else:
            # Already an active collaborator (e.g. the user accepted previously).
            assert live_client.is_collaborator(owner, repo, username) is True
    finally:
        _cleanup(live_client, owner, repo, username)

    # After cleanup the user is neither an active collaborator nor invited.
    assert live_client.is_collaborator(owner, repo, username) is False
    remaining_logins = {
        (item.get("invitee") or {}).get("login")
        for item in live_client.list_repo_invitations(owner, repo)
    }
    assert username not in remaining_logins
