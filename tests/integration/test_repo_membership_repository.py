"""Integration tests for GhrmRepoMembershipRepository (S49.1).

Exercises upsert (insert-then-update, no duplicates), the status filters
(find_grace_expired / find_invited), find_by_user across two packages,
find_by_user_and_package, delete_for_user, and the (user_id, package_id)
unique constraint against a real PostgreSQL database via the ``db``
fixture.
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from plugins.ghrm.src.models.ghrm_repo_membership import (
    GhrmRepoMembership,
    MembershipStatus,
)
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from plugins.ghrm.src.repositories.repo_membership_repository import (
    GhrmRepoMembershipRepository,
)
from plugins.subscription.subscription.models.tarif_plan import TarifPlan
from vbwd.models.enums import BillingPeriod
from vbwd.models.user import User


def _make_user(db) -> User:
    user = User(
        email=f"ghrm-member-{uuid4().hex[:10]}@example.com",
        password_hash="x",
    )
    db.session.add(user)
    db.session.flush()
    return user


def _make_plan(db, name: str) -> TarifPlan:
    plan = TarifPlan(
        name=name,
        slug=f"plan-{uuid4().hex[:8]}",
        price_float=10.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


def _make_package(db, slug: str) -> GhrmSoftwarePackage:
    plan = _make_plan(db, f"Plan {slug}")
    package = GhrmSoftwarePackage(
        tariff_plan_id=plan.id,
        name=f"Package {slug}",
        slug=slug,
        github_owner="acme",
        github_repo=f"repo-{slug}",
    )
    db.session.add(package)
    db.session.flush()
    return package


@pytest.fixture
def repository(db):
    return GhrmRepoMembershipRepository(db.session)


class TestUpsert:
    def test_inserts_then_updates_same_row_without_duplicate(self, db, repository):
        user_id = _make_user(db).id
        package = _make_package(db, f"upsert-{uuid4().hex[:6]}")

        first = repository.upsert(
            user_id=user_id,
            package_id=package.id,
            status=MembershipStatus.INVITED,
            invitation_id="inv-1",
        )
        db.session.flush()
        assert first.status == MembershipStatus.INVITED

        second = repository.upsert(
            user_id=user_id,
            package_id=package.id,
            status=MembershipStatus.ACTIVE,
            invitation_id=None,
            invited_at=datetime(2026, 5, 30, 12, 0, 0),
        )
        db.session.flush()

        assert second.id == first.id
        rows = (
            db.session.query(GhrmRepoMembership)
            .filter(
                GhrmRepoMembership.user_id == user_id,
                GhrmRepoMembership.package_id == package.id,
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == MembershipStatus.ACTIVE
        assert rows[0].invitation_id is None
        assert rows[0].invited_at == datetime(2026, 5, 30, 12, 0, 0)


class TestFinders:
    def test_find_by_user_returns_all_packages_for_user(self, db, repository):
        user_id = _make_user(db).id
        package_one = _make_package(db, f"a-{uuid4().hex[:6]}")
        package_two = _make_package(db, f"b-{uuid4().hex[:6]}")
        repository.upsert(
            user_id=user_id,
            package_id=package_one.id,
            status=MembershipStatus.ACTIVE,
        )
        repository.upsert(
            user_id=user_id,
            package_id=package_two.id,
            status=MembershipStatus.INVITED,
        )
        db.session.flush()

        found = repository.find_by_user(user_id)
        statuses = {membership.status for membership in found}
        assert len(found) == 2
        assert statuses == {MembershipStatus.ACTIVE, MembershipStatus.INVITED}

    def test_find_by_user_and_package(self, db, repository):
        user_id = _make_user(db).id
        package = _make_package(db, f"pair-{uuid4().hex[:6]}")
        repository.upsert(
            user_id=user_id,
            package_id=package.id,
            status=MembershipStatus.GRACE,
        )
        db.session.flush()

        found = repository.find_by_user_and_package(user_id, package.id)
        assert found is not None
        assert found.status == MembershipStatus.GRACE
        assert repository.find_by_user_and_package(uuid4(), package.id) is None

    def test_find_grace_expired_filters_by_status_and_deadline(self, db, repository):
        now = datetime(2026, 6, 1, 0, 0, 0)
        expired_package = _make_package(db, f"exp-{uuid4().hex[:6]}")
        future_package = _make_package(db, f"fut-{uuid4().hex[:6]}")
        active_package = _make_package(db, f"act-{uuid4().hex[:6]}")
        expired_user = _make_user(db).id
        repository.upsert(
            user_id=expired_user,
            package_id=expired_package.id,
            status=MembershipStatus.GRACE,
            grace_expires_at=now - timedelta(days=1),
        )
        repository.upsert(
            user_id=_make_user(db).id,
            package_id=future_package.id,
            status=MembershipStatus.GRACE,
            grace_expires_at=now + timedelta(days=1),
        )
        repository.upsert(
            user_id=_make_user(db).id,
            package_id=active_package.id,
            status=MembershipStatus.ACTIVE,
            grace_expires_at=now - timedelta(days=1),
        )
        db.session.flush()

        expired = repository.find_grace_expired(now)
        expired_users = {membership.user_id for membership in expired}
        assert expired_user in expired_users
        for membership in expired:
            assert membership.status == MembershipStatus.GRACE
            assert membership.grace_expires_at <= now

    def test_find_invited_returns_only_invited(self, db, repository):
        invited_package = _make_package(db, f"inv-{uuid4().hex[:6]}")
        active_package = _make_package(db, f"on-{uuid4().hex[:6]}")
        invited_user = _make_user(db).id
        repository.upsert(
            user_id=invited_user,
            package_id=invited_package.id,
            status=MembershipStatus.INVITED,
        )
        repository.upsert(
            user_id=_make_user(db).id,
            package_id=active_package.id,
            status=MembershipStatus.ACTIVE,
        )
        db.session.flush()

        invited = repository.find_invited()
        assert invited_user in {membership.user_id for membership in invited}
        for membership in invited:
            assert membership.status == MembershipStatus.INVITED


class TestDeleteForUser:
    def test_removes_all_memberships_for_user(self, db, repository):
        user_id = _make_user(db).id
        package_one = _make_package(db, f"d1-{uuid4().hex[:6]}")
        package_two = _make_package(db, f"d2-{uuid4().hex[:6]}")
        repository.upsert(
            user_id=user_id,
            package_id=package_one.id,
            status=MembershipStatus.ACTIVE,
        )
        repository.upsert(
            user_id=user_id,
            package_id=package_two.id,
            status=MembershipStatus.INVITED,
        )
        db.session.flush()

        repository.delete_for_user(user_id)
        db.session.flush()
        assert repository.find_by_user(user_id) == []


class TestUniqueConstraint:
    def test_rejects_duplicate_user_package_pair(self, db):
        user_id = _make_user(db).id
        package = _make_package(db, f"uniq-{uuid4().hex[:6]}")
        db.session.add(
            GhrmRepoMembership(
                user_id=user_id,
                package_id=package.id,
                status=MembershipStatus.ACTIVE,
            )
        )
        db.session.flush()
        db.session.add(
            GhrmRepoMembership(
                user_id=user_id,
                package_id=package.id,
                status=MembershipStatus.INVITED,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()
