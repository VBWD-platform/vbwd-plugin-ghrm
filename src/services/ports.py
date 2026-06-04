"""GHRM-owned ports (DIP).

GHRM depends on the *narrow abstractions it needs*, never on another plugin's
concrete classes. The subscription concrete is wired only at the composition
root (``plugins/ghrm/__init__.py``) via an adapter that satisfies this port —
the single, declared GHRM->subscription seam (S49.0).
"""
from typing import List, Protocol
from uuid import UUID


class ISubscriptionEntitlements(Protocol):
    """Which tariff plans is this user actively entitled to right now?"""

    def active_plan_ids(self, user_id: UUID) -> List[UUID]:
        ...
