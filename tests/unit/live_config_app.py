"""A bare Flask app whose ``config_store`` holds a saved ghrm config (test helper).

Saving the fe-admin plugin form writes only the shared config store; it never
touches the plugin's in-memory ``_config``. Tests that pin "live" reads build
their app here so the saved values are the only place the config lives.
"""
from flask import Flask

from plugins.ghrm.src.routes import ghrm_bp


class SavedConfigStore:
    """The one ``get_config`` call ghrm makes on the core config store."""

    def __init__(self, saved: dict):
        self._saved = saved

    def get_config(self, plugin_name: str) -> dict:
        assert plugin_name == "ghrm"
        return self._saved


def app_with_saved_config(saved: dict) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(ghrm_bp)
    setattr(app, "config_store", SavedConfigStore(saved))
    return app
