"""GHRM grace period scheduler — revokes expired collaborator access."""
import logging

logger = logging.getLogger(__name__)


def revoke_expired_grace_access():
    """
    Called by APScheduler (daily cron).
    Revokes all ghrm_user_github_access records where grace period has expired.
    """
    try:
        from plugins.ghrm.src.routes import _access_svc, GithubNotConfiguredError

        try:
            svc = _access_svc()
        except GithubNotConfiguredError:
            logger.warning("[GHRM] Scheduler skipped — GitHub App not configured")
            return

        count = svc.revoke_expired_grace_access()
        if count:
            logger.info(f"[GHRM] Revoked {count} expired grace access records")
    except Exception as exc:
        logger.error(f"[GHRM] Grace period scheduler error: {exc}", exc_info=True)
