"""Unit tests — GHRM widget config routes."""
import json
import pytest
from unittest.mock import patch, MagicMock


# ─── Auth helpers ─────────────────────────────────────────────────────────────

_FAKE_USER_ID = "00000000-0000-0000-0000-000000000001"
_FAKE_TOKEN = "fake-admin-token"


def _fake_admin_user():
    from src.models.enums import UserRole, UserStatus

    user = MagicMock()
    user.id = _FAKE_USER_ID
    user.role = UserRole.ADMIN
    user.status = MagicMock()
    user.status.value = "ACTIVE"
    return user


def _auth_patches():
    """Return context managers that bypass require_auth + require_admin."""
    return [
        patch("src.services.auth_service.AuthService.verify_token", return_value=_FAKE_USER_ID),
        patch("src.repositories.user_repository.UserRepository.find_by_id", return_value=_fake_admin_user()),
    ]


def _admin_headers():
    return {"Authorization": f"Bearer {_FAKE_TOKEN}", "Content-Type": "application/json"}


# ─── App factory ─────────────────────────────────────────────────────────────


def _make_app():
    from flask import Flask
    from plugins.ghrm.src.routes import ghrm_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(ghrm_bp)
    return app


@pytest.fixture
def app():
    return _make_app()


def _default_widgets():
    from plugins.ghrm.src.routes import _DEFAULT_WIDGETS

    return {k: dict(v) for k, v in _DEFAULT_WIDGETS.items()}


# ─── Public GET /api/v1/ghrm/widgets ─────────────────────────────────────────


class TestGetWidgetsPublic:
    def test_returns_200_with_widgets_list(self, app):
        with app.test_client() as c:
            with patch("plugins.ghrm.src.routes._load_widgets", return_value=_default_widgets()):
                resp = c.get("/api/v1/ghrm/widgets")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "widgets" in data
        assert isinstance(data["widgets"], list)

    def test_returns_both_catalogue_and_detail_widgets(self, app):
        with app.test_client() as c:
            with patch("plugins.ghrm.src.routes._load_widgets", return_value=_default_widgets()):
                resp = c.get("/api/v1/ghrm/widgets")
        widgets = {w["id"]: w for w in resp.get_json()["widgets"]}
        assert "catalogue" in widgets
        assert "detail" in widgets

    def test_widget_has_required_fields(self, app):
        with app.test_client() as c:
            with patch("plugins.ghrm.src.routes._load_widgets", return_value=_default_widgets()):
                resp = c.get("/api/v1/ghrm/widgets")
        widget = resp.get_json()["widgets"][0]
        for field in ("id", "separator", "root_name", "root_slug", "show_category", "max_label_length", "css"):
            assert field in widget, f"Missing field: {field}"

    def test_default_separator_is_slash(self, app):
        with app.test_client() as c:
            with patch("plugins.ghrm.src.routes._load_widgets", return_value=_default_widgets()):
                resp = c.get("/api/v1/ghrm/widgets")
        for w in resp.get_json()["widgets"]:
            assert w["separator"] == "/"

    def test_no_auth_required(self, app):
        """Public endpoint — no Authorization header needed."""
        with app.test_client() as c:
            with patch("plugins.ghrm.src.routes._load_widgets", return_value=_default_widgets()):
                resp = c.get("/api/v1/ghrm/widgets")
        assert resp.status_code == 200


# ─── Admin GET /api/v1/admin/ghrm/widgets ────────────────────────────────────


class TestAdminGetWidgets:
    def test_returns_200_with_full_widget_list(self, app):
        patches = _auth_patches()
        with app.test_client() as c:
            with patches[0], patches[1]:
                with patch("plugins.ghrm.src.routes._load_widgets", return_value=_default_widgets()):
                    resp = c.get("/api/v1/admin/ghrm/widgets", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["widgets"]) == 2

    def test_requires_auth(self, app):
        with app.test_client() as c:
            resp = c.get("/api/v1/admin/ghrm/widgets")
        assert resp.status_code == 401


# ─── Admin PUT /api/v1/admin/ghrm/widgets/<widget_id> ────────────────────────


class TestAdminUpdateWidget:
    def _put(self, widget_id, body):
        app = _make_app()
        storage = _default_widgets()

        def fake_save(data):
            storage.clear()
            storage.update(data)

        patches = _auth_patches()
        with app.test_client() as c:
            with patches[0], patches[1]:
                with patch("plugins.ghrm.src.routes._load_widgets", return_value=storage), patch(
                    "plugins.ghrm.src.routes._save_widgets", side_effect=fake_save
                ):
                    return c.put(
                        f"/api/v1/admin/ghrm/widgets/{widget_id}",
                        data=json.dumps(body),
                        headers=_admin_headers(),
                    )

    def test_update_separator(self):
        resp = self._put("catalogue", {"separator": ">"})
        assert resp.status_code == 200
        assert resp.get_json()["separator"] == ">"

    def test_update_root_name(self):
        resp = self._put("detail", {"root_name": "Start"})
        assert resp.status_code == 200
        assert resp.get_json()["root_name"] == "Start"

    def test_update_root_slug(self):
        resp = self._put("catalogue", {"root_slug": "/home"})
        assert resp.status_code == 200
        assert resp.get_json()["root_slug"] == "/home"

    def test_update_css(self):
        resp = self._put("catalogue", {"css": ".ghrm-breadcrumb { color: red; }"})
        assert resp.status_code == 200
        assert "color: red" in resp.get_json()["css"]

    def test_update_show_category(self):
        resp = self._put("detail", {"show_category": False})
        assert resp.status_code == 200
        assert resp.get_json()["show_category"] is False

    def test_update_max_label_length(self):
        resp = self._put("catalogue", {"max_label_length": 20})
        assert resp.status_code == 200
        assert resp.get_json()["max_label_length"] == 20

    def test_unknown_widget_id_returns_404(self):
        resp = self._put("nonexistent", {"separator": ">"})
        assert resp.status_code == 404
        assert "not found" in resp.get_json()["error"].lower()

    def test_unknown_fields_are_ignored(self):
        resp = self._put("catalogue", {"separator": "›", "hacked_field": "evil"})
        assert resp.status_code == 200
        assert "hacked_field" not in resp.get_json()

    def test_id_field_not_overwritten(self):
        resp = self._put("catalogue", {"id": "injected"})
        assert resp.status_code == 200
        assert resp.get_json()["id"] == "catalogue"

    def test_requires_auth(self):
        app = _make_app()
        with app.test_client() as c:
            resp = c.put(
                "/api/v1/admin/ghrm/widgets/catalogue",
                data=json.dumps({"separator": ">"}),
                content_type="application/json",
            )
        assert resp.status_code == 401
