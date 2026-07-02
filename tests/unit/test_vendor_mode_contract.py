"""GHRM vendor-mode contract + decoupling oracles.

A GHRM package rides a subscription plan: it is sold as a subscription and the
plan carries ``vendor_id``, so subscription's checkout stamps the buyer invoice
line automatically on purchase — GHRM adds no stamp of its own. These tests pin
the shared attribution key literal (so it can never drift from the documented
``vendor_id`` convention) and prove GHRM's source names no ``plugins.marketplace``
import (the money path stays decoupled).
"""
import os


GHRM_SOURCE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src")
)


def test_vendor_id_key_literal_is_vendor_id():
    from plugins.ghrm.src.constants import VENDOR_ID_KEY

    # Pinned to the documented marketplace convention WITHOUT importing
    # marketplace — DRY without inverting the dependency arrow.
    assert VENDOR_ID_KEY == "vendor_id"


def _python_files(root):
    for current_dir, _dirs, files in os.walk(root):
        if "__pycache__" in current_dir:
            continue
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(current_dir, name)


def test_ghrm_source_does_not_import_marketplace():
    offenders = []
    for path in _python_files(GHRM_SOURCE_ROOT):
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        if "plugins.marketplace" in content or "from plugins import marketplace" in (
            content
        ):
            offenders.append(path)
    assert not offenders, (
        "GHRM must not depend on the marketplace plugin — keep the money path "
        f"decoupled (the plan carries vendor_id, never import): {offenders}"
    )
