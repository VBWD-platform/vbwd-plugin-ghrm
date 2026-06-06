"""GithubAppClient — real implementation using PyGithub and httpx."""
import time
import httpx
import jwt as pyjwt
from typing import List, Optional
from plugins.ghrm.src.services.github_app_client import (
    AddCollaboratorResult,
    IGithubAppClient,
    ReleaseDTO,
    ReleaseAsset,
)


class GithubAppClientError(Exception):
    """Raised when a GitHub API call fails."""


class GithubAppClient(IGithubAppClient):
    """
    Real GitHub API client using httpx.
    Uses GitHub App installation token for repo operations,
    and OAuth token exchange for user identity.
    """

    GITHUB_API = "https://api.github.com"
    GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"

    def __init__(
        self,
        app_id: str,
        private_key: str,
        installation_id: str,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        """
        Args:
            app_id: GitHub App numeric ID.
            private_key: PEM-encoded RSA private key content (not a file path).
            installation_id: GitHub App installation ID for this org/account.
            transport: optional httpx transport, injected by contract tests so
                the HTTP layer is exercised offline (no real network).
        """
        self._app_id = app_id
        self._private_key = private_key
        self._installation_id = installation_id
        self._installation_token: str = ""
        self._transport = transport

    def _client(self) -> httpx.Client:
        """Return an httpx client, honouring an injected transport for tests."""
        if self._transport is not None:
            return httpx.Client(timeout=10, transport=self._transport)
        return httpx.Client(timeout=10)

    def _make_jwt(self) -> str:
        """Generate a short-lived JWT signed with the GitHub App private key."""
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 540, "iss": self._app_id}
        return pyjwt.encode(payload, self._private_key, algorithm="RS256")

    def _ensure_installation_token(self) -> None:
        """Fetch a fresh installation token if not already set."""
        if not self._installation_token:
            self._installation_token = self.get_installation_token(
                self._installation_id
            )

    def set_installation_token(self, token: str) -> None:
        """Update the installation token (e.g. after refresh)."""
        self._installation_token = token

    def _repo_headers(self) -> dict:
        self._ensure_installation_token()
        return {
            "Authorization": f"Bearer {self._installation_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _user_headers(self, oauth_token: str) -> dict:
        return {
            "Authorization": f"Bearer {oauth_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def add_collaborator(
        self, owner: str, repo: str, username: str, permission: str = "pull"
    ) -> AddCollaboratorResult:
        """Invite/add a collaborator.

        ``201`` -> the user is an outside collaborator and now has a pending
        invitation (captured as ``invitation_id``); ``204`` -> the user was
        already a member (active). Any other status raises.
        """
        url = f"{self.GITHUB_API}/repos/{owner}/{repo}/collaborators/{username}"
        with self._client() as client:
            resp = client.put(
                url, headers=self._repo_headers(), json={"permission": permission}
            )
        if resp.status_code == 201:
            invitation_id = str(resp.json().get("id"))
            return AddCollaboratorResult(state="invited", invitation_id=invitation_id)
        if resp.status_code == 204:
            return AddCollaboratorResult(state="active", invitation_id=None)
        raise GithubAppClientError(
            f"add_collaborator failed: {resp.status_code} {resp.text}"
        )

    def remove_collaborator(self, owner: str, repo: str, username: str) -> bool:
        """Remove user as collaborator."""
        url = f"{self.GITHUB_API}/repos/{owner}/{repo}/collaborators/{username}"
        with self._client() as client:
            resp = client.delete(url, headers=self._repo_headers())
        if resp.status_code not in (204, 404):
            raise GithubAppClientError(
                f"remove_collaborator failed: {resp.status_code} {resp.text}"
            )
        return True

    def is_collaborator(self, owner: str, repo: str, username: str) -> bool:
        """True when the user is an accepted collaborator (204), False on 404."""
        url = f"{self.GITHUB_API}/repos/{owner}/{repo}/collaborators/{username}"
        with self._client() as client:
            resp = client.get(url, headers=self._repo_headers())
        if resp.status_code == 204:
            return True
        if resp.status_code == 404:
            return False
        raise GithubAppClientError(
            f"is_collaborator failed: {resp.status_code} {resp.text}"
        )

    def list_repo_invitations(self, owner: str, repo: str) -> List[dict]:
        """Return the repo's pending collaborator invitations."""
        url = f"{self.GITHUB_API}/repos/{owner}/{repo}/invitations"
        with self._client() as client:
            resp = client.get(url, headers=self._repo_headers())
        if resp.status_code != 200:
            raise GithubAppClientError(
                f"list_repo_invitations failed: {resp.status_code} {resp.text}"
            )
        return resp.json()

    def cancel_invitation(self, owner: str, repo: str, invitation_id: str) -> None:
        """Cancel a pending invitation; 204 (cancelled) and 404 (gone) are ok."""
        url = f"{self.GITHUB_API}/repos/{owner}/{repo}/invitations/{invitation_id}"
        with self._client() as client:
            resp = client.delete(url, headers=self._repo_headers())
        if resp.status_code not in (204, 404):
            raise GithubAppClientError(
                f"cancel_invitation failed: {resp.status_code} {resp.text}"
            )

    def get_installation_token(self, installation_id: str) -> str:
        """Get a fresh installation token from GitHub App using a signed JWT."""
        url = f"{self.GITHUB_API}/app/installations/{installation_id}/access_tokens"
        jwt_headers = {
            "Authorization": f"Bearer {self._make_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, headers=jwt_headers)
        if resp.status_code != 201:
            raise GithubAppClientError(
                f"get_installation_token failed: {resp.status_code}"
            )
        return resp.json()["token"]

    def exchange_oauth_code(
        self, code: str, client_id: str, client_secret: str, redirect_uri: str
    ) -> str:
        """Exchange OAuth code for access token."""
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                self.GITHUB_OAUTH_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
        if resp.status_code != 200:
            raise GithubAppClientError(f"OAuth exchange failed: {resp.status_code}")
        data = resp.json()
        if "error" in data:
            raise GithubAppClientError(
                f"OAuth error: {data.get('error_description', data['error'])}"
            )
        return data["access_token"]

    def get_oauth_user(self, oauth_token: str) -> dict:
        """GET /user — returns dict with 'login' and 'id'."""
        url = f"{self.GITHUB_API}/user"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, headers=self._user_headers(oauth_token))
        if resp.status_code != 200:
            raise GithubAppClientError(f"get_oauth_user failed: {resp.status_code}")
        data = resp.json()
        return {"login": data["login"], "id": str(data["id"])}

    def fetch_readme(self, owner: str, repo: str) -> str:
        """Fetch README.md content (decoded)."""
        return self._fetch_file_content(owner, repo, "README.md")

    def fetch_changelog(self, owner: str, repo: str) -> Optional[str]:
        """Fetch CHANGELOG.md if it exists."""
        try:
            return self._fetch_file_content(owner, repo, "CHANGELOG.md")
        except GithubAppClientError:
            return None

    def fetch_docs_readme(self, owner: str, repo: str) -> Optional[str]:
        """Fetch docs/README.md if it exists."""
        try:
            return self._fetch_file_content(owner, repo, "docs/README.md")
        except GithubAppClientError:
            return None

    def fetch_releases(self, owner: str, repo: str) -> List[ReleaseDTO]:
        """Fetch all releases ordered newest first."""
        url = f"{self.GITHUB_API}/repos/{owner}/{repo}/releases"
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                url, headers=self._repo_headers(), params={"per_page": 50}
            )
        if resp.status_code != 200:
            raise GithubAppClientError(f"fetch_releases failed: {resp.status_code}")
        releases = []
        for r in resp.json():
            assets = [
                ReleaseAsset(name=a["name"], url=a["browser_download_url"])
                for a in r.get("assets", [])
            ]
            releases.append(
                ReleaseDTO(
                    tag=r["tag_name"],
                    date=r["published_at"] or r["created_at"],
                    notes=r.get("body") or "",
                    assets=assets,
                )
            )
        return releases

    def fetch_screenshot_urls(self, owner: str, repo: str) -> List[str]:
        """List files in docs/screenshots/ and return their raw URLs."""
        url = f"{self.GITHUB_API}/repos/{owner}/{repo}/contents/docs/screenshots"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, headers=self._repo_headers())
        if resp.status_code == 404:
            return []
        if resp.status_code != 200:
            raise GithubAppClientError(
                f"fetch_screenshot_urls failed: {resp.status_code}"
            )
        return [f["download_url"] for f in resp.json() if f.get("type") == "file"]

    def _fetch_file_content(self, owner: str, repo: str, path: str) -> str:
        """Fetch a file from the repo and return decoded text content."""
        import base64

        url = f"{self.GITHUB_API}/repos/{owner}/{repo}/contents/{path}"
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, headers=self._repo_headers())
        if resp.status_code == 404:
            raise GithubAppClientError(f"File not found: {path}")
        if resp.status_code != 200:
            raise GithubAppClientError(
                f"fetch_file_content({path}) failed: {resp.status_code}"
            )
        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8")
        return content
