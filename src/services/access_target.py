"""AccessTarget — the polymorphic grant primitive behind a GHRM package (S132).

The single Open/Closed seam the access service loops over: a package resolves to
a list of ``AccessTarget`` value objects, each of which knows how to ``grant`` /
``revoke`` / ``is_active`` for one developer. The service never branches on the
package's ``access_kind`` — adding a future kind (e.g. ``org``) is one new
subclass and a new branch in ``access_targets_for_package``, zero service edits.

``RepoTarget`` wraps today's per-repo collaborator calls verbatim (the
"99 invitations" behaviour). ``TeamTarget`` wraps the org/team membership calls:
adding a developer to a team is instant and needs at most one org invitation
(the first join), which is the whole point of the sprint. GitHub team membership
takes a team ROLE, not a repo permission, so ``TeamTarget.grant`` ignores the
``permission`` argument (repo access is configured on the team itself, out of
band).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

from plugins.ghrm.src.models.ghrm_software_package import DEFAULT_ACCESS_KIND
from plugins.ghrm.src.services.github_app_client import IGithubAppClient


@dataclass(frozen=True)
class GrantResult:
    """Outcome of a single target grant, in membership vocabulary.

    ``state`` is ``"active"`` when the developer already has live access, or
    ``"invited"`` when the grant left a pending acceptance (an outside-repo
    invitation, or a pending team/org join). ``invitation_id`` is the pending
    repo invitation's id when applicable (``None`` for team grants — team
    membership carries no per-team invitation id).
    """

    state: str  # "active" | "invited"
    invitation_id: Optional[str] = None


class AccessTarget(ABC):
    """One grantable access primitive for a (user, package) pair."""

    # The client operation name + a human location, used only to compose a
    # single, accurate grant-failure log line in the service (kept per-kind so
    # the message names the real GitHub call).
    grant_operation: str = "grant"

    @abstractmethod
    def descriptor(self) -> dict:
        """The persisted grant descriptor (``kind`` + identity), sans status."""
        ...

    @abstractmethod
    def key(self) -> Tuple[str, ...]:
        """Stable identity used for the D6 'still entitled elsewhere' guard."""
        ...

    @abstractmethod
    def location(self) -> str:
        """Human location for log lines (e.g. ``acme/repo`` or ``org/team``)."""
        ...

    @abstractmethod
    def grant(
        self, client: IGithubAppClient, username: str, permission: str
    ) -> GrantResult:
        ...

    @abstractmethod
    def revoke(
        self,
        client: IGithubAppClient,
        username: str,
        grant_state: Optional[str],
        invitation_id: Optional[str],
    ) -> None:
        ...

    @abstractmethod
    def is_active(self, client: IGithubAppClient, username: str) -> bool:
        ...


@dataclass(frozen=True)
class RepoTarget(AccessTarget):
    """Per-repo outside-collaborator grant — today's behaviour, byte-for-byte."""

    owner: str
    repo: str
    grant_operation: str = "add_collaborator"

    def descriptor(self) -> dict:
        return {"kind": "repo", "owner": self.owner, "repo": self.repo}

    def key(self) -> Tuple[str, ...]:
        return ("repo", self.owner, self.repo)

    def location(self) -> str:
        return f"{self.owner}/{self.repo}"

    def grant(
        self, client: IGithubAppClient, username: str, permission: str
    ) -> GrantResult:
        result = client.add_collaborator(self.owner, self.repo, username, permission)
        return GrantResult(state=result.state, invitation_id=result.invitation_id)

    def revoke(
        self,
        client: IGithubAppClient,
        username: str,
        grant_state: Optional[str],
        invitation_id: Optional[str],
    ) -> None:
        if grant_state == "invited" and invitation_id:
            client.cancel_invitation(self.owner, self.repo, invitation_id)
        else:
            client.remove_collaborator(self.owner, self.repo, username)

    def is_active(self, client: IGithubAppClient, username: str) -> bool:
        return client.is_collaborator(self.owner, self.repo, username)


@dataclass(frozen=True)
class TeamTarget(AccessTarget):
    """Team-membership grant — instant, at most one org invitation total."""

    org: str
    team_slug: str
    grant_operation: str = "add_team_membership"

    def descriptor(self) -> dict:
        return {"kind": "team", "org": self.org, "team_slug": self.team_slug}

    def key(self) -> Tuple[str, ...]:
        return ("team", self.org, self.team_slug)

    def location(self) -> str:
        return f"{self.org}/{self.team_slug}"

    def grant(
        self, client: IGithubAppClient, username: str, permission: str
    ) -> GrantResult:
        # ``permission`` is a repo permission and is irrelevant for a team grant:
        # repo access is configured on the team itself (out of band). Team
        # membership takes a team role, which GHRM does not manage here.
        result = client.add_team_membership(self.org, self.team_slug, username)
        state = "active" if result.state == "active" else "invited"
        return GrantResult(state=state, invitation_id=None)

    def revoke(
        self,
        client: IGithubAppClient,
        username: str,
        grant_state: Optional[str],
        invitation_id: Optional[str],
    ) -> None:
        client.remove_team_membership(self.org, self.team_slug, username)

    def is_active(self, client: IGithubAppClient, username: str) -> bool:
        return (
            client.get_team_membership(self.org, self.team_slug, username) == "active"
        )


def access_targets_for_package(package) -> List[AccessTarget]:
    """Resolve a package's access targets (the single home for the mapping).

    ``team`` -> one ``TeamTarget``; anything else (``repo``, the default, plus
    any legacy/None value) -> a ``RepoTarget`` per ``repo_targets()`` pair, so
    single and bundle packages keep today's per-repo behaviour unchanged.
    """
    kind = getattr(package, "access_kind", DEFAULT_ACCESS_KIND) or DEFAULT_ACCESS_KIND
    if kind == "team":
        return [TeamTarget(package.github_org, package.github_team_slug)]
    return [RepoTarget(owner, repo) for owner, repo in package.repo_targets()]


def target_from_grant(entry: dict) -> AccessTarget:
    """Reconstruct an ``AccessTarget`` from a persisted grant entry.

    Legacy entries written before S132 have no ``"kind"`` key and are treated as
    ``repo`` (backward-compatible read).
    """
    kind = entry.get("kind", "repo")
    if kind == "team":
        return TeamTarget(entry["org"], entry["team_slug"])
    return RepoTarget(entry["owner"], entry["repo"])
