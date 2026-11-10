"""S154 D9 — public catalogue reads follow the LIVE config, not ``_config``.

The fe-admin form saves to the shared config store only, so the plugin's
in-memory ``_config`` goes stale until the api restarts. The SPA routes (from
``/ghrm/config`` and ``/ghrm/categories``), the theme hand-off prefix and the
sitemap must therefore all read the config store — merged over the defaults —
so they agree on the catalogue base and category list right after an edit.
Here the in-memory config is deliberately stale and must be ignored.

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
"""
from unittest.mock import MagicMock, patch

import pytest

from plugins.ghrm import GhrmPlugin
from plugins.ghrm.src.services.plugin_config import live_catalogue_base
from plugins.ghrm.tests.unit.live_config_app import app_with_saved_config

SAVED = {
    "software_catalogue_cms_page_slug": "apps",
    "software_category_slugs": ["tools", "themes"],
}
STALE_IN_MEMORY = {
    "software_catalogue_cms_page_slug": "stale-base",
    "software_category_slugs": ["stale-category"],
}
CATALOG_READ_MODEL = (
    "plugins.subscription.subscription.services.catalog_read_model.CatalogReadModel"
)


@pytest.fixture
def stale_in_memory_config():
    with patch("plugins.ghrm.src.routes._cfg", return_value=STALE_IN_MEMORY):
        yield


def test_public_config_reports_the_saved_catalogue_base(stale_in_memory_config):
    app = app_with_saved_config(SAVED)

    body = app.test_client().get("/api/v1/ghrm/config").get_json()

    assert body["catalogue_page_slug"] == "apps"


def test_public_categories_are_the_saved_category_list(stale_in_memory_config):
    app = app_with_saved_config(SAVED)
    catalog = MagicMock()
    catalog.category_labels_by_slugs.return_value = {}

    with patch(CATALOG_READ_MODEL, return_value=catalog):
        body = app.test_client().get("/api/v1/ghrm/categories").get_json()

    assert [category["slug"] for category in body["categories"]] == [
        "tools",
        "themes",
    ]


def test_the_sitemap_base_matches_the_public_config(stale_in_memory_config):
    app = app_with_saved_config(SAVED)

    body = app.test_client().get("/api/v1/ghrm/config").get_json()
    with app.app_context():
        assert live_catalogue_base() == body["catalogue_page_slug"]


def test_the_spa_hand_off_prefix_is_the_saved_base():
    plugin = GhrmPlugin()
    plugin.initialize(STALE_IN_MEMORY)

    with app_with_saved_config(SAVED).app_context():
        assert plugin.fe_user_route_prefixes()[-1] == "/apps"


def test_the_admin_public_detail_path_uses_the_saved_base(stale_in_memory_config):
    from plugins.ghrm.src.routes import _build_public_detail_path_resolver

    with app_with_saved_config(SAVED).app_context():
        resolve_public_detail_path = _build_public_detail_path_resolver()

    assert resolve_public_detail_path({"slug": "cli-tool"}) == "/apps/cli-tool"
