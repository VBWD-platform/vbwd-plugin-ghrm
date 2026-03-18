"""GHRM plugin API routes.

Public (no auth):
    GET  /api/v1/ghrm/packages                 list packages
    GET  /api/v1/ghrm/packages/<slug>          package detail
    GET  /api/v1/ghrm/packages/<slug>/related  related packages
    GET  /api/v1/ghrm/packages/<slug>/versions versions list
    GET  /api/v1/ghrm/sync                     GitHub Action sync trigger (API key auth)
    GET  /api/v1/ghrm/widgets                  breadcrumb widget configs (public read)

Subscriber-only:
    GET  /api/v1/ghrm/packages/<slug>/install  install instructions

GitHub OAuth (requires_auth):
    GET    /api/v1/ghrm/auth/github            redirect to github.com/oauth/authorize
    POST   /api/v1/ghrm/auth/github/callback   exchange code + store identity
    DELETE /api/v1/ghrm/auth/github            disconnect GitHub

User profile (requires_auth):
    GET  /api/v1/ghrm/access                   current github access status

Admin (require_admin):
    GET    /api/v1/admin/ghrm/packages
    POST   /api/v1/admin/ghrm/packages
    PUT    /api/v1/admin/ghrm/packages/<id>
    DELETE /api/v1/admin/ghrm/packages/<id>
    POST   /api/v1/admin/ghrm/packages/<id>/rotate-key
    POST   /api/v1/admin/ghrm/packages/<id>/sync
    GET    /api/v1/admin/ghrm/packages/<id>/preview/<field>   (readme|changelog|screenshots)
    POST   /api/v1/admin/ghrm/packages/<id>/sync/<field>      (readme|changelog|screenshots)
    GET    /api/v1/admin/ghrm/access-log
    POST   /api/v1/admin/ghrm/access/sync/<user_id>
    GET    /api/v1/admin/ghrm/widgets
    PUT    /api/v1/admin/ghrm/widgets/<widget_id>
"""
import json
import logging
import os
import secrets
from flask import Blueprint, jsonify, request, g, current_app
from vbwd.extensions import db
from vbwd.middleware.auth import require_auth, require_admin

from plugins.ghrm.src.repositories.software_package_repository import (
    GhrmSoftwarePackageRepository,
)
from plugins.ghrm.src.repositories.software_sync_repository import (
    GhrmSoftwareSyncRepository,
)
from plugins.ghrm.src.repositories.user_github_access_repository import (
    GhrmUserGithubAccessRepository,
)
from plugins.ghrm.src.repositories.access_log_repository import GhrmAccessLogRepository
from plugins.ghrm.src.services.software_package_service import (
    SoftwarePackageService,
    GhrmPackageNotFoundError,
    GhrmSyncAuthError,
    GhrmNotConfiguredError,
    GhrmSubscriptionRequiredError,
)
from plugins.ghrm.src.services.github_access_service import (
    GithubAccessService,
    GhrmOAuthError,
)
from plugins.ghrm.src.services.github_app_client import IGithubAppClient
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage

logger = logging.getLogger(__name__)
ghrm_bp = Blueprint("ghrm", __name__)


# ─── Dependency factories ────────────────────────────────────────────────────


class GithubNotConfiguredError(Exception):
    """Raised when GitHub App credentials are absent or incomplete."""


def _make_github_client(cfg: dict) -> IGithubAppClient:
    """Return a configured GitHub App client.

    In non-production environments (GHRM_USE_MOCK_GITHUB=true) a MockGithubAppClient
    is returned so that integration tests and local development work without real
    GitHub App credentials.  The mock is NEVER used when this env var is absent or
    set to any value other than "true".
    """
    import os

    if os.environ.get("GHRM_USE_MOCK_GITHUB", "").lower() == "true":
        from plugins.ghrm.src.services.github_app_client import MockGithubAppClient

        return MockGithubAppClient()
    app_id = cfg.get("github_app_id", "")
    installation_id = cfg.get("github_installation_id", "")
    pem_path = cfg.get("github_app_private_key_path", "")
    if not (app_id and installation_id and pem_path and os.path.isfile(pem_path)):
        raise GithubNotConfiguredError(
            "GitHub App credentials not configured — set github_app_id, github_installation_id, "
            "and a valid github_app_private_key_path in the GHRM plugin config."
        )
    from plugins.ghrm.src.services.github_app_client_real import GithubAppClient

    with open(pem_path, "r") as f:
        private_key = f.read()
    return GithubAppClient(
        app_id=str(app_id),
        private_key=private_key,
        installation_id=str(installation_id),
    )


