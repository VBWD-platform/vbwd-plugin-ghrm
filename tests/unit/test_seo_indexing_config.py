"""S154 D7 — ghrm's fe-admin "indexable" switches and the live sitemap sources.

``seo_index_packages`` defaults ON and ``seo_index_categories`` OFF. Both ship in
``config.json`` (defaults) and ``admin-config.json`` (the fe-admin form), and the
runtime reads merge the saved config over the defaults on every call, like the
catalogue base and the shown category slugs the sitemap builds its URLs from.

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
"""
import json
import os

import pytest
from plugins.ghrm import DEFAULT_CONFIG
from plugins.ghrm.src.services import plugin_config
from plugins.ghrm.tests.unit.live_config_app import app_with_saved_config

PLUGIN_DIRECTORY = os.path.join(os.path.dirname(__file__), "..", "..")
SWITCH_DEFAULTS = {"seo_index_packages": True, "seo_index_categories": False}


def _read_json(file_name: str) -> dict:
    with open(os.path.join(PLUGIN_DIRECTORY, file_name)) as json_file:
        return json.load(json_file)


@pytest.mark.parametrize("config_key,default", SWITCH_DEFAULTS.items())
def test_switch_defaults_ship_in_config_json_and_default_config(config_key, default):
    assert _read_json("config.json")[config_key] is default
    assert DEFAULT_CONFIG[config_key] is default


@pytest.mark.parametrize("config_key", SWITCH_DEFAULTS)
def test_switches_render_as_fe_admin_checkboxes(config_key):
    fields = [
        field
        for tab in _read_json("admin-config.json")["tabs"]
        for field in tab["fields"]
    ]

    matching = [field for field in fields if field["key"] == config_key]

    assert len(matching) == 1
    assert matching[0]["component"] == "checkbox"


@pytest.mark.parametrize("config_key,default", SWITCH_DEFAULTS.items())
def test_an_unsaved_switch_reads_its_default(config_key, default):
    with app_with_saved_config({}).app_context():
        assert plugin_config.seo_indexing_enabled(config_key) is default


def test_a_saved_switch_wins_over_its_default():
    saved = {"seo_index_packages": False, "seo_index_categories": True}

    with app_with_saved_config(saved).app_context():
        assert plugin_config.seo_indexing_enabled("seo_index_packages") is False
        assert plugin_config.seo_indexing_enabled("seo_index_categories") is True


def test_the_live_catalogue_base_is_the_saved_slug():
    saved = {"software_catalogue_cms_page_slug": "apps"}

    with app_with_saved_config(saved).app_context():
        assert plugin_config.live_catalogue_base() == "apps"


def test_the_live_catalogue_base_defaults_to_the_bundled_slug():
    with app_with_saved_config({}).app_context():
        assert (
            plugin_config.live_catalogue_base()
            == _read_json("config.json")["software_catalogue_cms_page_slug"]
        )


def test_the_shown_category_slugs_are_the_saved_ones():
    saved = {"software_category_slugs": ["backend", "tools"]}

    with app_with_saved_config(saved).app_context():
        assert plugin_config.shown_category_slugs() == ["backend", "tools"]


def test_the_shown_category_slugs_default_when_none_are_saved():
    with app_with_saved_config({"software_category_slugs": []}).app_context():
        assert (
            plugin_config.shown_category_slugs()
            == _read_json("config.json")["software_category_slugs"]
        )
