# Building a GitLab Provider Plugin for GHRM

This guide explains how to build `plugins/gitlab_ghrm/` — a drop-in GitLab backend for GHRM that gives subscribers read access to private GitLab repositories, just as the default GitHub App implementation does.

---

## How GHRM's provider abstraction works

Every operation GHRM needs from a Git host — granting repo access, revoking it, fetching content, handling OAuth — is defined in one abstract base class:

```python
# plugins/ghrm/src/services/github_app_client.py

class IGithubAppClient(ABC):
    @abstractmethod
    def add_collaborator(self, owner: str, repo: str, username: str, branch: str) -> bool: ...
    @abstractmethod
    def remove_collaborator(self, owner: str, repo: str, username: str) -> bool: ...
    @abstractmethod
    def create_deploy_token(self, owner: str, repo: str, username: str) -> str: ...
    @abstractmethod
    def revoke_deploy_token(self, token: str) -> None: ...
    @abstractmethod
    def get_installation_token(self, installation_id: str) -> str: ...
    @abstractmethod
    def exchange_oauth_code(self, code: str, client_id: str, client_secret: str, redirect_uri: str) -> str: ...
    @abstractmethod
    def get_oauth_user(self, oauth_token: str) -> dict: ...   # must return {"login": str, "id": ...}
    @abstractmethod
    def fetch_readme(self, owner: str, repo: str) -> str: ...
    @abstractmethod
    def fetch_changelog(self, owner: str, repo: str) -> Optional[str]: ...
    @abstractmethod
    def fetch_docs_readme(self, owner: str, repo: str) -> Optional[str]: ...
    @abstractmethod
    def fetch_releases(self, owner: str, repo: str) -> List[ReleaseDTO]: ...
    @abstractmethod
    def fetch_screenshot_urls(self, owner: str, repo: str) -> List[str]: ...
```

`ReleaseDTO` and `ReleaseAsset` dataclasses are in the same file. Import them from there.

The two GHRM services — `SoftwarePackageService` (content) and `GithubAccessService` (access) — are injected with an `IGithubAppClient` instance at construction time and never import any concrete implementation. Swapping GitHub for GitLab is purely a factory concern.

---

## Where the factory lives

`plugins/ghrm/src/routes.py` contains `_make_github_client(cfg)`. Currently it either returns a `MockGithubAppClient` (when `GHRM_USE_MOCK_GITHUB=true`) or the real `GithubAppClient`.

To add GitLab support, add a provider branch **before** the GitHub real-client path:

```python
# plugins/ghrm/src/routes.py  — _make_github_client()

def _make_github_client(cfg: dict) -> IGithubAppClient:
    import os
    if os.environ.get("GHRM_USE_MOCK_GITHUB", "").lower() == "true":
        from plugins.ghrm.src.services.github_app_client import MockGithubAppClient
        return MockGithubAppClient()

    # ── NEW: provider selection ─────────────────────────────────────────────
    provider = os.environ.get("GHRM_GIT_PROVIDER", "github").lower()
    if provider == "gitlab":
        from plugins.gitlab_ghrm.src.services.gitlab_client import GitLabClient
        return GitLabClient(
            base_url=os.environ.get("GITLAB_BASE_URL", "https://gitlab.com"),
            token=os.environ.get("GITLAB_TOKEN", ""),
            oauth_app_id=os.environ.get("GITLAB_OAUTH_APP_ID", ""),
            oauth_app_secret=os.environ.get("GITLAB_OAUTH_APP_SECRET", ""),
        )
    # ───────────────────────────────────────────────────────────────────────

    # existing GitHub App path
    app_id = cfg.get("github_app_id", "")
    ...
```

`.env` additions:

```
GHRM_GIT_PROVIDER=gitlab          # github | gitlab   (default: github)
GITLAB_BASE_URL=https://gitlab.com # or your self-hosted URL
GITLAB_TOKEN=glpat-xxxxxxxxxxxx    # Group/project PAT with api + read_repository scopes
GITLAB_OAUTH_APP_ID=xxxxxxxxxxxx
GITLAB_OAUTH_APP_SECRET=xxxxxxxxxxxx
```

