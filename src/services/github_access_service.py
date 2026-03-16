"""GithubAccessService — manages GitHub OAuth identity and repo collaborator lifecycle."""
from datetime import timedelta
from src.utils.datetime_utils import utcnow
from typing import Optional, Dict, Any
from plugins.ghrm.src.models.ghrm_user_github_access import (
    GhrmUserGithubAccess,
    AccessStatus,
)
from plugins.ghrm.src.models.ghrm_access_log import SyncAction
from plugins.ghrm.src.repositories.user_github_access_repository import (
    GhrmUserGithubAccessRepository,
)
from plugins.ghrm.src.repositories.access_log_repository import GhrmAccessLogRepository
from plugins.ghrm.src.repositories.software_package_repository import (
    GhrmSoftwarePackageRepository,
)
from plugins.ghrm.src.services.github_app_client import IGithubAppClient


class GhrmGithubNotConnectedError(Exception):
    """Raised when an operation requires a connected GitHub account."""


class GhrmOAuthError(Exception):
    """Raised when GitHub OAuth exchange fails."""


class GithubAccessService:
    """Manages GitHub OAuth identity, deploy tokens, and collaborator lifecycle."""

    def __init__(
        self,
        access_repo: GhrmUserGithubAccessRepository,
        log_repo: GhrmAccessLogRepository,
        package_repo: GhrmSoftwarePackageRepository,
        github: IGithubAppClient,
        oauth_client_id: str = "",
        oauth_client_secret: str = "",
        oauth_redirect_uri: str = "",
        grace_period_fallback_days: int = 7,
    ) -> None:
        self._access_repo = access_repo
        self._log_repo = log_repo
        self._package_repo = package_repo
        self._github = github
        self._oauth_client_id = oauth_client_id
        self._oauth_client_secret = oauth_client_secret
        self._oauth_redirect_uri = oauth_redirect_uri
        self._grace_fallback_days = grace_period_fallback_days

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
                "scope": "read:user",
                "state": state,
            }
        )
        return f"https://github.com/login/oauth/authorize?{params}"

    def handle_oauth_callback(self, user_id: str, code: str) -> Dict[str, Any]:
        """
        Exchange OAuth code for token, fetch GitHub identity, upsert access record.
        If user has active subscription, adds collaborator for all their packages.
        Returns access dict.
        """
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
            gh_user = self._github.get_oauth_user(oauth_token)
        except Exception as exc:
            raise GhrmOAuthError(f"Failed to fetch GitHub user: {exc}") from exc

        github_username = gh_user["login"]
        github_user_id = str(gh_user["id"])

        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            access = GhrmUserGithubAccess(user_id=user_id)
        access.github_username = github_username
        access.github_user_id = github_user_id
        access.oauth_token = oauth_token  # TODO: encrypt in production
        access.oauth_scope = "read:user"
        access.access_status = AccessStatus.ACTIVE
        self._access_repo.save(access)

        # Add collaborator for any packages the user is subscribed to
        self._sync_collaborators_for_user(
            user_id, access, triggered_by="oauth_callback"
        )

        return access.to_dict()

    def disconnect_github(self, user_id: str) -> None:
        """Revoke token, remove collaborator from all repos, delete access record."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            return

        packages = self._get_packages_for_user(user_id)
        for pkg in packages:
            self._github.remove_collaborator(
                pkg.github_owner, pkg.github_repo, access.github_username
            )
            self._log_repo.log(
                user_id, str(pkg.id), SyncAction.REMOVE_COLLABORATOR, "manual"
            )

        if access.oauth_token:
            try:
                self._github.revoke_deploy_token(access.oauth_token)
            except Exception:
                pass

        self._access_repo.delete(str(access.id))

    # ------------------------------------------------------------------ #
    # Subscription event handlers                                          #
    # ------------------------------------------------------------------ #

    def on_subscription_activated(self, user_id: str, plan_id: str) -> None:
        """Add collaborator when subscription activates (if GitHub is connected)."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access or access.access_status == AccessStatus.REVOKED:
            return
        pkg = self._package_repo.find_by_tariff_plan_id(plan_id)
        if not pkg:
            return
        self._github.add_collaborator(
            pkg.github_owner,
            pkg.github_repo,
            access.github_username,
            pkg.github_protected_branch,
        )
        access.access_status = AccessStatus.ACTIVE
        access.grace_expires_at = None
        self._access_repo.save(access)
        self._log_repo.log(
            user_id, str(pkg.id), SyncAction.ADD_COLLABORATOR, "subscription_event"
        )

    def on_subscription_cancelled(
        self, user_id: str, plan_id: str, trailing_days: int = 0
    ) -> None:
        """Start grace period on subscription cancellation."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            return
        days = trailing_days or self._grace_fallback_days
        access.access_status = AccessStatus.GRACE
        access.grace_expires_at = utcnow() + timedelta(days=days)
        self._access_repo.save(access)
        pkg = self._package_repo.find_by_tariff_plan_id(plan_id)
        self._log_repo.log(
            user_id,
            str(pkg.id) if pkg else None,
            SyncAction.GRACE_STARTED,
            "subscription_event",
        )

    def on_subscription_payment_failed(
        self, user_id: str, plan_id: str, trailing_days: int = 0
    ) -> None:
        """Start grace period on payment failure."""
        self.on_subscription_cancelled(user_id, plan_id, trailing_days)

    def on_subscription_renewed(self, user_id: str, plan_id: str) -> None:
        """Extend access on renewal — rotate deploy token."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access:
            return
        pkg = self._package_repo.find_by_tariff_plan_id(plan_id)
        if not pkg:
            return
        if access.deploy_token:
            try:
                self._github.revoke_deploy_token(access.deploy_token)
            except Exception:
                pass
        new_token = self._github.create_deploy_token(
            pkg.github_owner, pkg.github_repo, access.github_username
        )
        access.deploy_token = new_token  # TODO: encrypt
        access.access_status = AccessStatus.ACTIVE
        access.grace_expires_at = None
        self._access_repo.save(access)
        self._log_repo.log(
            user_id, str(pkg.id), SyncAction.TOKEN_ROTATED, "subscription_event"
        )

    # ------------------------------------------------------------------ #
    # Grace period scheduler                                               #
    # ------------------------------------------------------------------ #

    def revoke_expired_grace_access(self) -> int:
        """Revoke all access records where grace period has expired. Returns count."""
        expired = self._access_repo.find_grace_expired(utcnow())
        count = 0
        for access in expired:
            packages = self._get_packages_for_user(str(access.user_id))
            for pkg in packages:
                self._github.remove_collaborator(
                    pkg.github_owner, pkg.github_repo, access.github_username
                )
                if access.deploy_token:
                    self._github.revoke_deploy_token(access.deploy_token)
                self._log_repo.log(
                    str(access.user_id),
                    str(pkg.id),
                    SyncAction.REMOVE_COLLABORATOR,
                    "scheduler",
                )
            access.access_status = AccessStatus.REVOKED
            access.deploy_token = None
            self._access_repo.save(access)
            count += 1
        return count

    # ------------------------------------------------------------------ #
    # User-facing queries                                                  #
    # ------------------------------------------------------------------ #

    def get_access_status(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return current GitHub access record for a user, or None."""
        access = self._access_repo.find_by_user_id(user_id)
        return access.to_dict() if access else None

    def get_install_token(self, user_id: str, package_slug: str) -> str:
        """Return deploy token for a user/package combo (active subs only)."""
        access = self._access_repo.find_by_user_id(user_id)
        if not access or access.access_status != AccessStatus.ACTIVE:
            raise GhrmGithubNotConnectedError("Active GitHub connection required")
        return access.deploy_token or ""

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _get_packages_for_user(self, user_id: str):
        """Return all active packages the user is subscribed to (simplified — checks all)."""
        # In production this would join subscriptions; for now returns all active packages
        result = self._package_repo.find_all(per_page=1000)
        return [p for p in result["items"] if p.is_active]

    def _sync_collaborators_for_user(
        self, user_id: str, access: GhrmUserGithubAccess, triggered_by: str
    ) -> None:
        """Add user as collaborator for all their active subscribed packages."""
        packages = self._get_packages_for_user(user_id)
        for pkg in packages:
            try:
                self._github.add_collaborator(
                    pkg.github_owner,
                    pkg.github_repo,
                    access.github_username,
                    pkg.github_protected_branch,
                )
                self._log_repo.log(
                    user_id, str(pkg.id), SyncAction.ADD_COLLABORATOR, triggered_by
                )
            except Exception:
                pass
