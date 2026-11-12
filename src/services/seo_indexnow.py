"""IndexNow ping for ghrm software packages (S154 D4) — cms stays optional.

Calls cms's stable ``ping_indexnow_urls`` seam through a lazy import (a silent
no-op when cms is absent or not enabled), mirroring the optional-dependency
pattern ``GhrmPackageSitemapProvider`` already uses for the sitemap registry
(D3). The public path is built from the SAME ``live_catalogue_base`` config
lookup the sitemap provider uses (DRY — never hardcoded, never recomputed),
gated by the package type's own fe-admin "indexable" switch (D7) — the
identical switch the sitemap provider checks.

ghrm's software categories (``software_category_slugs``) are a config list,
not a CRUD entity — there is no create/update/delete to hook for them, so only
packages are pinged here (NO OVERENGINEERING: no hook invented where none
naturally exists).

``notify_package_changed`` covers one row; ``notify_packages_changed`` batches
several rows into ONE IndexNow POST for the existing bulk-copy route (D4
batching) — unused today because package copies are always created inactive,
but kept available for symmetry with shop/booking.
"""
import logging
from typing import Iterable, List

from plugins.ghrm.src.services.plugin_config import (
    SEO_INDEX_PACKAGES_KEY,
    live_catalogue_base,
    seo_indexing_enabled,
)

logger = logging.getLogger(__name__)


def _import_ping_indexnow_urls():
    """cms's stable IndexNow ping seam (raises ``ImportError`` without cms)."""
    from plugins.cms.src.services.seo_wiring import ping_indexnow_urls

    return ping_indexnow_urls


def _ping_many(paths: List[str]) -> None:
    """Best-effort: a missing cms or a failing ping never breaks the caller."""
    try:
        ping_indexnow_urls = _import_ping_indexnow_urls()
    except ImportError:
        return
    try:
        ping_indexnow_urls(paths)
    except Exception as exc:  # pragma: no cover - defensive, best-effort
        logger.warning("[ghrm.indexnow] ping failed for %s: %s", paths, exc)


def _package_path(slug: str) -> str:
    return f"/{live_catalogue_base().strip('/')}/{slug}"


def notify_package_changed(slug: str) -> None:
    """Ping IndexNow of a changed package's public URL (switch-gated, D7)."""
    if not slug or not seo_indexing_enabled(SEO_INDEX_PACKAGES_KEY):
        return
    _ping_many([_package_path(slug)])


def notify_packages_changed(slugs: Iterable[str]) -> None:
    """Ping IndexNow once for several changed packages (switch-gated, D7)."""
    if not seo_indexing_enabled(SEO_INDEX_PACKAGES_KEY):
        return
    paths = [_package_path(slug) for slug in (slugs or []) if slug]
    if not paths:
        return
    _ping_many(paths)
