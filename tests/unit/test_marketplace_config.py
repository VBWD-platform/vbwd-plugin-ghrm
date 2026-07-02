"""GHRM vendor-mode config flag defaults OFF (classic behaviour unchanged).

``marketplace_enabled`` gates the vendor self-service package route. It must
default to ``False`` so an install that never enables the marketplace behaves
exactly as before.
"""


def test_default_config_marketplace_disabled():
    from plugins.ghrm import DEFAULT_CONFIG

    assert DEFAULT_CONFIG.get("marketplace_enabled") is False


def test_marketplace_enabled_reads_config():
    from flask import Flask

    from plugins.ghrm.src.services import plugin_config

    app = Flask(__name__)

    class _Store:
        def __init__(self, value):
            self._value = value

        def get_config(self, plugin_name):
            assert plugin_name == "ghrm"
            return {"marketplace_enabled": self._value}

    with app.app_context():
        app.config_store = _Store(True)  # type: ignore[attr-defined]
        assert plugin_config.marketplace_enabled() is True
        app.config_store = _Store(False)  # type: ignore[attr-defined]
        assert plugin_config.marketplace_enabled() is False


def test_marketplace_enabled_defaults_false_without_store():
    from flask import Flask

    from plugins.ghrm.src.services import plugin_config

    app = Flask(__name__)
    with app.app_context():
        # No config_store attached → fall back to DEFAULT_CONFIG (False).
        assert plugin_config.marketplace_enabled() is False
