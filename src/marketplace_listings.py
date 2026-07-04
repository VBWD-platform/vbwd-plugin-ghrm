"""Marketplace vendor-listings provider for the GHRM vertical.

The central ``marketplace`` plugin owns a registry that aggregates a user's
listings across every enabled vertical (its admin "what does this user sell?"
view). GHRM contributes a provider that returns the raw ``GhrmSoftwarePackage``
dicts a given vendor owns — reusing the same lookup ``vendor_list_packages``
performs (ownership lives on the linked subscription plan's ``vendor_id``).

GHRM declares ``dependencies=["subscription"]`` so reading ``TarifPlan`` here is
a declared, legitimate plugin->plugin dependency. This module never imports the
marketplace plugin (the money path stays decoupled — see
``test_vendor_mode_contract``); the actual registration onto the marketplace
registry is a guarded, soft import done in the plugin's ``on_enable``
(``plugins/ghrm/__init__.py``), so the per-plugin isolated CI (GHRM without
marketplace) still enables cleanly. Core names nothing here.
"""
from typing import List
from uuid import UUID

# The listing ``type`` id GHRM contributes — mirrors the marketplace
# ``LISTING_TYPE_CATALOG`` and the fe-user ``ListingType`` for software.
GHRM_LISTING_TYPE_ID = "software"


def vendor_listings_provider(user_id: UUID) -> List[dict]:
    """Return the raw ``GhrmSoftwarePackage`` dicts owned by ``user_id``.

    Ownership is the linked subscription plan's ``vendor_id``: resolve the
    vendor's plan ids, then fetch the packages linked to them — exactly as
    ``vendor_list_packages`` does. Resolves ``db.session`` and constructs the
    repository lazily at call time, so there is no app-context work at import.
    """
    from vbwd.extensions import db
    from plugins.subscription.subscription.models import TarifPlan
    from plugins.ghrm.src.repositories.software_package_repository import (
        GhrmSoftwarePackageRepository,
    )

    vendor_plans = (
        db.session.query(TarifPlan).filter(TarifPlan.vendor_id == user_id).all()
    )
    package_repo = GhrmSoftwarePackageRepository(db.session)
    packages = package_repo.find_by_tariff_plan_ids([plan.id for plan in vendor_plans])
    return [package.to_dict() for package in packages]
