"""GHRM marketplace vendor-listings provider (integration).

The GHRM vertical contributes a ``vendor_listings_provider`` the marketplace
registry calls to aggregate a user's software-package listings. This test
exercises the provider DIRECTLY against a real session — it never imports the
marketplace plugin, so it passes in the per-plugin isolated CI (which clones
GHRM alone, with its declared subscription dependency).

Ownership lives on the linked subscription plan's ``vendor_id`` (mirrors
``vendor_list_packages``). Seeds through the repositories (no raw SQL) and
asserts:
  - an empty list for a vendor who owns nothing (Liskov: safe empty result),
  - the vendor's own ``GhrmSoftwarePackage.to_dict()`` for a vendor who owns one,
  - another vendor's package is excluded (ownership scoping).
"""
from uuid import uuid4

from vbwd.models.user import User

from plugins.ghrm.src.marketplace_listings import vendor_listings_provider
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from plugins.ghrm.src.repositories.software_package_repository import (
    GhrmSoftwarePackageRepository,
)
from plugins.subscription.subscription.models import TarifPlan
from plugins.subscription.subscription.repositories.tarif_plan_repository import (
    TarifPlanRepository,
)


def _make_vendor(db):
    """Seed a real core user (plan.vendor_id has a FK to users), return its id."""
    user = User(email=f"ghrm-vendor-{uuid4().hex}@example.com", password_hash="x")
    db.session.add(user)
    db.session.commit()
    return user.id


def _make_vendor_package(db, vendor_id, name):
    """Seed a vendor-owned plan + a package linked to it, return the package."""
    slug = f"{name.lower().replace(' ', '-')}-{uuid4().hex[:8]}"
    plan = TarifPlan(
        name=name,
        slug=slug,
        description="Vendor software plan",
        price=29.0,
        billing_period="MONTHLY",
        is_active=True,
        vendor_id=vendor_id,
    )
    saved_plan = TarifPlanRepository(db.session).save(plan)

    package = GhrmSoftwarePackage(
        tariff_plan_id=saved_plan.id,
        name=name,
        slug=slug,
        description="A software package sold as a subscription",
        github_owner="acme",
        github_repo="widget",
        is_active=True,
    )
    return GhrmSoftwarePackageRepository(db.session).save(package)


def test_provider_returns_empty_for_vendor_without_packages(db):
    unknown_vendor_id = uuid4()

    assert vendor_listings_provider(unknown_vendor_id) == []


def test_provider_returns_only_the_vendors_own_package_dicts(db):
    vendor_id = _make_vendor(db)
    other_vendor_id = _make_vendor(db)

    owned = _make_vendor_package(db, vendor_id, "Owned Software")
    _make_vendor_package(db, other_vendor_id, "Other Software")

    listings = vendor_listings_provider(vendor_id)

    assert len(listings) == 1
    assert listings[0] == owned.to_dict()
    assert listings[0]["id"] == str(owned.id)
