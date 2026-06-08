"""GithubAccessService — entitlement-scoped, per-(user, package) collaborator lifecycle.

Grant/revoke is scoped to the user's *actual* entitlements (resolved through the
ghrm-owned ``ISubscriptionEntitlements`` port — S49.0), tracked per
(user, package) in ``GhrmRepoMembership`` with the GitHub invitation model
(INVITED -> ACTIVE), and failures are surfaced as ERROR (no silent swallow).
"""
import logging
from datetime import timedelta
from typing import Any, Dict, Optional, cast
from uuid import UUID

from vbwd.utils.datetime_utils import utcnow

from plugins.ghrm.src.models.ghrm_user_github_access import GhrmUserGithubAccess
from plugins.ghrm.src.models.ghrm_repo_membership import MembershipStatus
from plugins.ghrm.src.models.ghrm_software_package import (
    resolve_effective_permission,
)
from plugins.ghrm.src.models.ghrm_access_log import SyncAction
from plugins.ghrm.src.repositories.user_github_access_repository import (
    GhrmUserGithubAccessRepository,
)
from plugins.ghrm.src.repositories.repo_membership_repository import (
    GhrmRepoMembershipRepository,
)
from plugins.ghrm.src.repositories.access_log_repository import GhrmAccessLogRepository
from plugins.ghrm.src.repositories.software_package_repository import (
    GhrmSoftwarePackageRepository,
)
from plugins.ghrm.src.services.github_app_client import IGithubAppClient
from plugins.ghrm.src.services.github_app_client_real import GithubAppClientError
from plugins.ghrm.src.services.ports import ISubscriptionEntitlements

logger = logging.getLogger(__name__)

# The granted GitHub permission is configurable per package (S51) — see
# ``GhrmSoftwarePackage.collaborator_permission`` — but is clamped to read-only
# ("pull") whenever ``allow_extensive_permissions`` is off (D3 guardrail). The
# single decision home is ``resolve_effective_permission``.
OAUTH_SCOPE = "read:user"


class GhrmGithubNotConnectedError(Exception):
    """Raised when an operation requires a connected GitHub account."""


class GhrmOAuthError(Exception):
    """Raised when GitHub OAuth exchange fails."""


