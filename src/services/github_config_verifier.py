"""Offline-testable verifier for a GHRM instance's GitHub configuration (S128).

Proves whether the instance's GitHub credentials are valid WITHOUT any human
OAuth click-through, so an operator can run it on any box (local or prod). It
covers the two independent halves of the GitHub setup:

  * the **OAuth App** ``client_id`` / ``client_secret`` pair — probed via
    ``POST /applications/{client_id}/token`` (404 => the pair is recognised and
    valid; 401 => rejected);
  * the **GitHub App** ``app_id`` + PEM — probed by minting a short-lived JWT
    and calling ``GET /app`` (200 => valid, surfaces the app slug);
  * the optional **installation** id — ``GET /app/installations/{id}`` with the
    same JWT;
  * the OAuth **callback URL** — echoed only (there is no owner-less API to read
    an OAuth App's registered callback), so the operator can eyeball it.

The verification LOGIC takes an injectable HTTP caller and an injectable clock,
so unit tests drive every branch with mocked GitHub responses and ZERO network.
Secrets (client_secret, PEM contents) are NEVER put into a result message.
"""
from dataclasses import dataclass
from typing import Optional, Protocol

import time

import httpx

from plugins.ghrm.src.services.github_app_client_real import GithubAppClient

# ── Status vocabulary ─────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"

# ── GitHub HTTP semantics we branch on ────────────────────────────────────────

_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401
_HTTP_NOT_FOUND = 404
_HTTP_UNPROCESSABLE = 422

_GITHUB_API = "https://api.github.com"
_GITHUB_JSON_ACCEPT = "application/vnd.github+json"

# How many leading characters of the (public) client_id to show in a report.
_CLIENT_ID_VISIBLE_PREFIX = 6


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single verification check.

    ``status`` is one of PASS / FAIL / WARN / SKIP; ``http_code`` is the GitHub
    response code when a call was made (else ``None``); ``message`` is a short
    human-readable explanation that NEVER contains a secret.
    """

    name: str
    status: str
    http_code: Optional[int]
    message: str


@dataclass(frozen=True)
class HttpResponse:
    """Minimal response shape the verifier depends on (status + parsed body)."""

    status_code: int
    body: dict


class HttpCaller(Protocol):
    """Narrow port the verifier depends on — one method, injected for tests."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict] = None,
        auth: Optional[tuple] = None,
        json: Optional[dict] = None,
    ) -> HttpResponse:
        ...


class HttpxCaller:
    """Default ``HttpCaller`` — performs the real network call via httpx."""

    def __init__(self, timeout_seconds: int = 15) -> None:
        self._timeout_seconds = timeout_seconds

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict] = None,
        auth: Optional[tuple] = None,
        json: Optional[dict] = None,
    ) -> HttpResponse:
        with httpx.Client(timeout=self._timeout_seconds) as client:
            response = client.request(
                method, url, headers=headers, auth=auth, json=json
            )
        try:
            parsed = response.json()
        except ValueError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        return HttpResponse(status_code=response.status_code, body=parsed)


def _mask_client_id(client_id: str) -> str:
    """Return a truncated, safe-to-print form of the (public) client_id."""
    if len(client_id) <= _CLIENT_ID_VISIBLE_PREFIX:
        return client_id
    return f"{client_id[:_CLIENT_ID_VISIBLE_PREFIX]}..."


# ── Individual checks ─────────────────────────────────────────────────────────

_OAUTH_CHECK_NAME = "OAuth App (client_id/secret)"
_APP_CHECK_NAME = "GitHub App (app_id + PEM)"
_CALLBACK_CHECK_NAME = "Callback URL"


def verify_oauth_pair(
    client_id: str, client_secret: str, *, http: HttpCaller
) -> CheckResult:
    """Probe the OAuth App ``client_id`` / ``client_secret`` pair.

    ``POST /applications/{client_id}/token`` with Basic auth = (id, secret) and a
    dummy access token: 404 => pair recognised (PASS), 401 => rejected (FAIL),
    422 => reachable but unprocessable (WARN).
    """
    if not client_id or not client_secret:
        return CheckResult(_OAUTH_CHECK_NAME, SKIP, None, "client_id/secret not set")

    masked = _mask_client_id(client_id)
    url = f"{_GITHUB_API}/applications/{client_id}/token"
    try:
        response = http(
            "POST",
            url,
            headers={"Accept": _GITHUB_JSON_ACCEPT},
            auth=(client_id, client_secret),
            json={"access_token": "verifier-probe-token-not-real"},
        )
    except Exception as error:
        return CheckResult(_OAUTH_CHECK_NAME, WARN, None, f"network error: {error}")

    if response.status_code == _HTTP_NOT_FOUND:
        return CheckResult(
            _OAUTH_CHECK_NAME,
            PASS,
            _HTTP_NOT_FOUND,
            f"pair recognized  client_id={masked}",
        )
    if response.status_code == _HTTP_UNAUTHORIZED:
        return CheckResult(
            _OAUTH_CHECK_NAME,
            FAIL,
            _HTTP_UNAUTHORIZED,
            f"client_id/secret rejected (Bad credentials)  client_id={masked}",
        )
    if response.status_code == _HTTP_UNPROCESSABLE:
        return CheckResult(
            _OAUTH_CHECK_NAME,
            WARN,
            _HTTP_UNPROCESSABLE,
            f"reachable but unprocessable  client_id={masked}",
        )
    return CheckResult(
        _OAUTH_CHECK_NAME,
        WARN,
        response.status_code,
        f"unexpected status  client_id={masked}",
    )


