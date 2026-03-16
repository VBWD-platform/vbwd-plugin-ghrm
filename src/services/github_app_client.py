"""IGithubAppClient — interface and mock implementation for GitHub API operations."""
from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass, field


@dataclass
class ReleaseAsset:
    name: str
    url: str


@dataclass
class ReleaseDTO:
    tag: str
    date: str          # ISO format
    notes: str
    assets: List[ReleaseAsset] = field(default_factory=list)


class IGithubAppClient(ABC):
    """Interface for all GitHub API operations used by GHRM."""

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
    def exchange_oauth_code(self, code: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
        """Exchange OAuth code for access token. Returns the token string."""
        ...

    @abstractmethod
    def get_oauth_user(self, oauth_token: str) -> dict:
        """GET api.github.com/user. Returns dict with 'login' and 'id'."""
        ...

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


class MockGithubAppClient(IGithubAppClient):
    """
    Test double for IGithubAppClient.
    All methods are configurable via attributes set in tests.
    Satisfies full Liskov substitution — identical signatures, same exception types.
    """

    def __init__(self):
        self.collaborators: dict = {}           # (owner, repo) -> set of usernames
        self.deploy_tokens: dict = {}           # username -> token
        self.revoked_tokens: list = []
        self.oauth_token_map: dict = {}         # code -> token
        self.oauth_user_map: dict = {}          # token -> {"login": ..., "id": ...}
        self.readme_content: str = "# Mock README"
        self.changelog_content: Optional[str] = "# Mock Changelog"
        self.docs_content: Optional[str] = "# Mock Docs"
        self.releases: List[ReleaseDTO] = []
        self.screenshot_urls: List[str] = []
        self.raise_on_add_collaborator: Optional[Exception] = None
        self.raise_on_exchange: Optional[Exception] = None

    def add_collaborator(self, owner: str, repo: str, username: str, branch: str) -> bool:
        if self.raise_on_add_collaborator:
            raise self.raise_on_add_collaborator
        key = (owner, repo)
        self.collaborators.setdefault(key, set()).add(username)
        return True

    def remove_collaborator(self, owner: str, repo: str, username: str) -> bool:
        key = (owner, repo)
        self.collaborators.get(key, set()).discard(username)
        return True

    def create_deploy_token(self, owner: str, repo: str, username: str) -> str:
        token = f"mock-token-{username}"
        self.deploy_tokens[username] = token
        return token

    def revoke_deploy_token(self, token: str) -> None:
        self.revoked_tokens.append(token)

    def get_installation_token(self, installation_id: str) -> str:
        return f"mock-installation-token-{installation_id}"

    def exchange_oauth_code(self, code: str, client_id: str, client_secret: str, redirect_uri: str) -> str:
        if self.raise_on_exchange:
            raise self.raise_on_exchange
        return self.oauth_token_map.get(code, f"mock-oauth-token-{code}")

    def get_oauth_user(self, oauth_token: str) -> dict:
        return self.oauth_user_map.get(oauth_token, {"login": "testuser", "id": "12345"})

    def fetch_readme(self, owner: str, repo: str) -> str:
        return self.readme_content

    def fetch_changelog(self, owner: str, repo: str) -> Optional[str]:
        return self.changelog_content

    def fetch_docs_readme(self, owner: str, repo: str) -> Optional[str]:
        return self.docs_content

    def fetch_releases(self, owner: str, repo: str) -> List[ReleaseDTO]:
        return self.releases

    def fetch_screenshot_urls(self, owner: str, repo: str) -> List[str]:
        return self.screenshot_urls
