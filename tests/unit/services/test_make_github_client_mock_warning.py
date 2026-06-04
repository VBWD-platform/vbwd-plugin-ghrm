"""Root-cause guard for the silent-mock bug (S49.6).

The original live failure was that ``_make_github_client`` silently returned a
``MockGithubAppClient`` in an environment that was meant to talk to real GitHub.
These tests pin the behaviour that selecting the mock now emits a loud WARN, and
that selecting the real client does NOT emit it.
"""
import logging

from plugins.ghrm.src.routes import _make_github_client
from plugins.ghrm.src.services.github_app_client import MockGithubAppClient
from plugins.ghrm.src.services.github_app_client_real import GithubAppClient

MOCK_WARNING_FRAGMENT = "using MOCK GitHub client"


def test_mock_selection_emits_warning(monkeypatch, caplog):
    monkeypatch.setenv("GHRM_USE_MOCK_GITHUB", "true")

    with caplog.at_level(logging.WARNING, logger="plugins.ghrm.src.routes"):
        client = _make_github_client({})

    assert isinstance(client, MockGithubAppClient)
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert any(
        MOCK_WARNING_FRAGMENT in message for message in warning_messages
    ), f"expected a WARN containing {MOCK_WARNING_FRAGMENT!r}, got {warning_messages!r}"


def test_real_selection_does_not_emit_mock_warning(monkeypatch, caplog, tmp_path):
    monkeypatch.delenv("GHRM_USE_MOCK_GITHUB", raising=False)
    private_key_path = tmp_path / "github-app.pem"
    private_key_path.write_text(
        "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n"
    )
    config = {
        "github_app_id": "123456",
        "github_installation_id": "789012",
        "github_app_private_key_path": str(private_key_path),
    }

    with caplog.at_level(logging.WARNING, logger="plugins.ghrm.src.routes"):
        client = _make_github_client(config)

    assert isinstance(client, GithubAppClient)
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert not any(
        MOCK_WARNING_FRAGMENT in message for message in warning_messages
    ), f"real client must not log the mock WARN, got {warning_messages!r}"
