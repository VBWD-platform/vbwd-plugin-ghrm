"""Unit tests — offline GitHub-configuration verifier (S128).

The verifier proves, without any human OAuth click-through, whether an
instance's GitHub credentials are valid. These tests drive every branch with a
FAKE http caller and a fixed clock so the whole suite is deterministic and
never touches the network.

The two things that MUST hold no matter what:
  * every branch (OAuth 404/401/422, App 200/401, installation 200/404, unset
    creds, network exception) maps to the documented PASS/FAIL/WARN/SKIP status;
  * the client_secret and the PEM contents are NEVER present in any result
    message or repr.
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import jwt as pyjwt
import pytest

from plugins.ghrm.src.services.github_app_client_real import GithubAppClient
from plugins.ghrm.src.services.github_config_verifier import (
    CheckResult,
    HttpResponse,
    PASS,
    FAIL,
    WARN,
    SKIP,
    verify_all,
    verify_app_credentials,
    verify_callback_url,
    verify_installation,
    verify_oauth_pair,
)

FIXED_NOW = 1_700_000_000
SECRET_SENTINEL = "s3cr3t-client-secret-DO-NOT-LEAK"
PEM_SENTINEL = "PEM-CONTENTS-DO-NOT-LEAK-1234567890"


@pytest.fixture(scope="module")
def rsa_keypair():
    """A real RSA private-key PEM plus its public key (for JWT decode)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return pem, private_key.public_key()


