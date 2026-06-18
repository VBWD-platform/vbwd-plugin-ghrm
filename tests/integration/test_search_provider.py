"""GhrmPackageSearchProvider — search hits + get_detail over real rows.

A query finds ACTIVE software packages by name/slug; the hit carries the
query-driven public ``/category/search?q=<slug>`` url (the always-available
fallback — a package's category is owned by the subscription catalog and not
cheaply available from the package row) and no price (ghrm has no price column);
``get_detail`` re-resolves by slug.

Skip-guard: a ghrm package FK-references a subscription tariff plan, so this
spec needs the ``subscription`` peer present (per-plugin CI isolation lesson).
"""
from uuid import uuid4

import pytest

# A ghrm package requires a subscription plan (FK). Skip cleanly when the peer
# package is absent (a bare per-plugin CI clone).
pytest.importorskip("plugins.subscription.subscription.models.tarif_plan")

from vbwd.models.enums import BillingPeriod  # noqa: E402
from plugins.subscription.subscription.models.tarif_plan import TarifPlan  # noqa: E402
from plugins.ghrm.src.models.ghrm_software_package import (  # noqa: E402
    GhrmSoftwarePackage,
)
from plugins.ghrm.src.search_provider import GhrmPackageSearchProvider  # noqa: E402


def _make_plan(db):
    plan = TarifPlan(
        id=uuid4(),
        name="GHRM Plan",
        slug=f"plan-{uuid4().hex[:8]}",
        price=10.0,
        billing_period=BillingPeriod.MONTHLY,
        is_active=True,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


def _make_package(db, *, name, slug, description="", is_active=True):
    plan = _make_plan(db)
    package = GhrmSoftwarePackage(
        id=uuid4(),
        tariff_plan_id=plan.id,
        name=name,
        slug=slug,
        description=description,
        github_owner="acme",
        github_repo=slug,
        is_active=is_active,
    )
    db.session.add(package)
    db.session.commit()
    return package


@pytest.fixture
def provider():
    return GhrmPackageSearchProvider()


def test_search_finds_active_package_by_name(db, provider):
    _make_package(
        db,
        name="Awesome Toolkit",
        slug="awesome-toolkit",
        description="A handy toolkit.",
    )

    hits = provider.search("awesome", limit=5)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.entity_type == "ghrm_package"
    assert hit.entity_label == "Software"
    assert hit.key == "awesome-toolkit"
    assert hit.title == "Awesome Toolkit"
    assert hit.url == "/category/search?q=awesome-toolkit"
    assert hit.price is None


def test_search_excludes_inactive_package(db, provider):
    _make_package(db, name="Old Plugin", slug="old-plugin", is_active=False)

    assert provider.search("old", limit=5) == []


def test_get_detail_resolves_by_slug(db, provider):
    _make_package(
        db,
        name="Cool Lib",
        slug="cool-lib",
        description="Does cool things.",
    )

    hit = provider.get_detail("cool-lib")

    assert hit is not None
    assert hit.title == "Cool Lib"
    assert hit.url == "/category/search?q=cool-lib"


def test_get_detail_unknown_slug_returns_none(db, provider):
    assert provider.get_detail("missing") is None
