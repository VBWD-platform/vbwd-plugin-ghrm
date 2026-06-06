"""Unit tests — GET /api/v1/ghrm/config exposes the D3 security policy flag.

The public config response (consumed by fe-admin to decide whether to offer
the write+ access-level options) must include
``allow_extensive_github_permissions`` reflecting the configured value, and
default to ``false`` when the plugin config does not set it.
"""
from unittest.mock import patch

import pytest
from flask import Flask

from plugins.ghrm.src.routes import ghrm_bp


def _make_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(ghrm_bp)
    return app


@pytest.fixture
def app():
    return _make_app()


class TestPublicConfigExposesExtensivePermissionsFlag:
    def test_defaults_to_false_when_config_absent(self, app):
        with app.test_client() as client:
            with patch("plugins.ghrm.src.routes._cfg", return_value={}):
                response = client.get("/api/v1/ghrm/config")
        assert response.status_code == 200
        body = response.get_json()
        assert "allow_extensive_github_permissions" in body
        assert body["allow_extensive_github_permissions"] is False

    def test_reflects_enabled_value(self, app):
        with app.test_client() as client:
            with patch(
                "plugins.ghrm.src.routes._cfg",
                return_value={"allow_extensive_github_permissions": True},
            ):
                response = client.get("/api/v1/ghrm/config")
        assert response.get_json()["allow_extensive_github_permissions"] is True

    def test_reflects_explicit_disabled_value(self, app):
        with app.test_client() as client:
            with patch(
                "plugins.ghrm.src.routes._cfg",
                return_value={"allow_extensive_github_permissions": False},
            ):
                response = client.get("/api/v1/ghrm/config")
        assert response.get_json()["allow_extensive_github_permissions"] is False

    def test_still_returns_layout_slugs(self, app):
        with app.test_client() as client:
            with patch("plugins.ghrm.src.routes._cfg", return_value={}):
                response = client.get("/api/v1/ghrm/config")
        body = response.get_json()
        assert "catalogue_page_slug" in body
        assert "detail_page_slug" in body