def _pkg_svc() -> SoftwarePackageService:
    cfg = _cfg()
    try:
        github = _make_github_client(cfg)
    except GithubNotConfiguredError:
        github = None
    return SoftwarePackageService(
        package_repo=GhrmSoftwarePackageRepository(db.session),
        sync_repo=GhrmSoftwareSyncRepository(db.session),
        github=github,
        software_category_slugs=cfg.get("software_category_slugs", []),
    )


def _access_svc() -> GithubAccessService:
    cfg = _cfg()
    # Raises GithubNotConfiguredError if credentials absent — callers return 503
    github = _make_github_client(cfg)
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


def _cfg() -> dict:
    try:
        from flask import current_app

        pm = getattr(current_app, "plugin_manager", None)
        if pm is None:
            return {}
        plugin = pm.get_plugin("ghrm")
        return plugin._config if plugin else {}
    except Exception:
        return {}


# ─── Public catalogue ────────────────────────────────────────────────────────


@ghrm_bp.route("/api/v1/ghrm/config", methods=["GET"])
def get_public_config():
    """Return public GHRM config needed by the frontend (layout slugs, etc.)."""
    import json as _json  # noqa: E401
    import os as _os

    cfg = _cfg()

    def _fallback_cfg():
        path = _os.path.join(_os.path.dirname(__file__), "..", "config.json")
        try:
            with open(path) as f:
                return _json.load(f)
        except Exception:
            return {}

    fb = _fallback_cfg()
    catalogue_slug = cfg.get("software_catalogue_cms_page_slug") or fb.get(
        "software_catalogue_cms_page_slug", "ghrm-software-catalogue"
    )
    detail_slug = cfg.get("software_detail_cms_page_slug") or fb.get(
        "software_detail_cms_page_slug", "ghrm-software-detail"
    )
    return jsonify(
        {
            "catalogue_page_slug": catalogue_slug,
            "detail_page_slug": detail_slug,
        }
    )


@ghrm_bp.route("/api/v1/ghrm/categories", methods=["GET"])
def list_categories():
    """Return the configured software category slugs and their DB names as labels."""
    import json as _json  # noqa: E401
    import os as _os
    from vbwd.extensions import db
    from vbwd.models.tarif_plan_category import TarifPlanCategory

    cfg = _cfg()
    slugs = cfg.get("software_category_slugs") or []
    # Fall back to config.json defaults when DB config is empty
    if not slugs:
        _cfg_path = _os.path.join(_os.path.dirname(__file__), "..", "config.json")
        try:
            with open(_cfg_path) as _f:
                slugs = _json.load(_f).get("software_category_slugs", [])
        except Exception:
            slugs = []
    # Look up real names from DB; fall back to slug-derived title if not found
    db_cats = {
        c.slug: c.name
        for c in db.session.query(TarifPlanCategory)
        .filter(TarifPlanCategory.slug.in_(slugs))
        .all()
    }
    categories = [
        {"slug": s, "label": db_cats.get(s, s.replace("-", " ").title())} for s in slugs
    ]
    return jsonify({"categories": categories})


@ghrm_bp.route("/api/v1/ghrm/packages", methods=["GET"])
def list_packages():
    """List active software packages."""
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 100)
    category_slug = request.args.get("category_slug") or None
    query = request.args.get("q") or None
    result = _pkg_svc().list_packages(
        page=page, per_page=per_page, category_slug=category_slug, query=query
    )
    return jsonify(result)


@ghrm_bp.route("/api/v1/ghrm/packages/<slug>", methods=["GET"])
def get_package(slug):
    """Get package detail with merged cached/override data."""
    try:
        data = _pkg_svc().get_package(slug)
        return jsonify(data)
    except GhrmPackageNotFoundError as e:
        return jsonify({"error": str(e)}), 404


@ghrm_bp.route("/api/v1/ghrm/packages/<slug>/related", methods=["GET"])
def get_related(slug):
    try:
        return jsonify(_pkg_svc().get_related(slug))
    except GhrmPackageNotFoundError as e:
        return jsonify({"error": str(e)}), 404


@ghrm_bp.route("/api/v1/ghrm/packages/<slug>/versions", methods=["GET"])
def get_versions(slug):
    try:
        return jsonify(_pkg_svc().get_versions(slug))
    except GhrmPackageNotFoundError as e:
        return jsonify({"error": str(e)}), 404