---

## Plugin directory structure

```
plugins/gitlab_ghrm/
├── __init__.py                         # GitLabGhrmPlugin class
├── config.json                         # default config (base_url, scopes)
└── src/
    └── services/
        ├── __init__.py
        └── gitlab_client.py            # GitLabClient — implements IGithubAppClient
tests/
└── unit/
    └── services/
        └── test_gitlab_client.py
```

The plugin does **not** define any routes or models. It is a single class that satisfies the `IGithubAppClient` contract.

---

## `__init__.py`

```python
# plugins/gitlab_ghrm/__init__.py
from src.plugins.base import BasePlugin, PluginMetadata

class GitLabGhrmPlugin(BasePlugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="gitlab_ghrm",
            version="1.0.0",
            description="GitLab provider for GHRM repo access management",
            author="vbwd",
        )

    def get_blueprint(self):
        return None          # no routes — factory in ghrm routes.py does the wiring

    def get_url_prefix(self) -> str:
        return ""

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass
```

---

## Full `GitLabClient` implementation

```python
# plugins/gitlab_ghrm/src/services/gitlab_client.py
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Optional

import httpx

from plugins.ghrm.src.services.github_app_client import (
    IGithubAppClient, ReleaseDTO, ReleaseAsset,
)


class GitLabClient(IGithubAppClient):
    """
    GitLab implementation of IGithubAppClient.

    Terminology mapping:
      GitHub owner/repo  →  GitLab namespace/project  (URL-encoded as project path)
      GitHub collaborator →  GitLab project member     (access_level=30, Developer)
      GitHub deploy key  →  GitLab deploy token        (scope: read_repository)
      GitHub App token   →  GitLab PAT                 (static, returned as-is)
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        oauth_app_id: str = "",
        oauth_app_secret: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._oauth_app_id = oauth_app_id
        self._oauth_app_secret = oauth_app_secret

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"PRIVATE-TOKEN": self._token, "Content-Type": "application/json"}

    def _pid(self, owner: str, repo: str) -> str:
        """URL-encode the namespace/project path for use in GitLab API URLs."""
        return urllib.parse.quote(f"{owner}/{repo}", safe="")

    def _resolve_user_id(self, username: str) -> Optional[int]:
        resp = httpx.get(
            f"{self._base_url}/api/v4/users",
            params={"username": username},
            headers=self._headers(),
        )
        resp.raise_for_status()
        users = resp.json()
        return users[0]["id"] if users else None

    # ── Collaboration management ──────────────────────────────────────────────

    def add_collaborator(self, owner: str, repo: str, username: str, branch: str) -> bool:
        """Add a GitLab user as a Developer (access_level=30) on the project."""
        user_id = self._resolve_user_id(username)
        if user_id is None:
            return False
        resp = httpx.post(
            f"{self._base_url}/api/v4/projects/{self._pid(owner, repo)}/members",
            json={"user_id": user_id, "access_level": 30},
            headers=self._headers(),
        )
        # 409 = already a member — treat as success
        return resp.status_code in (200, 201, 409)

    def remove_collaborator(self, owner: str, repo: str, username: str) -> bool:
        """Remove a GitLab user from the project members list."""
        user_id = self._resolve_user_id(username)
        if user_id is None:
            return True   # user not found — nothing to remove
        resp = httpx.delete(
            f"{self._base_url}/api/v4/projects/{self._pid(owner, repo)}/members/{user_id}",
            headers=self._headers(),
        )
        return resp.status_code in (200, 204, 404)

    # ── Deploy tokens ─────────────────────────────────────────────────────────

    def create_deploy_token(self, owner: str, repo: str, username: str) -> str:
        """
        Create a GitLab deploy token scoped to read_repository.

        IMPORTANT: GitLab deploy token revocation requires the numeric token_id,
        not the token value. The token value is only returned at creation time.
        Callers that need to revoke later must store the token_id separately
        (e.g. in GhrmUserGithubAccess.provider_data as JSON: {"token_id": 123}).
        """
        url = f"{self._base_url}/api/v4/projects/{self._pid(owner, repo)}/deploy_tokens"
        resp = httpx.post(
            url,
            json={
                "name": f"vbwd-{username}-{int(time.time())}",
                "scopes": ["read_repository"],
                "expires_at": (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%d"),
            },
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        # data["id"]    → token_id  (store this for revocation)
        # data["token"] → token value (return this to the subscriber)
        return data["token"]

    def revoke_deploy_token(self, token: str) -> None:
        """
        Revoke a deploy token by its numeric ID.

        GitLab does not support lookup by token value — you must pass the
        token_id that was stored when create_deploy_token() was called.
        Pass the stored ID as the `token` argument:

            client.revoke_deploy_token(str(stored_token_id))

        If the ID is unknown, this is a no-op (logs a warning).
        """
        if not token or not token.isdigit():
            # token_id not available — skip silently
            # This happens when upgrading from a version that did not store token_id.
            return
        # token_id is stored per-project in provider_data — we need owner/repo context
        # but IGithubAppClient.revoke_deploy_token only receives the token string.
        # Convention: pass "<owner>/<repo>/<token_id>" as the token argument,
        # or store token_id in GhrmUserGithubAccess.provider_data and look it up
        # before calling this method.
        # Minimal fallback: parse "<project_path_encoded>/<token_id>" if colon-separated.
        if ":" in token:
            pid, token_id = token.rsplit(":", 1)
        else:
            return  # cannot revoke without project context
        resp = httpx.delete(
            f"{self._base_url}/api/v4/projects/{urllib.parse.quote(pid, safe='')}/deploy_tokens/{token_id}",
            headers=self._headers(),
        )
        # 404 = already revoked — not an error
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()

    # ── App installation token ────────────────────────────────────────────────

    def get_installation_token(self, installation_id: str) -> str:
        """
        GitLab uses a static PAT instead of short-lived installation tokens.
        Return the configured PAT directly.
        """
        return self._token

    # ── OAuth ─────────────────────────────────────────────────────────────────

    def exchange_oauth_code(
        self, code: str, client_id: str, client_secret: str, redirect_uri: str
    ) -> str:
        """Exchange a GitLab OAuth authorization code for an access token."""
        resp = httpx.post(
            f"{self._base_url}/oauth/token",
            json={
                "client_id": client_id or self._oauth_app_id,
                "client_secret": client_secret or self._oauth_app_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def get_oauth_user(self, oauth_token: str) -> dict:
        """
        Return the authenticated GitLab user.
        Maps GitLab's `username` field to the `login` key that GHRM expects.
        """
        resp = httpx.get(
            f"{self._base_url}/api/v4/user",
            headers={"Authorization": f"Bearer {oauth_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return {"login": data["username"], "id": data["id"]}

    # ── Content fetching ──────────────────────────────────────────────────────

    def fetch_readme(self, owner: str, repo: str) -> str:
        resp = httpx.get(
            f"{self._base_url}/api/v4/projects/{self._pid(owner, repo)}"
            f"/repository/files/README.md/raw",
            params={"ref": "main"},
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.text

    def fetch_changelog(self, owner: str, repo: str) -> Optional[str]:
        resp = httpx.get(
            f"{self._base_url}/api/v4/projects/{self._pid(owner, repo)}"
            f"/repository/files/CHANGELOG.md/raw",
            params={"ref": "main"},
            headers=self._headers(),
        )
        return resp.text if resp.status_code == 200 else None

    def fetch_docs_readme(self, owner: str, repo: str) -> Optional[str]:
        path = urllib.parse.quote("docs/README.md", safe="")
        resp = httpx.get(
            f"{self._base_url}/api/v4/projects/{self._pid(owner, repo)}"
            f"/repository/files/{path}/raw",
            params={"ref": "main"},
            headers=self._headers(),
        )
        return resp.text if resp.status_code == 200 else None

    def fetch_releases(self, owner: str, repo: str) -> List[ReleaseDTO]:
        resp = httpx.get(
            f"{self._base_url}/api/v4/projects/{self._pid(owner, repo)}/releases",
            headers=self._headers(),
        )
        resp.raise_for_status()
        results = []
        for r in resp.json():
            assets = [
                ReleaseAsset(name=a["name"], url=a["direct_asset_url"])
                for a in r.get("assets", {}).get("links", [])
            ]
            results.append(ReleaseDTO(
                tag=r["tag_name"],
                date=r["released_at"],
                notes=r.get("description", ""),
                assets=assets,
            ))
        return results

    def fetch_screenshot_urls(self, owner: str, repo: str) -> List[str]:
        resp = httpx.get(
            f"{self._base_url}/api/v4/projects/{self._pid(owner, repo)}/repository/tree",
            params={"path": "screenshots", "ref": "main"},
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return []
        base = f"{self._base_url}/{owner}/{repo}/-/raw/main/screenshots"
        return [
            f"{base}/{f['name']}"
            for f in resp.json()
            if f["type"] == "blob"
        ]
```