class GithubAccessService:
    """OAuth identity + per-(user, package) collaborator lifecycle."""

    def __init__(
        self,
        access_repo: GhrmUserGithubAccessRepository,
        membership_repo: GhrmRepoMembershipRepository,
        log_repo: GhrmAccessLogRepository,
        package_repo: GhrmSoftwarePackageRepository,
        github: IGithubAppClient,
        entitlements: ISubscriptionEntitlements,
        oauth_client_id: str = "",
        oauth_client_secret: str = "",
        oauth_redirect_uri: str = "",
        grace_period_fallback_days: int = 7,
        allow_extensive_permissions: bool = False,
    ) -> None:
        self._access_repo = access_repo
        self._membership_repo = membership_repo
        self._log_repo = log_repo
        self._package_repo = package_repo
        self._github = github
        self._entitlements = entitlements
        self._oauth_client_id = oauth_client_id
        self._oauth_client_secret = oauth_client_secret
        self._oauth_redirect_uri = oauth_redirect_uri
        self._grace_fallback_days = grace_period_fallback_days
        self._allow_extensive_permissions = allow_extensive_permissions

    # ------------------------------------------------------------------ #
    # OAuth flow                                                           #
    # ------------------------------------------------------------------ #

    def get_oauth_url(self, user_id: str, state: str) -> str:
        """Build the GitHub OAuth authorize URL."""
        from urllib.parse import urlencode

        params = urlencode(
            {
                "client_id": self._oauth_client_id,
                "redirect_uri": self._oauth_redirect_uri,
                "scope": OAUTH_SCOPE,
                "state": state,
            }
        )
        return f"https://github.com/login/oauth/authorize?{params}"

    def handle_oauth_callback(self, user_id: str, code: str) -> Dict[str, Any]:
        """Exchange the OAuth code, store identity, then grant entitled repos."""
        try:
            oauth_token = self._github.exchange_oauth_code(
                code=code,
                client_id=self._oauth_client_id,
                client_secret=self._oauth_client_secret,
                redirect_uri=self._oauth_redirect_uri,
            )
        except Exception as exc:
            raise GhrmOAuthError(f"OAuth exchange failed: {exc}") from exc

        try:
            github_user = self._github.get_oauth_user(oauth_token)
        except Exception as exc:
            raise GhrmOAuthError(f"Failed to fetch GitHub user: {exc}") from exc

        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            access = GhrmUserGithubAccess(user_id=user_id)
        access.github_username = github_user["login"]
        access.github_user_id = str(github_user["id"])
        # Encrypted at rest by the EncryptedString TypeDecorator on the column (S05).
        access.oauth_token = oauth_token
        access.oauth_scope = OAUTH_SCOPE
        self._access_repo.save(access)

        self._grant_entitled(user_id, access, triggered_by="oauth_callback")
        return access.to_dict()

    def disconnect_github(self, user_id: str) -> None:
        """Remove every membership's collaborator/invite, then delete identity."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            return

        for membership in self._membership_repo.find_by_user(user_id):
            self._tear_down_membership(access, membership, triggered_by="manual")

        self._membership_repo.delete_for_user(user_id)
        self._access_repo.delete(str(access.id))

    # ------------------------------------------------------------------ #
    # Subscription event handlers                                          #
    # ------------------------------------------------------------------ #

    def on_subscription_activated(self, user_id: str, plan_id: str) -> None:
        """Ensure the plan's package when connected; no-op when not connected."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            return  # Not connected — connect re-resolves entitlements (D1).
        package = self._package_repo.find_by_tariff_plan_id(plan_id)
        if not package:
            return
        self._ensure_collaborator(
            user_id, package, access, triggered_by="subscription_event"
        )

    def on_subscription_cancelled(
        self, user_id: str, plan_id: str, trailing_days: int = 0
    ) -> None:
        """Move the plan's membership into GRACE with an expiry."""
        package = self._package_repo.find_by_tariff_plan_id(plan_id)
        if not package:
            return
        days = trailing_days or self._grace_fallback_days
        self._membership_repo.upsert(
            user_id,
            package.id,
            status=MembershipStatus.GRACE.value,
            grace_expires_at=utcnow() + timedelta(days=days),
        )
        self._log_repo.log(
            user_id, str(package.id), SyncAction.GRACE_STARTED, "subscription_event"
        )

    def on_subscription_payment_failed(
        self, user_id: str, plan_id: str, trailing_days: int = 0
    ) -> None:
        """Start the grace period (same as cancellation)."""
        self.on_subscription_cancelled(user_id, plan_id, trailing_days)

    def on_subscription_renewed(self, user_id: str, plan_id: str) -> None:
        """Re-ensure access on renewal (no token rotation — D3)."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            return
        package = self._package_repo.find_by_tariff_plan_id(plan_id)
        if not package:
            return
        self._ensure_collaborator(
            user_id, package, access, triggered_by="subscription_event"
        )

    # ------------------------------------------------------------------ #
    # Grace period scheduler + acceptance verification                     #
    # ------------------------------------------------------------------ #

    def revoke_expired_grace_access(self) -> int:
        """Revoke every grace-expired membership. Returns the count."""
        expired = self._membership_repo.find_grace_expired(utcnow())
        access_cache: Dict[str, Optional[GhrmUserGithubAccess]] = {}
        count = 0
        for membership in expired:
            user_id = str(membership.user_id)
            if user_id not in access_cache:
                access_cache[user_id] = self._access_repo.find_by_user_id(user_id)
            access = access_cache[user_id]
            if access:
                self._tear_down_membership(access, membership, triggered_by="scheduler")
            self._membership_repo.upsert(
                membership.user_id,
                membership.package_id,
                status=MembershipStatus.REVOKED.value,
                grace_expires_at=None,
            )
            count += 1
        return count

    def verify_acceptance(self, user_id: str) -> None:
        """Promote INVITED memberships the user has accepted to ACTIVE."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            return
        for membership in self._membership_repo.find_by_user(user_id):
            if membership.status != MembershipStatus.INVITED.value:
                continue
            package = self._resolve_package(membership)
            if not package:
                continue
            if self._github.is_collaborator(
                package.github_owner, package.github_repo, access.github_username
            ):
                self._membership_repo.upsert(
                    membership.user_id,
                    membership.package_id,
                    status=MembershipStatus.ACTIVE.value,
                    invitation_id=None,
                )

    # ------------------------------------------------------------------ #
    # User-facing queries                                                  #
    # ------------------------------------------------------------------ #

    def get_access_status(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Identity + memberships for the user, or None when not connected."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            return None
        memberships = self._membership_repo.find_by_user(user_id)
        result = access.to_dict()
        result["connected"] = True
        result["memberships"] = [membership.to_dict() for membership in memberships]
        return result

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _grant_entitled(
        self, user_id: str, access: GhrmUserGithubAccess, triggered_by: str
    ) -> None:
        """Ensure a collaborator for every package the user is entitled to."""
        # The port is typed in UUIDs; GHRM carries ids as UUID-strings (event
        # payloads / g.user_id) and the subscription read model accepts either.
        # Cast at this single typed boundary — no runtime conversion (which would
        # break on non-UUID test fixtures).
        for plan_id in self._entitlements.active_plan_ids(cast(UUID, user_id)):
            package = self._package_repo.find_by_tariff_plan_id(str(plan_id))
            if not package:
                continue
            self._ensure_collaborator(
                user_id, package, access, triggered_by=triggered_by
            )

    def _ensure_collaborator(
        self,
        user_id: str,
        package: Any,
        access: GhrmUserGithubAccess,
        triggered_by: str,
    ) -> None:
        """Single home for grant: add collaborator on every repo the package
        resolves to (``repo_targets()`` — single or bundle) + upsert one
        membership with per-repo ``repo_grants`` and a rolled-up row status (DRY).

        Per-repo ``GithubAppClientError`` is recorded in that repo's entry and
        in ``last_error`` but never aborts the loop (best-effort). Any other
        exception propagates. The rollup is ACTIVE when every repo is active,
        else INVITED when any repo is pending, else ERROR.
        """
        permission = resolve_effective_permission(
            package, self._allow_extensive_permissions
        )
        repo_grants: list = []
        last_error: Optional[str] = None
        for owner, repo in package.repo_targets():
            try:
                result = self._github.add_collaborator(
                    owner, repo, access.github_username, permission
                )
                repo_status = (
                    MembershipStatus.ACTIVE.value
                    if result.state == "active"
                    else MembershipStatus.INVITED.value
                )
                repo_grants.append(
                    {
                        "owner": owner,
                        "repo": repo,
                        "status": repo_status,
                        "invitation_id": result.invitation_id,
                    }
                )
            except GithubAppClientError as exc:
                last_error = str(exc)
                repo_grants.append(
                    {
                        "owner": owner,
                        "repo": repo,
                        "status": MembershipStatus.ERROR.value,
                        "invitation_id": None,
                    }
                )
                logger.warning(
                    "[GHRM] add_collaborator failed for %s/%s: %s",
                    owner,
                    repo,
                    exc,
                )

        rolled_up_status = self._roll_up_status(repo_grants)
        representative_invitation_id = self._representative_invitation_id(
            package, repo_grants
        )
        self._membership_repo.upsert(
            user_id,
            package.id,
            status=rolled_up_status,
            invitation_id=representative_invitation_id,
            invited_at=utcnow(),
            last_error=last_error,
            repo_grants=repo_grants,
        )
        if rolled_up_status != MembershipStatus.ERROR.value:
            self._log_repo.log(
                user_id, str(package.id), SyncAction.ADD_COLLABORATOR, triggered_by
            )

    @staticmethod
    def _roll_up_status(repo_grants: list) -> str:
        """ACTIVE if every repo active, else INVITED if any pending, else ERROR."""
        statuses = {grant["status"] for grant in repo_grants}
        if statuses == {MembershipStatus.ACTIVE.value}:
            return MembershipStatus.ACTIVE.value
        if MembershipStatus.INVITED.value in statuses:
            return MembershipStatus.INVITED.value
        if MembershipStatus.ACTIVE.value in statuses:
            # Mix of active + error (no pending) — still partially granted.
            return MembershipStatus.ACTIVE.value
        return MembershipStatus.ERROR.value

    @staticmethod
    def _representative_invitation_id(package: Any, repo_grants: list) -> Optional[str]:
        """The representative repo's invitation id (display + back-compat)."""
        representative = (package.github_owner, package.github_repo)
        for grant in repo_grants:
            if (grant["owner"], grant["repo"]) == representative:
                return grant.get("invitation_id")
        return repo_grants[0].get("invitation_id") if repo_grants else None

    def _tear_down_membership(
        self, access: GhrmUserGithubAccess, membership: Any, triggered_by: str
    ) -> None:
        """Remove collaborator / cancel invite for every repo a membership grants.

        Loops the membership's recorded ``repo_grants`` (falling back to the
        package's representative repo for legacy/empty rows). A repo still
        covered by another live (ACTIVE/INVITED/GRACE) membership is SKIPPED
        (D6 — no over-revoke). For each remaining repo an INVITED grant with an
        invitation id is ``cancel_invitation``-d, otherwise ``remove_collaborator``.

        Best-effort: a GitHub-side failure (e.g. the App lacks permission → 403)
        is logged and swallowed so the user's disconnect always completes.
        """
        package = self._resolve_package(membership)
        if not package:
            return
        still_entitled = self._repos_still_entitled(
            str(membership.user_id), excluding=membership
        )
        for owner, repo, status, invitation_id in self._tear_down_targets(
            membership, package
        ):
            if (owner, repo) in still_entitled:
                continue
            try:
                if status == MembershipStatus.INVITED.value and invitation_id:
                    self._github.cancel_invitation(owner, repo, invitation_id)
                else:
                    self._github.remove_collaborator(
                        owner, repo, access.github_username
                    )
            except GithubAppClientError as exc:
                logger.warning(
                    "[GHRM] tear-down (remove collaborator / cancel invite) failed "
                    "for %s/%s: %s — continuing disconnect",
                    owner,
                    repo,
                    exc,
                )
                continue
            self._log_repo.log(
                str(membership.user_id),
                str(membership.package_id),
                SyncAction.REMOVE_COLLABORATOR,
                triggered_by,
            )

    @staticmethod
    def _tear_down_targets(membership: Any, package: Any) -> list:
        """Per-repo ``(owner, repo, status, invitation_id)`` tuples to tear down.

        Prefers the membership's recorded ``repo_grants``; falls back to the
        package's representative repo (with the row-level status/invitation) for
        legacy rows that predate ``repo_grants``.
        """
        repo_grants = getattr(membership, "repo_grants", None) or []
        if repo_grants:
            return [
                (
                    grant["owner"],
                    grant["repo"],
                    grant.get("status"),
                    grant.get("invitation_id"),
                )
                for grant in repo_grants
            ]
        return [
            (
                package.github_owner,
                package.github_repo,
                membership.status,
                membership.invitation_id,
            )
        ]

    _LIVE_STATUSES = (
        MembershipStatus.ACTIVE.value,
        MembershipStatus.INVITED.value,
        MembershipStatus.GRACE.value,
    )

    def _repos_still_entitled(self, user_id: str, *, excluding: Any) -> set:
        """Repos the user is still entitled to via OTHER live memberships (D6).

        Pure read: the union of ``repo_targets()`` over the user's other
        memberships in {ACTIVE, INVITED, GRACE}. No GitHub calls.
        """
        excluded_id = getattr(excluding, "id", None)
        still_entitled: set = set()
        for membership in self._membership_repo.find_by_user(user_id):
            if (
                excluded_id is not None
                and getattr(membership, "id", None) == excluded_id
            ):
                continue
            if membership is excluding:
                continue
            if membership.status not in self._LIVE_STATUSES:
                continue
            package = self._resolve_package(membership)
            if not package:
                continue
            still_entitled.update(package.repo_targets())
        return still_entitled

    def _resolve_package(self, membership: Any) -> Optional[Any]:
        """Return the membership's package, preferring the eager relationship."""
        package = getattr(membership, "package", None)
        if package is not None:
            return package
        return self._package_repo.find_by_id(str(membership.package_id))
