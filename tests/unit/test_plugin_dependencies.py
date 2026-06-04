"""GHRM declares its subscription dependency (S49.0)."""
from plugins.ghrm import GhrmPlugin


def test_ghrm_declares_subscription_dependency():
    assert GhrmPlugin().metadata.dependencies == ["subscription"]
