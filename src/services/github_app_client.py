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
    date: str  # ISO format
    notes: str
    assets: List[ReleaseAsset] = field(default_factory=list)


@dataclass
class AddCollaboratorResult:
    """Outcome of a ``PUT .../collaborators/<user>`` call.

    GitHub returns ``201`` with an invitation payload when the user is an
    *outside* collaborator (membership is pending until accepted), and ``204``
    when the user is already a member (no invitation). ``state`` distinguishes
    the two; ``invitation_id`` is the pending invitation's id (``None`` when
    already active).
    """

    state: str  # "invited" | "active"
    invitation_id: Optional[str] = None


class IGithubAppClient(ABC):
    """Interface for all GitHub API operations used by GHRM."""

    @abstractmethod
    def add_collaborator(
        self, owner: str, repo: str, username: str, permission: str = "pull"
    ) -> AddCollaboratorResult:
        """Invite/add a collaborator. 201 -> invited, 204 -> active; else raise."""
        ...

    @abstractmethod
    def remove_collaborator(self, owner: str, repo: str, username: str) -> bool:
        ...

    @abstractmethod
    def is_collaborator(self, owner: str, repo: str, username: str) -> bool:
        """True when the user is an accepted collaborator (GET 204), else False (404)."""
        ...

    @abstractmethod
    def list_repo_invitations(self, owner: str, repo: str) -> List[dict]:
        ...

    @abstractmethod
    def cancel_invitation(self, owner: str, repo: str, invitation_id: str) -> None:
        ...

    @abstractmethod
    def get_installation_token(self, installation_id: str) -> str:
        ...

    @abstractmethod
    def exchange_oauth_code(
        self, code: str, client_id: str, client_secret: str, redirect_uri: str
    ) -> str:
        """Exchange OAuth code for access token. Returns the token string."""
        ...

    @abstractmethod
    def get_oauth_user(self, oauth_token: str) -> dict:
        """GET api.github.com/user. Returns dict with 'login' and 'id'."""
        ...

    @abstractmethod
    def fetch_readme(self, owner: str, repo: str) -> str:
        ...

    @abstractmethod
    def fetch_changelog(self, owner: str, repo: str) -> Optional[str]:
        ...

    @abstractmethod
    def fetch_docs_readme(self, owner: str, repo: str) -> Optional[str]:
        ...

    @abstractmethod
    def fetch_releases(self, owner: str, repo: str) -> List[ReleaseDTO]:
        ...

    @abstractmethod
    def fetch_screenshot_urls(self, owner: str, repo: str) -> List[str]:
        ...


class MockGithubAppClient(IGithubAppClient):
    """
    Test double for IGithubAppClient.
    All methods are configurable via attributes set in tests.
    Satisfies full Liskov substitution — identical signatures, same exception types.

    Invitation model: ``add_collaborator`` returns ``invited`` by default
    (mirroring GitHub's 201 for an outside collaborator) unless the
    ``(owner, repo, username)`` triple is in ``members_already`` (mirroring
    204 for an existing member). ``is_collaborator`` returns True only when the
    triple is in ``accepted`` — letting a test simulate the user accepting an
    invitation.
    """

    def __init__(self):
        self.collaborators: dict = {}  # (owner, repo) -> set of usernames
        self.revoked_tokens: list = (
            []
        )  # retained so callers asserting "no rotation" work
        self.oauth_token_map: dict = {}  # code -> token
        self.oauth_user_map: dict = {}  # token -> {"login": ..., "id": ...}
        self.readme_content: str = "# Mock README"
        self.changelog_content: Optional[str] = "# Mock Changelog"
        self.docs_content: Optional[str] = "# Mock Docs"
        self.releases: List[ReleaseDTO] = []
        self.screenshot_urls: List[str] = []
        self.raise_on_add_collaborator: Optional[Exception] = None
        self.raise_on_exchange: Optional[Exception] = None
        # (owner, repo, username) triples that simulate "already a member" (204).
        self.members_already: set = set()
        # (owner, repo, username) triples that simulate an accepted invitation.
        self.accepted: set = set()
        # (owner, repo) -> list of {"id": ...} pending invitations.
        self.invitations: dict = {}
        self._next_invitation_id: int = 1000

    def add_collaborator(
        self, owner: str, repo: str, username: str, permission: str = "pull"
    ) -> AddCollaboratorResult:
        if self.raise_on_add_collaborator:
            raise self.raise_on_add_collaborator
        key = (owner, repo)
        self.collaborators.setdefault(key, set()).add(username)
        if (owner, repo, username) in self.members_already:
            return AddCollaboratorResult(state="active", invitation_id=None)
        invitation_id = str(self._next_invitation_id)
        self._next_invitation_id += 1
        self.invitations.setdefault(key, []).append(
            {"id": int(invitation_id), "invitee": {"login": username}}
        )
        return AddCollaboratorResult(state="invited", invitation_id=invitation_id)

    def remove_collaborator(self, owner: str, repo: str, username: str) -> bool:
        key = (owner, repo)
        self.collaborators.get(key, set()).discard(username)
        return True

    def is_collaborator(self, owner: str, repo: str, username: str) -> bool:
        return (owner, repo, username) in self.accepted

    def list_repo_invitations(self, owner: str, repo: str) -> List[dict]:
        return list(self.invitations.get((owner, repo), []))

    def cancel_invitation(self, owner: str, repo: str, invitation_id: str) -> None:
        key = (owner, repo)
        self.invitations[key] = [
            item
            for item in self.invitations.get(key, [])
            if str(item["id"]) != str(invitation_id)
        ]

    def get_installation_token(self, installation_id: str) -> str:
        return f"mock-installation-token-{installation_id}"

    def exchange_oauth_code(
        self, code: str, client_id: str, client_secret: str, redirect_uri: str
    ) -> str:
        if self.raise_on_exchange:
            raise self.raise_on_exchange
        return self.oauth_token_map.get(code, f"mock-oauth-token-{code}")

    def get_oauth_user(self, oauth_token: str) -> dict:
        """Return the GitHub identity for an OAuth token.

        TEST FIXTURE: the hard-coded ``{"login": "testuser", "id": "12345"}``
        default is returned when a test does not register a token in
        ``oauth_user_map``. This constant default is exactly what masked the
        original live collaborator bug — production never sees it; always set
        ``oauth_user_map`` in tests that assert on the resolved username.
        """
        return self.oauth_user_map.get(
            oauth_token, {"login": "testuser", "id": "12345"}
        )

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
