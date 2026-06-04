"""GhrmRepoMembershipRepository — data access for per-(user, package) memberships."""
from datetime import datetime
from typing import List, Optional

from plugins.ghrm.src.models.ghrm_repo_membership import (
    GhrmRepoMembership,
    MembershipStatus,
)


class GhrmRepoMembershipRepository:
    def __init__(self, session) -> None:
        self.session = session

    def upsert(self, user_id, package_id, **fields) -> GhrmRepoMembership:
        """Insert a membership for ``(user_id, package_id)``, or update it in
        place if one already exists. The pair is unique, so there is never
        more than one row per ``(user, package)``.
        """
        membership = self.find_by_user_and_package(user_id, package_id)
        if membership is None:
            membership = GhrmRepoMembership(user_id=user_id, package_id=package_id)
            self.session.add(membership)
        for field_name, field_value in fields.items():
            setattr(membership, field_name, field_value)
        self.session.flush()
        return membership

    def find_by_user(self, user_id) -> List[GhrmRepoMembership]:
        return (
            self.session.query(GhrmRepoMembership)
            .filter(GhrmRepoMembership.user_id == user_id)
            .all()
        )

    def find_by_user_and_package(
        self, user_id, package_id
    ) -> Optional[GhrmRepoMembership]:
        return (
            self.session.query(GhrmRepoMembership)
            .filter(
                GhrmRepoMembership.user_id == user_id,
                GhrmRepoMembership.package_id == package_id,
            )
            .first()
        )

    def find_grace_expired(self, now: datetime) -> List[GhrmRepoMembership]:
        return (
            self.session.query(GhrmRepoMembership)
            .filter(
                GhrmRepoMembership.status == MembershipStatus.GRACE.value,
                GhrmRepoMembership.grace_expires_at <= now,
            )
            .all()
        )

    def find_invited(self) -> List[GhrmRepoMembership]:
        return (
            self.session.query(GhrmRepoMembership)
            .filter(GhrmRepoMembership.status == MembershipStatus.INVITED.value)
            .all()
        )

    def delete_for_user(self, user_id) -> None:
        self.session.query(GhrmRepoMembership).filter(
            GhrmRepoMembership.user_id == user_id
        ).delete(synchronize_session=False)
        self.session.flush()
