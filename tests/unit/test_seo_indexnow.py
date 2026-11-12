"""S154 D4 — ghrm pings cms's IndexNow seam on software-package change.

Unit level: the cms import is monkeypatched (no DB, no cms source needed), so
these run against the per-plugin isolated CI (ghrm without cms) too, where the
import naturally raises ``ImportError`` and every assertion still holds (no-op,
never a crash).

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
"""
import logging
from unittest.mock import MagicMock

from plugins.ghrm.src.services import seo_indexnow


def _switch_on(monkeypatch, enabled=True):
    monkeypatch.setattr(seo_indexnow, "seo_indexing_enabled", lambda key: enabled)


def _fixed_catalogue_base(monkeypatch, base="software"):
    monkeypatch.setattr(seo_indexnow, "live_catalogue_base", lambda: base)


def _fake_cms_ping(monkeypatch):
    ping = MagicMock()
    monkeypatch.setattr(seo_indexnow, "_import_ping_indexnow_urls", lambda: ping)
    return ping


def test_notify_package_changed_pings_the_package_path_when_switch_on(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_catalogue_base(monkeypatch, "software")

    seo_indexnow.notify_package_changed("my-tool")

    ping.assert_called_once_with(["/software/my-tool"])


def test_notify_package_changed_noop_when_switch_off(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, False)
    _fixed_catalogue_base(monkeypatch, "software")

    seo_indexnow.notify_package_changed("my-tool")

    ping.assert_not_called()


def test_notify_package_changed_noop_without_a_slug(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_catalogue_base(monkeypatch, "software")

    seo_indexnow.notify_package_changed("")
    seo_indexnow.notify_package_changed(None)

    ping.assert_not_called()


def test_notify_package_changed_respects_the_configured_catalogue_base(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_catalogue_base(monkeypatch, "store")

    seo_indexnow.notify_package_changed("my-tool")

    ping.assert_called_once_with(["/store/my-tool"])


def test_notify_packages_changed_batches_every_slug_into_one_ping(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, True)
    _fixed_catalogue_base(monkeypatch, "software")

    seo_indexnow.notify_packages_changed(["my-tool", "other-tool"])

    ping.assert_called_once_with(["/software/my-tool", "/software/other-tool"])


def test_notify_packages_changed_noop_when_switch_off(monkeypatch):
    ping = _fake_cms_ping(monkeypatch)
    _switch_on(monkeypatch, False)
    _fixed_catalogue_base(monkeypatch, "software")

    seo_indexnow.notify_packages_changed(["my-tool"])

    ping.assert_not_called()


def test_noop_when_cms_is_not_importable(monkeypatch):
    def _missing_cms():
        raise ImportError("No module named 'plugins.cms'")

    monkeypatch.setattr(seo_indexnow, "_import_ping_indexnow_urls", _missing_cms)
    _switch_on(monkeypatch, True)
    _fixed_catalogue_base(monkeypatch, "software")

    # Never raises — the plugin keeps working with cms absent/disabled.
    seo_indexnow.notify_package_changed("my-tool")


def test_ping_failure_is_swallowed(monkeypatch, caplog):
    ping = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(seo_indexnow, "_import_ping_indexnow_urls", lambda: ping)
    _switch_on(monkeypatch, True)
    _fixed_catalogue_base(monkeypatch, "software")

    with caplog.at_level(logging.WARNING):
        seo_indexnow.notify_package_changed("my-tool")

    assert caplog.records
