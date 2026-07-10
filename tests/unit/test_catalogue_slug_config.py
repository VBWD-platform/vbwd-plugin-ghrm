"""Unit tests — the catalogue page slug default is ``software`` (S127).

The GHRM software catalogue is now ONE page (``software``) with in-widget
filters, not one CMS page per category. The bundled ``config.json`` default for
``software_catalogue_cms_page_slug`` is therefore ``software``; a DB plugin
config value still overrides it (single source of truth stays the config key —
nothing hardcodes the literal ``software`` in code).

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY; no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import json
import os
from unittest.mock import patch

import pytest
from flask import Flask

from plugins.ghrm.src.routes import ghrm_bp


def _config_json() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "config.json")
    with open(path) as config_file:
        return json.load(config_file)


def test_config_json_catalogue_slug_default_is_software():
    assert _config_json()["software_catalogue_cms_page_slug"] == "software"


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(ghrm_bp)
    return app


class TestPublicConfigCatalogueSlug:
    def test_defaults_to_software_when_db_config_absent(self, app):
        with app.test_client() as client:
            with patch("plugins.ghrm.src.routes._cfg", return_value={}):
                response = client.get("/api/v1/ghrm/config")
        assert response.status_code == 200
        assert response.get_json()["catalogue_page_slug"] == "software"

    def test_db_config_overrides_default(self, app):
        with app.test_client() as client:
            with patch(
                "plugins.ghrm.src.routes._cfg",
                return_value={"software_catalogue_cms_page_slug": "custom-catalogue"},
            ):
                response = client.get("/api/v1/ghrm/config")
        assert response.get_json()["catalogue_page_slug"] == "custom-catalogue"
