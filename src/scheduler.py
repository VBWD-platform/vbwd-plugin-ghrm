"""GHRM grace period scheduler — revokes expired collaborator access."""
import logging

logger = logging.getLogger(__name__)


def revoke_expired_grace_access():
    """
    Called by APScheduler (daily cron).
    Revokes all ghrm_user_github_access records where grace period has expired.
    """
    try:
        from src.extensions import db
        from plugins.ghrm.src.repositories.user_github_access_repository import GhrmUserGithubAccessRepository
        from plugins.ghrm.src.repositories.access_log_repository import GhrmAccessLogRepository
        from plugins.ghrm.src.repositories.software_package_repository import GhrmSoftwarePackageRepository
        from plugins.ghrm.src.services.github_access_service import GithubAccessService
        from plugins.ghrm.src.routes import _make_github_client, _cfg, GithubNotConfiguredError

        try:
            github = _make_github_client(_cfg())
        except GithubNotConfiguredError:
            logger.warning("[GHRM] Scheduler skipped — GitHub App not configured")
            return

        svc = GithubAccessService(
            access_repo=GhrmUserGithubAccessRepository(db.session),
            log_repo=GhrmAccessLogRepository(db.session),
            package_repo=GhrmSoftwarePackageRepository(db.session),
            github=github,
        )
        count = svc.revoke_expired_grace_access()
        if count:
            logger.info(f"[GHRM] Revoked {count} expired grace access records")
    except Exception as exc:
        logger.error(f"[GHRM] Grace period scheduler error: {exc}", exc_info=True)