@ghrm_bp.route("/api/v1/ghrm/packages/by-plan/<plan_id>", methods=["GET"])
def get_package_by_plan(plan_id: str):
    pkg = _pkg_svc().get_by_tariff_plan_id(plan_id)
    if not pkg:
        return jsonify({"error": "No package for this plan"}), 404
    return jsonify(pkg.to_dict())


@ghrm_bp.route("/api/v1/ghrm/packages/<slug>/install", methods=["GET"])
@require_auth
def get_install(slug):
    """Return install instructions — requires active subscription + GitHub connection."""
    user_id = g.user_id
    # Fetch deploy_token directly from repo (not exposed in to_dict())
    raw = GhrmUserGithubAccessRepository(db.session).find_by_user_id(user_id)
    token = raw.deploy_token if raw else None
    try:
        return jsonify(
            _pkg_svc().get_install_instructions(slug, user_id, deploy_token=token)
        )
    except GhrmPackageNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except GhrmSubscriptionRequiredError as e:
        return jsonify({"error": str(e)}), 403


# ─── Sync endpoint (GitHub Action) ──────────────────────────────────────────


@ghrm_bp.route("/api/v1/ghrm/sync", methods=["GET", "POST"])
def sync_package():
    """Triggered by GitHub Action: ?package=<slug>&key=<api_key>"""
    api_key = request.args.get("key") or (request.json or {}).get("key", "")
    try:
        result = _pkg_svc().sync_package(api_key)
        return jsonify({"ok": True, "sync": result})
    except GhrmNotConfiguredError as e:
        logger.warning(f"[GHRM] sync skipped — {e}")
        return jsonify({"error": "GitHub App not configured — sync unavailable"}), 503
    except GithubNotConfiguredError as e:
        logger.warning(f"[GHRM] sync skipped — {e}")
        return jsonify({"error": "GitHub App not configured — sync unavailable"}), 503
    except GhrmSyncAuthError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        logger.error(f"[GHRM] sync error: {e}", exc_info=True)
        return jsonify({"error": "Sync failed"}), 500


# ─── GitHub OAuth ────────────────────────────────────────────────────────────


@ghrm_bp.route("/api/v1/ghrm/auth/github", methods=["GET"])
@require_auth
def github_oauth_start():
    """Build and return the GitHub OAuth URL (client does the redirect)."""
    import jwt as pyjwt
    import time

    user_id = g.user_id
    nonce = secrets.token_urlsafe(16)
    # Store nonce in Redis with 10-min TTL
    try:
        from vbwd.extensions import redis_client

        redis_client.setex(f"ghrm:oauth:nonce:{user_id}", 600, nonce)
    except Exception:
        pass
    state = pyjwt.encode(
        {"user_id": str(user_id), "nonce": nonce, "exp": int(time.time()) + 600},
        current_app.config.get("JWT_SECRET_KEY", "dev"),
        algorithm="HS256",
    )
    try:
        url = _access_svc().get_oauth_url(user_id, state)
    except GithubNotConfiguredError:
        return jsonify({"error": "GitHub App not configured"}), 503
    return jsonify({"url": url})


@ghrm_bp.route("/api/v1/ghrm/auth/github/callback", methods=["POST"])
@require_auth
def github_oauth_callback():
    """Exchange OAuth code, verify CSRF state, store GitHub identity."""
    import jwt as pyjwt

    user_id = g.user_id
    body = request.json or {}
    code = body.get("code", "")
    state = body.get("state", "")
    # Verify state JWT
    try:
        payload = pyjwt.decode(
            state, current_app.config.get("JWT_SECRET_KEY", "dev"), algorithms=["HS256"]
        )
        if str(payload.get("user_id")) != str(user_id):
            return jsonify({"error": "State mismatch"}), 400
        # Verify nonce
        nonce = payload.get("nonce", "")
        try:
            from vbwd.extensions import redis_client

            stored = redis_client.get(f"ghrm:oauth:nonce:{user_id}")
            if not stored or stored.decode() != nonce:
                return jsonify({"error": "Invalid or expired state"}), 400
            redis_client.delete(f"ghrm:oauth:nonce:{user_id}")
        except Exception:
            pass
    except pyjwt.ExpiredSignatureError:
        return jsonify({"error": "State expired"}), 400
    except pyjwt.InvalidTokenError:
        return jsonify({"error": "Invalid state"}), 400
    try:
        result = _access_svc().handle_oauth_callback(user_id, code)
        return jsonify(result)
    except GithubNotConfiguredError:
        return jsonify({"error": "GitHub App not configured"}), 503
    except GhrmOAuthError as e:
        return jsonify({"error": str(e)}), 502


