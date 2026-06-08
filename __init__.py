"""GHRM — GitHub Repo Manager plugin."""
from typing import Optional, Dict, Any, TYPE_CHECKING
from vbwd.plugins.base import BasePlugin, PluginMetadata

if TYPE_CHECKING:
    from flask import Blueprint

DEFAULT_CONFIG = {
    "github_app_id": "",
    # Externally provisioned (ops-placed, read-only) GitHub App RSA key. Read via
    # the core FilesystemManager (secrets perms posture + path confinement, S58.4).
    # Default kept at the legacy path for back-compat; NEW installs may point this
    # at the secrets namespace, e.g. "/app/var/secrets/ghrm/github-app.pem".
    "github_app_private_key_path": "/app/var/ghrm/auth/github-app.pem",
    "github_installation_id": "",
    "github_oauth_client_id": "",
    "github_oauth_client_secret": "",
    "github_oauth_redirect_uri": "http://localhost:8080/ghrm/auth/github/callback",
    "software_category_slugs": ["backend", "fe-user", "fe-admin"],
    "software_detail_cms_layout_slug": "ghrm-software-detail",
    "grace_period_fallback_days": 7,
    "allow_extensive_github_permissions": False,
}


class _SubscriptionEntitlementsAdapter:
    """Satisfies ghrm's ``ISubscriptionEntitlements`` port (DIP) by delegating to
    the subscription plugin's read model.

    This is the SINGLE place GHRM imports from the subscription plugin —
    legitimate because GHRM declares ``dependencies=["subscription"]`` (a
    declared plugin->plugin dependency). The import is local so it is reached
    only when an entitlement read actually happens.
    """

    def active_plan_ids(self, user_id):
        from plugins.subscription.subscription.services.subscription_read_model import (
            SubscriptionReadModel,
        )

        return SubscriptionReadModel().active_plan_ids(user_id)


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
            dependencies=["subscription"],
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

    @property
    def admin_permissions(self):
        return [
            {"key": "ghrm.packages.view", "label": "View packages", "group": "GHRM"},
            {
                "key": "ghrm.packages.manage",
                "label": "Manage packages",
                "group": "GHRM",
            },
            {"key": "ghrm.access.view", "label": "View access", "group": "GHRM"},
            {"key": "ghrm.access.manage", "label": "Manage access", "group": "GHRM"},
            {"key": "ghrm.configure", "label": "GHRM settings", "group": "GHRM"},
        ]

    def _register_data_exchangers(self) -> None:
        """Register the GHRM entity exchangers into the data-exchange seam.

        Core declares none of these (it stays agnostic); the plugin adds them on
        enable through the shared ``db.session`` so GHRM packages appear on the
        generic Settings → Import/Export page. Clear-safe: re-registering
        replaces by key (per-test app re-enable).
        """
        import logging

        try:
            from vbwd.extensions import db
            from plugins.ghrm.src.services.data_exchange.ghrm_exchangers import (
                register_ghrm_exchangers,
            )

            register_ghrm_exchangers(db.session)
        except Exception as exchanger_error:
            logging.getLogger(__name__).warning(
                "[ghrm] Failed to register data exchangers: %s", exchanger_error
            )

    def on_enable(self) -> None:
        self._register_data_exchangers()

    def _make_access_service(self):
        """Composition root for GithubAccessService.

        Builds the repos (inline, ``db.session``-bound — GHRM's repo wiring
        convention) and injects the subscription-backed entitlements adapter.
        This is the ONLY place the subscription concrete is reached; the
        service itself depends on the ghrm-owned ``ISubscriptionEntitlements``
        port (DIP). Raises GithubNotConfiguredError when credentials are absent.
        """
        from vbwd.extensions import db
        from plugins.ghrm.src.repositories.user_github_access_repository import (
            GhrmUserGithubAccessRepository,
        )
        from plugins.ghrm.src.repositories.repo_membership_repository import (
            GhrmRepoMembershipRepository,
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
        from plugins.ghrm.src.routes import _make_github_client

        cfg = self._config or {}
        github = _make_github_client(cfg)
        return GithubAccessService(
            access_repo=GhrmUserGithubAccessRepository(db.session),
            membership_repo=GhrmRepoMembershipRepository(db.session),
            log_repo=GhrmAccessLogRepository(db.session),
            package_repo=GhrmSoftwarePackageRepository(db.session),
            github=github,
            entitlements=_SubscriptionEntitlementsAdapter(),
            oauth_client_id=cfg.get("github_oauth_client_id", ""),
            oauth_client_secret=cfg.get("github_oauth_client_secret", ""),
            oauth_redirect_uri=cfg.get("github_oauth_redirect_uri", ""),
            grace_period_fallback_days=cfg.get("grace_period_fallback_days", 7),
            allow_extensive_permissions=bool(
                cfg.get("allow_extensive_github_permissions", False)
            ),
        )

    def register_event_handlers(self, bus: Any) -> None:
        """Subscribe GHRM subscription lifecycle handlers to EventBus."""
        try:
            from plugins.ghrm.src.routes import (
                _make_github_client,
                GithubNotConfiguredError,
            )

            cfg = self._config or {}
            # Validate credentials up front so misconfiguration is logged once,
            # not per event. The handlers rebuild the service per call (fresh
            # db.session) via the composition root.
            _make_github_client(cfg)

            def on_activated(_name: str, payload: dict) -> None:
                self._make_access_service().on_subscription_activated(
                    payload["user_id"], payload["plan_id"]
                )

            def on_cancelled(_name: str, payload: dict) -> None:
                self._make_access_service().on_subscription_cancelled(
                    payload["user_id"],
                    payload["plan_id"],
                    trailing_days=payload.get("trailing_days", 0),
                )

            def on_payment_failed(_name: str, payload: dict) -> None:
                self._make_access_service().on_subscription_payment_failed(
                    payload["user_id"],
                    payload["plan_id"],
                    trailing_days=payload.get("trailing_days", 0),
                )

            def on_renewed(_name: str, payload: dict) -> None:
                self._make_access_service().on_subscription_renewed(
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
