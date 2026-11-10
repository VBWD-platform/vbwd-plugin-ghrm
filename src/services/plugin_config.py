"""Single home for reading GHRM's runtime config (DRY).

Reads fresh from the shared ``config_store`` on every call (multi-worker safe,
admin changes take effect without restart) and falls back to the plugin's
``DEFAULT_CONFIG`` for any missing key. Mirrors the subscription plugin's helper.
"""
from typing import Any, Dict, List

from flask import current_app


def ghrm_config() -> Dict[str, Any]:
    """The merged ghrm config: ``DEFAULT_CONFIG`` overlaid with saved values."""
    from plugins.ghrm import DEFAULT_CONFIG

    merged = {**DEFAULT_CONFIG}
    config_store = getattr(current_app, "config_store", None)
    if config_store is not None:
        merged.update(config_store.get_config("ghrm") or {})
    return merged


def marketplace_enabled() -> bool:
    """Whether GHRM vendor-mode (the self-service vendor package route) is on."""
    return bool(ghrm_config().get("marketplace_enabled", False))


SEO_INDEX_PACKAGES_KEY = "seo_index_packages"
SEO_INDEX_CATEGORIES_KEY = "seo_index_categories"
SHOWN_CATEGORY_SLUGS_KEY = "software_category_slugs"


def seo_indexing_enabled(config_key: str) -> bool:
    """Whether the fe-admin "indexable" switch ``config_key`` is on (S154 D7)."""
    return bool(ghrm_config().get(config_key, False))


def live_catalogue_base() -> str:
    """The catalogue base slug (``software`` on vbwd.cc) from the live config.

    Operator-configurable (S154 D9), so it is never hardcoded: the saved value
    wins and the bundled ``config.json`` is the default.
    """
    from plugins.ghrm.src.routes import resolve_catalogue_page_slug

    return resolve_catalogue_page_slug(ghrm_config())


def shown_category_slugs() -> List[str]:
    """The categories the public catalogue shows; the bundled ones when none saved."""
    from plugins.ghrm import DEFAULT_CONFIG

    return list(
        ghrm_config().get(SHOWN_CATEGORY_SLUGS_KEY)
        or DEFAULT_CONFIG[SHOWN_CATEGORY_SLUGS_KEY]
    )