@ghrm_bp.route("/api/v1/ghrm/auth/github", methods=["DELETE"])
@require_auth
def github_disconnect():
    """Disconnect GitHub — revoke token and remove collaborator."""
    try:
        _access_svc().disconnect_github(g.user_id)
    except GithubNotConfiguredError:
        return jsonify({"error": "GitHub App not configured"}), 503
    return jsonify({"ok": True})


# ─── User profile ────────────────────────────────────────────────────────────


@ghrm_bp.route("/api/v1/ghrm/access", methods=["GET"])
@require_auth
def get_access_status():
    try:
        result = _access_svc().get_access_status(g.user_id)
    except GithubNotConfiguredError:
        return jsonify({"connected": False}), 200
    if not result:
        return jsonify({"connected": False}), 200
    return jsonify({**result, "connected": True})


# ─── Admin endpoints ─────────────────────────────────────────────────────────


@ghrm_bp.route("/api/v1/admin/ghrm/packages", methods=["GET"])
@require_auth
@require_admin
def admin_list_packages():
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 100)
    query = request.args.get("q") or None
    tariff_plan_id = request.args.get("tariff_plan_id") or None
    repo = GhrmSoftwarePackageRepository(db.session)
    if tariff_plan_id:
        pkg = repo.find_by_tariff_plan_id(tariff_plan_id)
        items = [pkg.to_dict()] if pkg else []
        return jsonify(
            {
                "items": items,
                "total": len(items),
                "page": 1,
                "per_page": per_page,
                "pages": 1,
            }
        )
    result = repo.find_all(page=page, per_page=per_page, query=query)
    result["items"] = [p.to_dict() for p in result["items"]]
    return jsonify(result)