class _StubHttp:
    """Single-shot fake http caller — returns one response or raises one error."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.last_call = None

    def __call__(self, method, url, *, headers=None, auth=None, json=None):
        self.last_call = {
            "method": method,
            "url": url,
            "headers": headers,
            "auth": auth,
            "json": json,
        }
        if self.error is not None:
            raise self.error
        return self.response


class _RoutingHttp:
    """Fake http caller that picks a response by (method, url-substring)."""

    def __init__(self, routes):
        self._routes = routes  # list of (method, url_substring, HttpResponse)
        self.calls = []

    def __call__(self, method, url, *, headers=None, auth=None, json=None):
        self.calls.append((method, url))
        for route_method, url_substring, response in self._routes:
            if route_method == method and url_substring in url:
                return response
        raise AssertionError(f"unexpected call {method} {url}")


# ── OAuth App pair ────────────────────────────────────────────────────────────


class TestVerifyOauthPair:
    def test_404_means_pair_recognized_pass(self):
        http = _StubHttp(HttpResponse(404, {"message": "Not Found"}))
        result = verify_oauth_pair("Ov23liABCDEFGH", SECRET_SENTINEL, http=http)
        assert result.status == PASS
        assert result.http_code == 404
        assert http.last_call["method"] == "POST"
        assert "applications/Ov23liABCDEFGH/token" in http.last_call["url"]
        assert http.last_call["auth"] == ("Ov23liABCDEFGH", SECRET_SENTINEL)

    def test_401_means_bad_credentials_fail(self):
        http = _StubHttp(HttpResponse(401, {"message": "Bad credentials"}))
        result = verify_oauth_pair("Ov23liABCDEFGH", SECRET_SENTINEL, http=http)
        assert result.status == FAIL
        assert result.http_code == 401

    def test_422_means_unprocessable_warn(self):
        http = _StubHttp(HttpResponse(422, {"message": "Unprocessable"}))
        result = verify_oauth_pair("Ov23liABCDEFGH", SECRET_SENTINEL, http=http)
        assert result.status == WARN
        assert result.http_code == 422

    def test_unset_credentials_skip(self):
        http = _StubHttp(HttpResponse(404, {}))
        result = verify_oauth_pair("", "", http=http)
        assert result.status == SKIP
        assert http.last_call is None  # never called GitHub

    def test_network_error_is_warn_not_traceback(self):
        http = _StubHttp(error=RuntimeError("connection refused"))
        result = verify_oauth_pair("Ov23liABCDEFGH", SECRET_SENTINEL, http=http)
        assert result.status == WARN
        assert "connection refused" in result.message

    def test_secret_never_leaks(self):
        for response in (
            HttpResponse(404, {"message": "Not Found"}),
            HttpResponse(401, {"message": "Bad credentials"}),
            HttpResponse(422, {}),
        ):
            http = _StubHttp(response)
            result = verify_oauth_pair("Ov23liABCDEFGH", SECRET_SENTINEL, http=http)
            assert SECRET_SENTINEL not in repr(result)
            assert SECRET_SENTINEL not in result.message


# ── GitHub App credentials ────────────────────────────────────────────────────


class TestVerifyAppCredentials:
    def test_app_endpoint_200_pass_with_slug(self, rsa_keypair):
        pem, _public_key = rsa_keypair
        http = _StubHttp(HttpResponse(200, {"slug": "vbwd-ghrm", "id": 42}))
        result = verify_app_credentials("123", pem, http=http, now=FIXED_NOW)
        assert result.status == PASS
        assert result.http_code == 200
        assert "vbwd-ghrm" in result.message
        assert http.last_call["method"] == "GET"
        assert http.last_call["url"].endswith("/app")
        assert http.last_call["headers"]["Authorization"].startswith("Bearer ")

    def test_app_endpoint_401_fail(self, rsa_keypair):
        pem, _public_key = rsa_keypair
        http = _StubHttp(HttpResponse(401, {"message": "bad creds"}))
        result = verify_app_credentials("123", pem, http=http, now=FIXED_NOW)
        assert result.status == FAIL
        assert result.http_code == 401

    def test_unset_app_credentials_skip(self):
        http = _StubHttp(HttpResponse(200, {"slug": "x"}))
        result = verify_app_credentials("", "", http=http, now=FIXED_NOW)
        assert result.status == SKIP
        assert http.last_call is None

    def test_bad_pem_is_fail_without_leaking_pem(self):
        broken_pem = f"-----BEGIN X-----\n{PEM_SENTINEL}\n-----END X-----\n"
        http = _StubHttp(HttpResponse(200, {"slug": "x"}))
        result = verify_app_credentials("123", broken_pem, http=http, now=FIXED_NOW)
        assert result.status == FAIL
        assert http.last_call is None  # never reached the network
        assert PEM_SENTINEL not in repr(result)
        assert PEM_SENTINEL not in result.message

    def test_network_error_is_warn(self, rsa_keypair):
        pem, _public_key = rsa_keypair
        http = _StubHttp(error=RuntimeError("timeout"))
        result = verify_app_credentials("123", pem, http=http, now=FIXED_NOW)
        assert result.status == WARN
        assert "timeout" in result.message

    def test_injected_clock_drives_jwt_claims(self, rsa_keypair):
        pem, public_key = rsa_keypair
        captured = {}

        def _capture(method, url, *, headers=None, auth=None, json=None):
            captured["token"] = headers["Authorization"].split(" ", 1)[1]
            return HttpResponse(200, {"slug": "vbwd-ghrm"})

        verify_app_credentials("999", pem, http=_capture, now=FIXED_NOW)
        claims = pyjwt.decode(
            captured["token"],
            public_key,
            algorithms=["RS256"],
            options={"verify_exp": False},
        )
        assert claims["iss"] == "999"
        assert claims["iat"] == FIXED_NOW - 60
        assert claims["exp"] == FIXED_NOW + 540


# ── Installation ──────────────────────────────────────────────────────────────


class TestVerifyInstallation:
    def test_installation_200_pass(self, rsa_keypair):
        pem, _public_key = rsa_keypair
        http = _StubHttp(HttpResponse(200, {"id": 115771705}))
        result = verify_installation("123", pem, "115771705", http=http, now=FIXED_NOW)
        assert result.status == PASS
        assert "app/installations/115771705" in http.last_call["url"]

    def test_installation_404_fail(self, rsa_keypair):
        pem, _public_key = rsa_keypair
        http = _StubHttp(HttpResponse(404, {"message": "Not Found"}))
        result = verify_installation("123", pem, "999", http=http, now=FIXED_NOW)
        assert result.status == FAIL
        assert result.http_code == 404

    def test_unset_installation_skip(self, rsa_keypair):
        pem, _public_key = rsa_keypair
        http = _StubHttp(HttpResponse(200, {}))
        result = verify_installation("123", pem, "", http=http, now=FIXED_NOW)
        assert result.status == SKIP
        assert http.last_call is None


# ── Callback URL echo ─────────────────────────────────────────────────────────


class TestVerifyCallbackUrl:
    def test_configured_url_is_echoed(self):
        url = "https://vbwd.cc/ghrm/auth/github/callback"
        result = verify_callback_url(url)
        assert result.status == SKIP
        assert url in result.message

    def test_unset_url_skip(self):
        result = verify_callback_url("")
        assert result.status == SKIP


# ── verify_all orchestration ──────────────────────────────────────────────────


class TestVerifyAll:
    def test_empty_config_all_skip_no_network(self):
        def _no_network(*args, **kwargs):
            raise AssertionError("verify_all must not hit the network for empty config")

        results = verify_all({}, http=_no_network, now=FIXED_NOW)
        assert [r.name for r in results] == [
            "OAuth App (client_id/secret)",
            "GitHub App (app_id + PEM)",
            "Installation",
            "Callback URL",
        ]
        assert all(isinstance(r, CheckResult) for r in results)
        assert all(r.status == SKIP for r in results)

    def test_dispatches_all_checks(self, rsa_keypair, tmp_path):
        pem, _public_key = rsa_keypair
        pem_file = tmp_path / "github-app.pem"
        pem_file.write_text(pem)
        config = {
            "github_app_id": "123",
            "github_app_private_key_path": str(pem_file),
            "github_installation_id": "115771705",
            "github_oauth_client_id": "Ov23liABCDEFGH",
            "github_oauth_client_secret": SECRET_SENTINEL,
            "github_oauth_redirect_uri": "https://vbwd.cc/ghrm/auth/github/callback",
        }
        http = _RoutingHttp(
            [
                ("POST", "/applications/", HttpResponse(404, {"message": "Not Found"})),
                ("GET", "/app/installations/", HttpResponse(200, {"id": 1})),
                ("GET", "/app", HttpResponse(200, {"slug": "vbwd-ghrm"})),
            ]
        )
        results = verify_all(config, http=http, now=FIXED_NOW)
        by_name = {r.name: r for r in results}
        assert by_name["OAuth App (client_id/secret)"].status == PASS
        assert by_name["GitHub App (app_id + PEM)"].status == PASS
        assert "vbwd-ghrm" in by_name["GitHub App (app_id + PEM)"].message
        assert by_name["Installation 115771705"].status == PASS
        assert by_name["Callback URL"].status == SKIP
        for result in results:
            assert SECRET_SENTINEL not in repr(result)


# ── JWT seam reused from GithubAppClient (DRY) ────────────────────────────────


class TestJwtSeamReuse:
    def test_mint_app_jwt_uses_injected_clock(self, rsa_keypair):
        pem, public_key = rsa_keypair
        token = GithubAppClient.mint_app_jwt("555", pem, now=FIXED_NOW)
        claims = pyjwt.decode(
            token, public_key, algorithms=["RS256"], options={"verify_exp": False}
        )
        assert claims == {
            "iss": "555",
            "iat": FIXED_NOW - 60,
            "exp": FIXED_NOW + 540,
        }
