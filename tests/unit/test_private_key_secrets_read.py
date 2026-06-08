"""Unit tests — GHRM private key (PEM) is read through the FilesystemManager.

Sprint 58.4 (D4 / Security #2): the GitHub App RSA private key must no longer be
read via a bare ``open()``. It is routed through the core FilesystemManager's
``secrets``-posture path so it inherits:

  * path confinement (realpath-within-namespace — no ``..`` / symlink escape), and
  * the secrets perms posture (0700 dir / 0600 file).

External provisioning stays supported: the key is placed by ops and is plaintext
(we do not control its writing), so the read MUST NOT attempt decryption. The
configured absolute path is honoured exactly (legacy ``/app/var/ghrm/auth/...``
deployments keep working) by pinning the read root to the configured file's
directory and reading its basename through the confined manager.
"""
import os

import pytest

from plugins.ghrm.src.routes import read_private_key_pem


_SAMPLE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
    "-----END RSA PRIVATE KEY-----\n"
)


class TestReadPrivateKeyPem:
    def test_reads_key_contents_from_configured_path(self, tmp_path):
        pem_path = tmp_path / "secrets" / "ghrm" / "github-app.pem"
        pem_path.parent.mkdir(parents=True)
        pem_path.write_text(_SAMPLE_PEM)

        result = read_private_key_pem(str(pem_path))

        assert result == _SAMPLE_PEM

    def test_legacy_absolute_path_is_read_correctly(self, tmp_path):
        # The prod default lives outside the canonical secrets/ tree; existing
        # deployments must keep working at the EXACT configured path.
        legacy = tmp_path / "ghrm" / "auth" / "github-app.pem"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(_SAMPLE_PEM)

        result = read_private_key_pem(str(legacy))

        assert result == _SAMPLE_PEM

    def test_missing_file_raises_file_not_found(self, tmp_path):
        missing = tmp_path / "ghrm" / "auth" / "github-app.pem"

        with pytest.raises(FileNotFoundError):
            read_private_key_pem(str(missing))

    def test_symlink_escaping_pinned_dir_is_rejected(self, tmp_path):
        # The configured directory is pinned as the namespace root; a symlink
        # inside it that resolves OUTSIDE the root must be rejected by the
        # realpath-within-namespace confinement guard, not silently followed.
        secret_outside = tmp_path / "outside.pem"
        secret_outside.write_text(_SAMPLE_PEM)
        pem_dir = tmp_path / "ghrm" / "auth"
        pem_dir.mkdir(parents=True)
        link_path = pem_dir / "github-app.pem"
        os.symlink(str(secret_outside), str(link_path))

        with pytest.raises(ValueError):
            read_private_key_pem(str(link_path))

    def test_read_does_not_attempt_decryption_of_plaintext_key(self, tmp_path):
        # Externally provisioned keys are plaintext; the read path must return
        # them verbatim (no cipher round-trip that would corrupt/raise).
        pem_path = tmp_path / "ghrm" / "auth" / "github-app.pem"
        pem_path.parent.mkdir(parents=True)
        pem_path.write_text(_SAMPLE_PEM)

        result = read_private_key_pem(str(pem_path))

        assert "BEGIN RSA PRIVATE KEY" in result
        assert result == _SAMPLE_PEM


class TestGithubClientConsumesManagerReadKey:
    def test_jwt_signing_path_uses_manager_read_key(self, tmp_path, monkeypatch):
        # End-to-end (offline): the key read through the manager flows into the
        # GitHub App client and signs a JWT with no network access.
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pem_path = tmp_path / "ghrm" / "auth" / "github-app.pem"
        pem_path.parent.mkdir(parents=True)
        pem_path.write_bytes(pem_bytes)

        key_contents = read_private_key_pem(str(pem_path))

        from plugins.ghrm.src.services.github_app_client_real import GithubAppClient

        client = GithubAppClient(
            app_id="12345",
            private_key=key_contents,
            installation_id="67890",
        )
        token = client._make_jwt()

        assert isinstance(token, str) and token.count(".") == 2
