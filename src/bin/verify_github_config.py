#!/usr/bin/env python
"""Verify this instance's GHRM GitHub configuration against REAL GitHub.

Runs the offline-testable verifier (``github_config_verifier``) against the
running instance's live ghrm plugin config — the SAME source the routes read
(``current_app.plugin_manager.get_plugin("ghrm")._config``) — so an operator can
PROVE on any box (local or prod) whether the GitHub credentials are valid,
WITHOUT any human OAuth click-through.

Usage (inside the api container):
    python /app/plugins/ghrm/src/bin/verify_github_config.py

Exit code:
    0  no check FAILed (all PASS / WARN / SKIP)
    1  at least one check FAILed  -> usable in ops / CI

Secrets are never printed: only the (public, masked) client_id and PASS/FAIL/
WARN/SKIP statuses are shown.
"""
import sys

sys.path.insert(0, "/app")

_STATUS_ORDER = {"PASS": 0, "FAIL": 1, "WARN": 2, "SKIP": 3}
_LEADER_WIDTH = 34


def _format_line(result) -> str:
    """Render one aligned report line: ``name .... STATUS  (code) message``."""
    leader = f"{result.name} "
    leader = leader + "." * max(0, _LEADER_WIDTH - len(leader)) + " "
    code = f"({result.http_code}) " if result.http_code is not None else ""
    return f"{leader}{result.status}  {code}{result.message}".rstrip()


def _load_ghrm_config() -> dict:
    """Return the live ghrm ``_config`` via the app's plugin manager."""
    from vbwd.app import create_app

    app = create_app()
    with app.app_context():
        plugin_manager = getattr(app, "plugin_manager", None)
        if plugin_manager is None:
            raise SystemExit("plugin manager is not available on this instance")
        plugin = plugin_manager.get_plugin("ghrm")
        if plugin is None:
            raise SystemExit("ghrm plugin is not registered on this instance")
        return dict(plugin._config or {})


def main() -> int:
    from plugins.ghrm.src.services.github_config_verifier import FAIL, verify_all

    config = _load_ghrm_config()
    results = verify_all(config)

    print("\n=== GHRM GitHub configuration verification ===\n")
    for result in results:
        print(_format_line(result))

    failed = [result for result in results if result.status == FAIL]
    counts = {status: 0 for status in _STATUS_ORDER}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = ", ".join(f"{counts[status]} {status}" for status in _STATUS_ORDER)
    print(f"\nSummary: {summary}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
