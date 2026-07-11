"""Gated live verification against REAL GitHub (S128).

This proves, end-to-end and without any human OAuth click-through, that a real
instance's GitHub configuration is valid: it runs ``verify_all`` against
api.github.com and asserts every applicable check is PASS.

It is OPT-IN and SKIPPED unless every one of the following is present (mirroring
``test_github_live.py`` exactly):

    GHRM_LIVE_TEST=1                     explicit opt-in switch
    GHRM_GITHUB_APP_ID                   GitHub App numeric id
    GHRM_GITHUB_INSTALLATION_ID          installation id on the App's account
    GHRM_GITHUB_APP_PRIVATE_KEY_PATH     path to the App's PEM (mounted secret)
    GHRM_GITHUB_OAUTH_CLIENT_ID          OAuth App client id
    GHRM_GITHUB_OAUTH_CLIENT_SECRET      OAuth App client secret

Because CI has none of these, this test is SKIPPED in CI and never touches the
network there — the offline suite stays green. No secrets are committed; all
credentials come from the environment / a mounted PEM.
"""
import os

import pytest

from plugins.ghrm.src.services.github_config_verifier import (
    FAIL,
    PASS,
    SKIP,
    verify_all,
)

LIVE_TEST_ENABLED = os.environ.get("GHRM_LIVE_TEST") == "1"
GITHUB_APP_ID = os.environ.get("GHRM_GITHUB_APP_ID", "")
GITHUB_INSTALLATION_ID = os.environ.get("GHRM_GITHUB_INSTALLATION_ID", "")
GITHUB_APP_PRIVATE_KEY_PATH = os.environ.get("GHRM_GITHUB_APP_PRIVATE_KEY_PATH", "")
GITHUB_OAUTH_CLIENT_ID = os.environ.get("GHRM_GITHUB_OAUTH_CLIENT_ID", "")
GITHUB_OAUTH_CLIENT_SECRET = os.environ.get("GHRM_GITHUB_OAUTH_CLIENT_SECRET", "")

_REQUIRED_PRESENT = (
    LIVE_TEST_ENABLED
    and bool(GITHUB_APP_ID)
    and bool(GITHUB_INSTALLATION_ID)
    and bool(GITHUB_APP_PRIVATE_KEY_PATH)
    and os.path.isfile(GITHUB_APP_PRIVATE_KEY_PATH)
    and bool(GITHUB_OAUTH_CLIENT_ID)
    and bool(GITHUB_OAUTH_CLIENT_SECRET)
)

pytestmark = pytest.mark.skipif(
    not _REQUIRED_PRESENT,
    reason=(
        "live GitHub config verification is opt-in: requires GHRM_LIVE_TEST=1 "
        "plus real OAuth App creds (GHRM_GITHUB_OAUTH_CLIENT_ID / _SECRET) and "
        "GitHub App creds (GHRM_GITHUB_APP_ID, GHRM_GITHUB_INSTALLATION_ID, "
        "GHRM_GITHUB_APP_PRIVATE_KEY_PATH pointing at a real PEM). Skipped in CI."
    ),
)


def test_verify_all_against_real_github_passes():
    """Every applicable check PASSes against real GitHub; none FAILs."""
    config = {
        "github_app_id": GITHUB_APP_ID,
        "github_app_private_key_path": GITHUB_APP_PRIVATE_KEY_PATH,
        "github_installation_id": GITHUB_INSTALLATION_ID,
        "github_oauth_client_id": GITHUB_OAUTH_CLIENT_ID,
        "github_oauth_client_secret": GITHUB_OAUTH_CLIENT_SECRET,
        "github_oauth_redirect_uri": os.environ.get(
            "GHRM_GITHUB_OAUTH_REDIRECT_URI", ""
        ),
    }

    results = verify_all(config)
    by_name = {result.name: result for result in results}

    assert by_name["OAuth App (client_id/secret)"].status == PASS
    assert by_name["GitHub App (app_id + PEM)"].status == PASS
    assert by_name[f"Installation {GITHUB_INSTALLATION_ID}"].status == PASS
    # No check may FAIL; the callback URL is echo-only (SKIP).
    assert all(result.status != FAIL for result in results)
    assert by_name["Callback URL"].status == SKIP

    # The client_secret must never surface in any result.
    for result in results:
        assert GITHUB_OAUTH_CLIENT_SECRET not in repr(result)