def _mint_app_jwt_or_none(app_id: str, private_key_pem: str, now: int):
    """Mint the App JWT, returning ``(token, None)`` or ``(None, CheckResult)``.

    A failing mint means a malformed PEM or app_id — a FAIL that never reaches
    the network and never echoes the key contents.
    """
    try:
        return GithubAppClient.mint_app_jwt(app_id, private_key_pem, now=now), None
    except Exception as error:
        return None, CheckResult(
            _APP_CHECK_NAME,
            FAIL,
            None,
            f"could not mint JWT from PEM ({type(error).__name__})",
        )


def verify_app_credentials(
    app_id: str, private_key_pem: str, *, http: HttpCaller, now: int
) -> CheckResult:
    """Probe the GitHub App ``app_id`` + PEM via a minted JWT and ``GET /app``.

    200 => valid (surfaces the app slug), 401 => bad app_id or PEM.
    """
    if not app_id or not private_key_pem:
        return CheckResult(_APP_CHECK_NAME, SKIP, None, "app_id/PEM not set")

    jwt_token, mint_failure = _mint_app_jwt_or_none(str(app_id), private_key_pem, now)
    if mint_failure is not None:
        return mint_failure

    try:
        response = http(
            "GET",
            f"{_GITHUB_API}/app",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": _GITHUB_JSON_ACCEPT,
            },
        )
    except Exception as error:
        return CheckResult(_APP_CHECK_NAME, WARN, None, f"network error: {error}")

    if response.status_code == _HTTP_OK:
        slug = response.body.get("slug", "?")
        return CheckResult(
            _APP_CHECK_NAME, PASS, _HTTP_OK, f"GET /app 200, slug={slug}"
        )
    if response.status_code == _HTTP_UNAUTHORIZED:
        return CheckResult(
            _APP_CHECK_NAME,
            FAIL,
            _HTTP_UNAUTHORIZED,
            "GET /app 401 (bad app_id or PEM)",
        )
    return CheckResult(
        _APP_CHECK_NAME,
        WARN,
        response.status_code,
        f"GET /app {response.status_code}",
    )


def verify_installation(
    app_id: str,
    private_key_pem: str,
    installation_id: str,
    *,
    http: HttpCaller,
    now: int,
) -> CheckResult:
    """Probe the installation id with the same App JWT (``GET /app/installations``).

    200 => PASS, 404 => FAIL; skipped cleanly when ``installation_id`` is unset.
    """
    name = f"Installation {installation_id}" if installation_id else "Installation"
    if not installation_id:
        return CheckResult("Installation", SKIP, None, "installation_id not set")
    if not app_id or not private_key_pem:
        return CheckResult(name, SKIP, None, "app_id/PEM not set")

    jwt_token, mint_failure = _mint_app_jwt_or_none(str(app_id), private_key_pem, now)
    if mint_failure is not None:
        return CheckResult(name, FAIL, None, "could not mint JWT from PEM")

    try:
        response = http(
            "GET",
            f"{_GITHUB_API}/app/installations/{installation_id}",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": _GITHUB_JSON_ACCEPT,
            },
        )
    except Exception as error:
        return CheckResult(name, WARN, None, f"network error: {error}")

    if response.status_code == _HTTP_OK:
        return CheckResult(name, PASS, _HTTP_OK, "installation found")
    if response.status_code == _HTTP_NOT_FOUND:
        return CheckResult(name, FAIL, _HTTP_NOT_FOUND, "installation not found")
    return CheckResult(
        name,
        WARN,
        response.status_code,
        f"unexpected status {response.status_code}",
    )


def verify_callback_url(redirect_uri: str) -> CheckResult:
    """Echo the configured OAuth callback URL (no GitHub-side verification).

    There is no owner-less API to read an OAuth App's registered callback, so we
    surface the configured value for a human to eyeball — status SKIP (never a
    pass/fail), so it never affects the run's exit code.
    """
    if not redirect_uri:
        return CheckResult(_CALLBACK_CHECK_NAME, SKIP, None, "redirect_uri not set")
    return CheckResult(_CALLBACK_CHECK_NAME, SKIP, None, redirect_uri)


def _load_private_key_pem(config: dict) -> str:
    """Read the PEM contents from the configured path (through the same confined
    secrets read production uses), returning ``""`` when absent/unreadable."""
    pem_path = config.get("github_app_private_key_path", "")
    if not pem_path:
        return ""
    try:
        from plugins.ghrm.src.routes import read_private_key_pem

        return read_private_key_pem(pem_path)
    except Exception:
        return ""


def verify_all(
    config: dict,
    *,
    http: Optional[HttpCaller] = None,
    now: Optional[int] = None,
) -> list:
    """Run every check against the ghrm config dict and return the results.

    Degrades gracefully: unset creds => SKIP, network error => WARN — never a
    traceback, so a firewalled box gives a clear signal.
    """
    caller: HttpCaller = http if http is not None else HttpxCaller()
    clock = now if now is not None else int(time.time())

    private_key_pem = _load_private_key_pem(config)
    return [
        verify_oauth_pair(
            config.get("github_oauth_client_id", ""),
            config.get("github_oauth_client_secret", ""),
            http=caller,
        ),
        verify_app_credentials(
            config.get("github_app_id", ""),
            private_key_pem,
            http=caller,
            now=clock,
        ),
        verify_installation(
            config.get("github_app_id", ""),
            private_key_pem,
            config.get("github_installation_id", ""),
            http=caller,
            now=clock,
        ),
        verify_callback_url(config.get("github_oauth_redirect_uri", "")),
    ]