---

## Key differences from GitHub

### Project path vs owner/repo

GitLab identifies projects by a URL-encoded `namespace/project` string or by a numeric ID. The GHRM interface passes `owner` and `repo` as separate strings — combine and encode them:

```python
def _pid(self, owner: str, repo: str) -> str:
    return urllib.parse.quote(f"{owner}/{repo}", safe="")
    # "mygroup/myproject" → "mygroup%2Fmyproject"
    # Used as: /api/v4/projects/mygroup%2Fmyproject/...
```

In the admin UI, the `github_owner` field stores the GitLab group/namespace and `github_repo` stores the project name. No schema changes needed.

### Deploy token revocation

GitLab deploy token revocation requires the numeric token ID (`/deploy_tokens/{id}`), not the token value. The token value is only returned at creation time and cannot be looked up later.

**Recommended approach**: when storing the deploy token in `GhrmUserGithubAccess`, save the token_id alongside it. The model has a `provider_data` JSON column for exactly this:

```python
# After calling create_deploy_token(), store the ID separately
# In GithubAccessService.handle_oauth_callback():
token_value = github.create_deploy_token(owner, repo, username)
# For GitLab, also fetch token_id from the last API call response and store:
# access_record.provider_data = {"gitlab_deploy_token_id": token_id}
```

