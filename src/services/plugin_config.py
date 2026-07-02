"""Single home for reading GHRM's runtime config (DRY).

Reads fresh from the shared ``config_store`` on every call (multi-worker safe,
admin changes take effect without restart) and falls back to the plugin's
``DEFAULT_CONFIG`` for any missing key. Mirrors the subscription plugin's helper.
"""
from typing import Any, Dict

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