@ghrm_bp.route("/api/v1/admin/ghrm/packages", methods=["POST"])
@require_auth
@require_admin
def admin_create_package():
    body = request.json or {}
    required = ("name", "slug", "github_owner", "github_repo", "tariff_plan_id")
    missing = [f for f in required if not body.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    repo = GhrmSoftwarePackageRepository(db.session)
    if repo.find_by_slug(body["slug"]):
        return jsonify({"error": "Slug already exists"}), 409
    pkg = GhrmSoftwarePackage(
        tariff_plan_id=body["tariff_plan_id"],
        name=body["name"],
        slug=body["slug"],
        description=body.get("description"),
        author_name=body.get("author_name"),
        icon_url=body.get("icon_url"),
        github_owner=body["github_owner"],
        github_repo=body["github_repo"],
        github_protected_branch=body.get("github_protected_branch", "release"),
        tech_specs=body.get("tech_specs", {}),
        related_slugs=body.get("related_slugs", []),
        sort_order=body.get("sort_order", 0),
    )
    repo.save(pkg)
    return jsonify(pkg.to_dict()), 201


@ghrm_bp.route("/api/v1/admin/ghrm/packages/<pkg_id>", methods=["PUT"])
@require_auth
@require_admin
def admin_update_package(pkg_id):
    repo = GhrmSoftwarePackageRepository(db.session)
    pkg = repo.find_by_id(pkg_id)
    if not pkg:
        return jsonify({"error": "Not found"}), 404
    body = request.json or {}
    updatable = (
        "name",
        "description",
        "author_name",
        "icon_url",
        "github_owner",
        "github_repo",
        "github_protected_branch",
        "tech_specs",
        "related_slugs",
        "sort_order",
        "is_active",
    )
    for field in updatable:
        if field in body:
            setattr(pkg, field, body[field])
    # Sync overrides
    if any(
        k in body
        for k in (
            "override_readme",
            "override_changelog",
            "override_docs",
            "admin_screenshots",
        )
    ):
        sync_repo = GhrmSoftwareSyncRepository(db.session)
        sync = sync_repo.find_by_package_id(pkg_id)
        if sync:
            for field in (
                "override_readme",
                "override_changelog",
                "override_docs",
                "admin_screenshots",
            ):
                if field in body:
                    setattr(sync, field, body[field])
            sync_repo.save(sync)
    repo.save(pkg)
    return jsonify(pkg.to_dict())


@ghrm_bp.route("/api/v1/admin/ghrm/packages/<pkg_id>", methods=["DELETE"])
@require_auth
@require_admin
def admin_delete_package(pkg_id):
    repo = GhrmSoftwarePackageRepository(db.session)
    if not repo.delete(pkg_id):
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@ghrm_bp.route("/api/v1/admin/ghrm/packages/<pkg_id>/rotate-key", methods=["POST"])
@require_auth
@require_admin
def admin_rotate_key(pkg_id):
    try:
        new_key = _pkg_svc().rotate_api_key(pkg_id)
        return jsonify({"sync_api_key": new_key})
    except GhrmPackageNotFoundError as e:
        return jsonify({"error": str(e)}), 404


@ghrm_bp.route("/api/v1/admin/ghrm/packages/<pkg_id>/sync", methods=["POST"])
@require_auth
@require_admin
def admin_sync_package(pkg_id):
    repo = GhrmSoftwarePackageRepository(db.session)
    pkg = repo.find_by_id(pkg_id)
    if not pkg:
        return jsonify({"error": "Not found"}), 404
    try:
        result = _pkg_svc().sync_package(pkg.sync_api_key)
        return jsonify({"ok": True, "sync": result})
    except (GhrmNotConfiguredError, GithubNotConfiguredError) as e:
        return jsonify({"error": str(e)}), 503
    except GhrmSyncAuthError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error(f"[GHRM] admin sync error: {e}", exc_info=True)
        return jsonify({"error": "Sync failed"}), 500


_VALID_PREVIEW_FIELDS = {"readme", "changelog", "screenshots"}


@ghrm_bp.route("/api/v1/admin/ghrm/packages/<pkg_id>/preview/<field>", methods=["GET"])
@require_auth
@require_admin
def admin_preview_field(pkg_id, field):
    if field not in _VALID_PREVIEW_FIELDS:
        return (
            jsonify(
                {
                    "error": f"Invalid field '{field}'. Must be one of: readme, changelog, screenshots"
                }
            ),
            400,
        )
    try:
        svc = _pkg_svc()
        if field == "readme":
            content = svc.preview_readme(pkg_id)
            return jsonify({"content": content})
        elif field == "changelog":
            content = svc.preview_changelog(pkg_id)
            return jsonify({"content": content})
        else:
            urls = svc.preview_screenshots(pkg_id)
            return jsonify({"urls": urls})
    except GhrmPackageNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except (GhrmNotConfiguredError, GithubNotConfiguredError, GhrmSyncAuthError) as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error(f"[GHRM] preview error: {e}", exc_info=True)
        return jsonify({"error": "Preview failed"}), 500


@ghrm_bp.route("/api/v1/admin/ghrm/packages/<pkg_id>/sync/<field>", methods=["POST"])
@require_auth
@require_admin
def admin_sync_field(pkg_id, field):
    if field not in _VALID_PREVIEW_FIELDS:
        return (
            jsonify(
                {
                    "error": f"Invalid field '{field}'. Must be one of: readme, changelog, screenshots"
                }
            ),
            400,
        )
    try:
        svc = _pkg_svc()
        result = svc.sync_field(pkg_id, field)
        return jsonify({"ok": True, "sync": result})
    except GhrmPackageNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except (GhrmNotConfiguredError, GithubNotConfiguredError, GhrmSyncAuthError) as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error(f"[GHRM] sync_field error: {e}", exc_info=True)
        return jsonify({"error": "Sync failed"}), 500


@ghrm_bp.route("/api/v1/admin/ghrm/access-log", methods=["GET"])
@require_auth
@require_admin
def admin_access_log():
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 100)
    user_id = request.args.get("user_id")
    log_repo = GhrmAccessLogRepository(db.session)
    if user_id:
        result = log_repo.find_by_user(user_id, page=page, per_page=per_page)
    else:
        # All logs — query directly
        from plugins.ghrm.src.models.ghrm_access_log import GhrmAccessLog

        q = db.session.query(GhrmAccessLog).order_by(GhrmAccessLog.created_at.desc())
        total = q.count()
        items = q.offset((page - 1) * per_page).limit(per_page).all()
        result = {
            "items": [i.to_dict() for i in items],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }
    return jsonify(result)


@ghrm_bp.route("/api/v1/admin/ghrm/access/sync/<user_id>", methods=["POST"])
@require_auth
@require_admin
def admin_sync_user_access(user_id):
    _access_svc().on_subscription_activated(user_id, "")
    return jsonify({"ok": True})


