"""GHRM — GitHub Repo Manager plugin."""
from typing import Optional, Dict, Any, TYPE_CHECKING
from src.plugins.base import BasePlugin, PluginMetadata

if TYPE_CHECKING:
    from flask import Blueprint

DEFAULT_CONFIG = {
    "github_app_id": "",
    "github_app_private_key_path": "/app/plugins/ghrm/github-app.pem",
    "github_installation_id": "",
    "github_oauth_client_id": "",
    "github_oauth_client_secret": "",
    "github_oauth_redirect_uri": "http://localhost:8080/ghrm/auth/github/callback",
    "software_category_slugs": ["backend", "fe-user", "fe-admin"],
    "software_detail_cms_layout_slug": "ghrm-software-detail",
    "grace_period_fallback_days": 7,
}


class GhrmPlugin(BasePlugin):
    """GitHub Repo Manager — software catalogue with subscription-gated repo access.

    Class MUST be defined in __init__.py (not re-exported) due to
    discovery check obj.__module__ != full_module in manager.py.
    """

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="ghrm",
            version="1.0.0",
            author="VBWD Team",
            description="GitHub Repo Manager — software catalogue with subscription-gated GitHub access",
            dependencies=[],
        )

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        merged = {**DEFAULT_CONFIG}
        if config:
            merged.update(config)
        super().initialize(merged)

    def get_blueprint(self) -> Optional["Blueprint"]:
        from plugins.ghrm.src.routes import ghrm_bp

        return ghrm_bp

    def get_url_prefix(self) -> Optional[str]:
        return ""

    def on_enable(self) -> None:
        pass

    def register_event_handlers(self, bus: Any) -> None:
        """Subscribe GHRM subscription lifecycle handlers to EventBus."""
        try:
            from src.extensions import db
            from plugins.ghrm.src.repositories.user_github_access_repository import (
                GhrmUserGithubAccessRepository,
            )
            from plugins.ghrm.src.repositories.access_log_repository import (
                GhrmAccessLogRepository,
            )
            from plugins.ghrm.src.repositories.software_package_repository import (
                GhrmSoftwarePackageRepository,
            )
            from plugins.ghrm.src.services.github_access_service import (
                GithubAccessService,
            )
            from plugins.ghrm.src.routes import (
                _make_github_client,
                GithubNotConfiguredError,
            )

            cfg = self._config or {}
            github = _make_github_client(cfg)

            def _make_access_service():
                return GithubAccessService(
                    access_repo=GhrmUserGithubAccessRepository(db.session),
                    log_repo=GhrmAccessLogRepository(db.session),
                    package_repo=GhrmSoftwarePackageRepository(db.session),
                    github=github,
                    oauth_client_id=cfg.get("github_oauth_client_id", ""),
                    oauth_client_secret=cfg.get("github_oauth_client_secret", ""),
                    oauth_redirect_uri=cfg.get("github_oauth_redirect_uri", ""),
                    grace_period_fallback_days=cfg.get("grace_period_fallback_days", 7),
                )

            def on_activated(_name: str, payload: dict) -> None:
                _make_access_service().on_subscription_activated(
                    payload["user_id"], payload["plan_id"]
                )

            def on_cancelled(_name: str, payload: dict) -> None:
                _make_access_service().on_subscription_cancelled(
                    payload["user_id"],
                    payload["plan_id"],
                    trailing_days=payload.get("trailing_days", 0),
                )

            def on_payment_failed(_name: str, payload: dict) -> None:
                _make_access_service().on_subscription_payment_failed(
                    payload["user_id"],
                    payload["plan_id"],
                    trailing_days=payload.get("trailing_days", 0),
                )

            def on_renewed(_name: str, payload: dict) -> None:
                _make_access_service().on_subscription_renewed(
                    payload["user_id"], payload["plan_id"]
                )

            bus.subscribe("subscription.activated", on_activated)
            bus.subscribe("subscription.cancelled", on_cancelled)
            bus.subscribe("subscription.payment_failed", on_payment_failed)
            bus.subscribe("subscription.renewed", on_renewed)
        except GithubNotConfiguredError as exc:
            import logging

            logging.getLogger(__name__).warning(
                "[GHRM] Subscription event handlers not registered — %s", exc
            )
        except Exception:
            pass  # Plugin disabled or dependencies not ready

    def on_disable(self) -> None:
        pass
