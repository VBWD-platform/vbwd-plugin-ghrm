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
        """Single home for grant: add collaborator + upsert membership (DRY).

        Only ``GithubAppClientError`` is caught — it is recorded as an ERROR
        membership and a warning is logged. Any other exception propagates.
        """
        try:
            result = self._github.add_collaborator(
                package.github_owner,
                package.github_repo,
                access.github_username,
                resolve_effective_permission(
                    package, self._allow_extensive_permissions
                ),
            )
            status = (
                MembershipStatus.ACTIVE.value
                if result.state == "active"
                else MembershipStatus.INVITED.value
            )
            self._membership_repo.upsert(
                user_id,
                package.id,
                status=status,
                invitation_id=result.invitation_id,
                invited_at=utcnow(),
                last_error=None,
            )
            self._log_repo.log(
                user_id, str(package.id), SyncAction.ADD_COLLABORATOR, triggered_by
            )
        except GithubAppClientError as exc:
            self._membership_repo.upsert(
                user_id,
                package.id,
                status=MembershipStatus.ERROR.value,
                last_error=str(exc),
            )
            logger.warning(
                "[GHRM] add_collaborator failed for %s/%s: %s",
                package.github_owner,
                package.github_repo,
                exc,
            )

    def _tear_down_membership(
        self, access: GhrmUserGithubAccess, membership: Any, triggered_by: str
    ) -> None:
        """Remove a collaborator or cancel a pending invitation for a membership.

        Best-effort: a GitHub-side failure (e.g. the App lacks permission to
        remove a collaborator → 403 "Resource not accessible by integration")
        is logged and swallowed so the user's disconnect always completes. The
        local access record + memberships are still deleted; only the
        GitHub-side removal is skipped. Mirrors the add path's handling.
        """
        package = self._resolve_package(membership)
        if not package:
            return
        try:
            if (
                membership.status == MembershipStatus.INVITED.value
                and membership.invitation_id
            ):
                self._github.cancel_invitation(
                    package.github_owner, package.github_repo, membership.invitation_id
                )
            else:
                self._github.remove_collaborator(
                    package.github_owner, package.github_repo, access.github_username
                )
        except GithubAppClientError as exc:
            logger.warning(
                "[GHRM] tear-down (remove collaborator / cancel invite) failed "
                "for %s/%s: %s — continuing disconnect",
                package.github_owner,
                package.github_repo,
                exc,
            )
            return
        self._log_repo.log(
            str(membership.user_id),
            str(membership.package_id),
            SyncAction.REMOVE_COLLABORATOR,
            triggered_by,
        )

    def _resolve_package(self, membership: Any) -> Optional[Any]:
        """Return the membership's package, preferring the eager relationship."""
        package = getattr(membership, "package", None)
        if package is not None:
            return package
        return self._package_repo.find_by_id(str(membership.package_id))