# ─── Widget config ────────────────────────────────────────────────────────────

_WIDGETS_PATH = os.path.join(os.path.dirname(__file__), "..", "widgets.json")

_DEFAULT_BREADCRUMB_CSS = (
    ".ghrm-breadcrumb {"
    " display: flex; align-items: center; gap: 6px; font-size: 13px;"
    " color: #6b7280; padding: 8px 0 16px; flex-wrap: wrap; }\n"
    ".ghrm-breadcrumb a { color: #3498db; text-decoration: none; }\n"
    ".ghrm-breadcrumb a:hover { text-decoration: underline; }\n"
    ".ghrm-breadcrumb__separator { color: #9ca3af; user-select: none; }\n"
    ".ghrm-breadcrumb__current { color: #374151; font-weight: 500; }"
)

_DEFAULT_WIDGETS = {
    "catalogue": {
        "id": "catalogue",
        "separator": "/",
        "root_name": "Home",
        "root_slug": "/",
        "show_category": True,
        "max_label_length": 40,
        "css": _DEFAULT_BREADCRUMB_CSS,
    },
    "detail": {
        "id": "detail",
        "separator": "/",
        "root_name": "Home",
        "root_slug": "/",
        "show_category": True,
        "max_label_length": 40,
        "css": _DEFAULT_BREADCRUMB_CSS,
    },
}

_WIDGET_ALLOWED_FIELDS = {
    "separator",
    "root_name",
    "root_slug",
    "show_category",
    "max_label_length",
    "css",
}


def _load_widgets() -> dict:
    try:
        with open(_WIDGETS_PATH) as f:
            return json.load(f)
    except Exception:
        return {k: dict(v) for k, v in _DEFAULT_WIDGETS.items()}


def _save_widgets(data: dict) -> None:
    with open(_WIDGETS_PATH, "w") as f:
        json.dump(data, f, indent=2)


@ghrm_bp.route("/api/v1/ghrm/widgets", methods=["GET"])
def get_widgets():
    """Return breadcrumb widget config — public read for fe-user rendering.

    Prefers the CMS widget (widget_type='vue-component', config.component_name='ghrm-breadcrumb')
    when one exists in the DB.  Falls back to widgets.json for backward compatibility.
    """
    _BREADCRUMB_FIELDS = (
        "separator",
        "root_name",
        "root_slug",
        "show_category",
        "max_label_length",
        "css",
    )
    try:
        from plugins.cms.src.models.cms_widget import CmsWidget as _CmsWidget

        candidates = (
            db.session.query(_CmsWidget)
            .filter(
                _CmsWidget.widget_type == "vue-component",
                _CmsWidget.is_active.is_(True),
            )
            .all()
        )
        cms_widget = next(
            (
                w
                for w in candidates
                if (w.content_json or {}).get("component") == "CmsBreadcrumb"
                or (w.config or {}).get("component_name")
                in ("CmsBreadcrumb", "ghrm-breadcrumb")
            ),
            None,
        )
        if cms_widget and cms_widget.config:
            cfg = {
                k: cms_widget.config[k]
                for k in _BREADCRUMB_FIELDS
                if k in cms_widget.config
            }
            cfg["id"] = "ghrm-breadcrumb"
            return jsonify({"widgets": [cfg]})
    except Exception:
        pass
    # Fallback: widgets.json
    widgets = _load_widgets()
    return jsonify({"widgets": list(widgets.values())})


@ghrm_bp.route("/api/v1/admin/ghrm/widgets", methods=["GET"])
@require_auth
@require_admin
def admin_get_widgets():
    """Admin: return all GHRM widget configs."""
    widgets = _load_widgets()
    return jsonify({"widgets": list(widgets.values())})


@ghrm_bp.route("/api/v1/admin/ghrm/widgets/<widget_id>", methods=["PUT"])
@require_auth
@require_admin
def admin_update_widget(widget_id):
    """Admin: update a widget config (General fields + CSS)."""
    widgets = _load_widgets()
    if widget_id not in widgets:
        return jsonify({"error": f"Widget '{widget_id}' not found"}), 404
    body = request.json or {}
    update = {k: v for k, v in body.items() if k in _WIDGET_ALLOWED_FIELDS}
    widgets[widget_id].update(update)
    result = dict(widgets[widget_id])
    _save_widgets(widgets)
    return jsonify(result)