Pass the stored ID when revoking: `client.revoke_deploy_token(f"{owner}/{repo}:{token_id}")`.

### OAuth authorization URL

GitLab OAuth requires `read_user` scope (equivalent to GitHub's `read:user`).

Authorization URL to redirect users to:

```
https://gitlab.com/oauth/authorize
  ?client_id=<GITLAB_OAUTH_APP_ID>
  &response_type=code
  &redirect_uri=<redirect_uri>
  &scope=read_user
```

For self-hosted instances replace `gitlab.com` with `GITLAB_BASE_URL`.

The token exchange endpoint is `POST {base_url}/oauth/token` with `grant_type=authorization_code`.

### Member access levels

| GitLab access_level | Meaning | GHRM use |
|---------------------|---------|----------|
| 10 | Guest | — |
| 20 | Reporter | — |
| **30** | **Developer** | Grant on subscribe |
| 40 | Maintainer | — |
| 50 | Owner | — |

Developer (30) gives read + clone access. This is the equivalent of GitHub's collaborator with read permission.

### `get_installation_token`

GitHub App authentication generates short-lived installation tokens for each API call. GitLab uses a static Personal Access Token — return `self._token` directly:

```python
def get_installation_token(self, installation_id: str) -> str:
    return self._token
```

---

## Testing

### Unit tests

```python
# plugins/gitlab_ghrm/tests/unit/services/test_gitlab_client.py
from unittest.mock import patch, MagicMock
from plugins.gitlab_ghrm.src.services.gitlab_client import GitLabClient


def _client():
    return GitLabClient(
        base_url="https://gitlab.com",
        token="test-token",
        oauth_app_id="app-id",
        oauth_app_secret="app-secret",
    )


class TestAddCollaborator:
    def test_adds_member_successfully(self):
        client = _client()
        with patch("httpx.get") as mock_get, patch("httpx.post") as mock_post:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: [{"id": 42}])
            mock_post.return_value = MagicMock(status_code=201)
            assert client.add_collaborator("myorg", "myrepo", "alice", "main") is True

    def test_returns_true_when_already_member(self):
        client = _client()
        with patch("httpx.get") as mock_get, patch("httpx.post") as mock_post:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: [{"id": 42}])
            mock_post.return_value = MagicMock(status_code=409)
            assert client.add_collaborator("myorg", "myrepo", "alice", "main") is True

    def test_returns_false_when_user_not_found(self):
        client = _client()
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: [])
            assert client.add_collaborator("myorg", "myrepo", "ghost", "main") is False


class TestGetOauthUser:
    def test_maps_username_to_login(self):
        client = _client()
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"username": "alice", "id": 99},
            )
            result = client.get_oauth_user("some-token")
        assert result == {"login": "alice", "id": 99}


class TestFetchReleases:
    def test_maps_gitlab_releases_to_dto(self):
        client = _client()
        with patch("httpx.get") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: [{
                    "tag_name": "v1.0.0",
                    "released_at": "2026-01-01T00:00:00Z",
                    "description": "First release",
                    "assets": {"links": [{"name": "dist.zip", "direct_asset_url": "https://..."}]},
                }],
            )
            releases = client.fetch_releases("myorg", "myrepo")
        assert len(releases) == 1
        assert releases[0].tag == "v1.0.0"
        assert releases[0].assets[0].name == "dist.zip"
```

### Mock client for GHRM service tests

When testing GHRM services (`GithubAccessService`, `SoftwarePackageService`) with a GitLab backend, use the existing `MockGithubAppClient` — it satisfies the full interface and is provider-agnostic:

```python
from plugins.ghrm.src.services.github_app_client import MockGithubAppClient

mock = MockGithubAppClient()
mock.oauth_user_map["my-token"] = {"login": "alice", "id": 42}
svc = GithubAccessService(access_repo=..., github=mock, ...)
```

---

## OAuth setup on gitlab.com

1. Go to **User Settings → Applications** (or **Admin Area → Applications** for system-wide)
2. Create a new application:
   - **Name:** vbwd
   - **Redirect URI:** `https://yourdomain.com/ghrm/oauth/callback`
   - **Scopes:** `read_user`
3. Copy **Application ID** → `GITLAB_OAUTH_APP_ID`
4. Copy **Secret** → `GITLAB_OAUTH_APP_SECRET`

For self-hosted GitLab, set `GITLAB_BASE_URL=https://your-gitlab.example.com`.

---

## OAuth setup for self-hosted GitLab

Same steps, but the OAuth authorize URL becomes:

```
https://your-gitlab.example.com/oauth/authorize?client_id=...&scope=read_user&...
```

No code changes needed — `GitLabClient` uses `self._base_url` everywhere.

---

## Registration

**`plugins/plugins.json`:**
```json
{ "name": "gitlab_ghrm", "enabled": false }
```

Disabled by default. Enable only when `GHRM_GIT_PROVIDER=gitlab`.

**`plugins/config.json`** additions:
```json
"gitlab_ghrm": {
  "base_url": "https://gitlab.com",
  "default_branch": "main"
}
```

---

## Checklist

- [ ] Create `plugins/gitlab_ghrm/` directory structure
- [ ] Implement all 12 abstract methods of `IGithubAppClient`
- [ ] Add `GHRM_GIT_PROVIDER` branch to `_make_github_client()` in `plugins/ghrm/src/routes.py`
- [ ] Add `GHRM_GIT_PROVIDER`, `GITLAB_*` vars to `.env.example`
- [ ] Store `token_id` in `GhrmUserGithubAccess.provider_data` on `create_deploy_token`
- [ ] Update `GithubAccessService.disconnect_github()` to pass `<project>:<token_id>` to `revoke_deploy_token`
- [ ] Write unit tests for all `GitLabClient` methods (mocked `httpx`)
- [ ] Register plugin in `plugins/plugins.json` (disabled by default)
- [ ] Test OAuth flow end-to-end against a real GitLab instance with `GHRM_GIT_PROVIDER=gitlab`
- [ ] Document OAuth app setup URL in project README
