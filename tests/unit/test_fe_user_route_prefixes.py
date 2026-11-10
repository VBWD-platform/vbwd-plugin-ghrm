"""S154 D9 — ghrm owns its public fe-user routes, catalogue base from live config.

ghrm's fe-user plugin mounts ``/<catalogue base>`` (``/search``,
``/:package_slug``) and the github auth callback under ``/ghrm``. The base is
``software_catalogue_cms_page_slug`` — operator-configurable, so it is read from
the plugin's live config (bundled ``config.json`` as the default) and never
hardcoded. ghrm declares the prefixes through ``fe_user_route_prefixes()``; it
imports no theme module (S152 R1), so it behaves the same with no theme
installed.

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY/Liskov; clean code; no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import json
import os
import sys
from unittest.mock import patch

from plugins.ghrm import GhrmPlugin

GITHUB_AUTH_CALLBACK_PREFIX = "/ghrm"


def _bundled_catalogue_slug() -> str:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
    with open(path) as config_file:
        return json.load(config_file)["software_catalogue_cms_page_slug"]


def _initialized_plugin(config: dict) -> GhrmPlugin:
    plugin = GhrmPlugin()
    plugin.initialize(config)
    return plugin


def test_the_configured_catalogue_base_and_the_auth_callback_are_declared():
    plugin = _initialized_plugin({"software_catalogue_cms_page_slug": "my-catalogue"})

    assert plugin.fe_user_route_prefixes() == [
        GITHUB_AUTH_CALLBACK_PREFIX,
        "/my-catalogue",
    ]


def test_the_bundled_default_base_applies_when_the_operator_set_none():
    plugin = _initialized_plugin({})

    assert plugin.fe_user_route_prefixes() == [
        GITHUB_AUTH_CALLBACK_PREFIX,
        f"/{_bundled_catalogue_slug()}",
    ]


def test_a_slashed_base_is_normalised_to_one_absolute_prefix():
    plugin = _initialized_plugin({"software_catalogue_cms_page_slug": "/my-catalogue/"})

    assert plugin.fe_user_route_prefixes()[-1] == "/my-catalogue"


def test_declaring_the_prefixes_works_with_no_theme_plugin_installed():
    plugin = _initialized_plugin({"software_catalogue_cms_page_slug": "my-catalogue"})

    # ``None`` in sys.modules makes any ``import plugins.theme`` raise ImportError.
    with patch.dict(sys.modules, {"plugins.theme": None}):
        assert plugin.fe_user_route_prefixes()[-1] == "/my-catalogue"
