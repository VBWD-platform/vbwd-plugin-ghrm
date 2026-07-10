"""Unit tests — the GHRM catalogue widget passes ``items_per_page`` as a PROP.

The CMS ``CmsWidgetRenderer.vue`` spreads ONLY ``content_json.props`` onto the
resolved vue component (``v-bind="content_json?.props || {}"``); it reads the
component *name* from ``content_json.component`` separately. A top-level
``items_per_page`` on ``content_json`` is therefore silently ignored — the prop
must live under ``content_json.props``.

Both seed paths must agree (they must not drift):
  * the script constant ``populate_ghrm.CATALOGUE_WIDGETS``, and
  * the import envelope ``docs/imports/widgets/ghrm-categories.json``.

Engineering requirements (binding, restated): TDD-first; DevOps-first (no DB
needed here); SOLID/DI/DRY (one widget definition per seed path, asserted at
unit speed); clean code; no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import json
import os

import plugins.cms as cms_package
from plugins.ghrm.src.bin import populate_ghrm


def _catalogue_widgets_by_slug() -> dict:
    return {widget["slug"]: widget for widget in populate_ghrm.CATALOGUE_WIDGETS}


class TestScriptWidgetProps:
    def test_categories_widget_nests_items_per_page_under_props(self):
        content = _catalogue_widgets_by_slug()["ghrm-categories"]["content_json"]
        assert content["component"] == "GhrmCatalogueContent"
        assert content["props"]["items_per_page"] == 12
        # A top-level items_per_page would be dropped by the renderer.
        assert "items_per_page" not in content

    def test_detail_widget_keeps_component_and_drops_meaningless_prop(self):
        content = _catalogue_widgets_by_slug()["ghrm-software-detail"]["content_json"]
        assert content["component"] == "GhrmPackageDetail"
        # The detail widget does not take items_per_page — it must not carry it
        # (neither top-level nor under props).
        assert "items_per_page" not in content
        assert "items_per_page" not in content.get("props", {})


class TestImportWidgetProps:
    def _import_widget(self, slug: str) -> dict:
        imports_dir = os.path.join(
            os.path.dirname(cms_package.__file__), "docs", "imports", "widgets"
        )
        with open(os.path.join(imports_dir, f"{slug}.json")) as widget_file:
            envelope = json.load(widget_file)
        rows = envelope["cms_widgets"]
        return next(row for row in rows if row["slug"] == slug)

    def test_import_categories_widget_nests_items_per_page_under_props(self):
        content = self._import_widget("ghrm-categories")["content_json"]
        assert content["component"] == "GhrmCatalogueContent"
        assert content["props"]["items_per_page"] == 12
        assert "items_per_page" not in content
