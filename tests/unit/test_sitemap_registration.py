"""S154 P2 / D3 — ghrm registers its sitemap providers whenever cms is INSTALLED.

cms owns the registry and only cms's own sitemap routes read it, so ghrm
registers as soon as ``plugins.cms.src.services.seo_registry`` imports —
independent of cms's enable state and of plugin enable order. With cms not
importable the registration is a silent no-op and ghrm still enables;
``on_disable`` takes the providers out again. The cms registry is faked (the
per-plugin CI clones ghrm without cms).

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
"""
from types import SimpleNamespace

import pytest
from flask import Flask

from plugins.ghrm import GhrmPlugin

GHRM_PROVIDER_NAMES = ["ghrm-categories", "ghrm-packages"]


class _FakeSitemapRegistry:
    """The two cms registry functions ghrm calls, plus cms's entry type."""

    SitemapEntry = SimpleNamespace

    def __init__(self):
        self.providers = {}

    def register_sitemap_provider(self, provider):
        self.providers[provider.name] = provider

    def unregister_sitemap_provider(self, provider):
        self.providers.pop(provider.name, None)


@pytest.fixture
def fake_registry(monkeypatch):
    registry = _FakeSitemapRegistry()
    monkeypatch.setattr(
        GhrmPlugin, "_import_cms_sitemap_registry", staticmethod(lambda: registry)
    )
    return registry


def _register() -> GhrmPlugin:
    plugin = GhrmPlugin()
    with Flask(__name__).app_context():
        plugin._register_sitemap_providers()
    return plugin


def test_cms_installed_registers_both_providers(fake_registry):
    _register()

    assert sorted(fake_registry.providers) == GHRM_PROVIDER_NAMES


def test_disable_unregisters_both_providers(fake_registry):
    plugin = _register()

    plugin._unregister_sitemap_providers()

    assert fake_registry.providers == {}


def test_cms_not_importable_is_a_no_op(monkeypatch):
    def _missing_cms():
        raise ImportError("No module named 'plugins.cms'")

    monkeypatch.setattr(
        GhrmPlugin, "_import_cms_sitemap_registry", staticmethod(_missing_cms)
    )

    plugin = _register()

    plugin._unregister_sitemap_providers()  # never raises without registration
