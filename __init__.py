"""GHRM — GitHub Repo Manager plugin."""
from typing import Optional, Dict, Any, TYPE_CHECKING
from vbwd.plugins.base import BasePlugin, PluginMetadata, PublicRouteDeclaration

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
    "software_category_slugs": ["backend", "fe-user", "fe-admin", "plugin-bundles"],
    "software_detail_cms_layout_slug": "ghrm-software-detail",
    "grace_period_fallback_days": 7,
    "allow_extensive_github_permissions": False,
    # S132: org/team access model. ``github_org`` is the GitHub org that team
    # packages live under; ``default_access_kind`` is the kind a newly created
    # package defaults to. Both are model/admin-level (a package carries its own
    # ``access_kind``/``github_org``/``github_team_slug``), so they are NOT wired
    # into the runtime grant path — the service reads the package's own columns.
    "github_org": "",
    "default_access_kind": "repo",
    # Vendor-mode (marketplace): gates the self-service vendor package route.
    # Off by default so a classic install (admin-only packages) is unchanged.
    "marketplace_enabled": False,
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
            version="26.6.1",
            author="VBWD Team",
            description="GitHub Repo Manager — software catalogue with subscription-gated GitHub access",
            dependencies=["subscription"],
        )

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        merged = {**DEFAULT_CONFIG}
        if config:
            merged.update(config)
        super().initialize(merged)

    def declare_public_routes(self) -> PublicRouteDeclaration:
        """Public GHRM marketplace reads + the API-key-gated CI sync trigger.

        The marketplace (categories, config, packages, widgets) browses
        pre-login; the sync trigger is authenticated by an API key validated
        inside the handler (public to the CI runner, not the session).
        """
        return PublicRouteDeclaration(
            read={
                "/api/v1/ghrm/categories": "Public GHRM marketplace category listing.",
                "/api/v1/ghrm/tags": "Public GHRM marketplace package-tag listing.",
                "/api/v1/ghrm/config": "Public GHRM marketplace config for the storefront.",
                "/api/v1/ghrm/packages": "Public GHRM software-package listing.",
                "/api/v1/ghrm/packages/<slug>": "Public single GHRM package detail.",
                "/api/v1/ghrm/packages/<slug>/related": "Public related-packages list for a GHRM package.",
                "/api/v1/ghrm/packages/<slug>/versions": "Public version history for a GHRM package.",
                "/api/v1/ghrm/packages/by-plan/<plan_id>": "Public GHRM package lookup by plan id.",
                "/api/v1/ghrm/widgets": "Public GHRM storefront widgets.",
            },
            mutation={
                "/api/v1/ghrm/sync": "GHRM CI sync trigger; authenticated by an API key validated inside the handler.",
            },
        )

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

        # S88 — contribute the GHRM catalog seed to ``flask reset-demo`` through
        # the agnostic demo-data registry (core imports no ghrm model).
        # The seed module imports cms models (it seeds the software-catalogue
        # CMS pages), so cms is a SOFT dependency: when cms is absent the demo
        # seeder simply isn't registered — ghrm still enables and its entity
        # type below is still registered (a missing optional dep must not abort
        # on_enable). Mirrors meinchat's soft-cms posture.
        import logging

        from vbwd.services.demo_data_registry import register_catalog_seeder

        try:
            from plugins.ghrm.src.bin.populate_ghrm import seed_catalog

            register_catalog_seeder(seed_catalog)
        except ImportError as seed_import_error:
            logging.getLogger(__name__).warning(
                "[ghrm] Demo catalog seeder not registered (cms absent?): %s",
                seed_import_error,
            )

        # S77 — register ghrm_software_package as taggable/custom-field-able so
        # the core value endpoints resolve it (gated by ghrm.packages.manage)
        # and the package serializer can append tags / custom fields.
        from vbwd.services.entity_type_registry import (
            EntityTypeRegistration,
            register_entity_type,
        )

        register_entity_type(
            EntityTypeRegistration(
                "ghrm_software_package",
                "Software package",
                "ghrm.packages.manage",
            )
        )

        # Cross-entity search seam — contribute active software packages to the
        # agnostic search registry so the /search bot can find them (idempotent:
        # register replaces by entity_type). Core names no ghrm vocabulary.
        from vbwd.services.search import search_provider_registry
        from plugins.ghrm.src.search_provider import GhrmPackageSearchProvider

        search_provider_registry.register(GhrmPackageSearchProvider())

        # Marketplace vendor-listings seam — contribute this vendor's software
        # packages to the marketplace admin "what does this user sell?"
        # aggregation. The soft import lives HERE (the plugin wiring root, not
        # ghrm source) so ghrm's source stays marketplace-free
        # (test_vendor_mode_contract) AND the per-plugin isolated CI (ghrm
        # without marketplace) still enables.
        try:
            from plugins.marketplace.marketplace.services import (
                vendor_listings_registry as marketplace_listings_registry,
            )
        except ImportError:
            marketplace_listings_registry = None
        if marketplace_listings_registry is not None:
            from plugins.ghrm.src.marketplace_listings import (
                GHRM_LISTING_TYPE_ID,
                vendor_listings_provider,
            )

            marketplace_listings_registry.register_vendor_listings_provider(
                GHRM_LISTING_TYPE_ID, vendor_listings_provider
            )

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
        from vbwd.services.entity_type_registry import unregister_entity_type

        unregister_entity_type("ghrm_software_package")

        from vbwd.services.search import search_provider_registry

        search_provider_registry.unregister("ghrm_package")

        # Mirror of the on_enable registration — guarded soft import so the
        # source stays marketplace-free and disable is safe when absent.
        try:
            from plugins.marketplace.marketplace.services import (
                vendor_listings_registry as marketplace_listings_registry,
            )
        except ImportError:
            marketplace_listings_registry = None
        if marketplace_listings_registry is not None:
            from plugins.ghrm.src.marketplace_listings import GHRM_LISTING_TYPE_ID

            marketplace_listings_registry.unregister_vendor_listings_provider(
                GHRM_LISTING_TYPE_ID
            )
