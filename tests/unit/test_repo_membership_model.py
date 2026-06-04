"""Unit tests for GhrmRepoMembership model and MembershipStatus enum (S49.1).

These exercise the per-(user, package) membership model in isolation: the
five statuses and the ``to_dict()`` serialization shape (explicit fields,
ISO-formatted timestamps, and the carried package slug/name for the
fe-user tab). No database is touched.
"""
from datetime import datetime
from uuid import uuid4

from plugins.ghrm.src.models.ghrm_repo_membership import (
    GhrmRepoMembership,
    MembershipStatus,
)


class TestMembershipStatus:
    def test_has_exactly_the_five_lifecycle_values(self):
        assert MembershipStatus.INVITED == "invited"
        assert MembershipStatus.ACTIVE == "active"
        assert MembershipStatus.GRACE == "grace"
        assert MembershipStatus.REVOKED == "revoked"
        assert MembershipStatus.ERROR == "error"

    def test_no_unexpected_values(self):
        values = {member.value for member in MembershipStatus}
        assert values == {"invited", "active", "grace", "revoked", "error"}


class _StubPackage:
    """Stand-in for the related GhrmSoftwarePackage carried in to_dict()."""

    def __init__(self, slug: str, name: str) -> None:
        self.slug = slug
        self.name = name


class TestToDict:
    def _build_membership(self) -> GhrmRepoMembership:
        membership = GhrmRepoMembership()
        membership.id = uuid4()
        membership.user_id = uuid4()
        membership.package_id = uuid4()
        membership.status = MembershipStatus.ACTIVE
        membership.invitation_id = "inv-123"
        membership.invited_at = datetime(2026, 5, 30, 12, 0, 0)
        membership.grace_expires_at = datetime(2026, 6, 30, 12, 0, 0)
        membership.last_error = None
        membership.created_at = datetime(2026, 5, 1, 9, 0, 0)
        membership.updated_at = datetime(2026, 5, 2, 9, 0, 0)
        membership.package = _StubPackage(slug="my-pkg", name="My Package")
        return membership

    def test_includes_explicit_identity_fields(self):
        membership = self._build_membership()
        data = membership.to_dict()
        assert data["id"] == str(membership.id)
        assert data["user_id"] == str(membership.user_id)
        assert data["package_id"] == str(membership.package_id)
        assert data["status"] == "active"
        assert data["invitation_id"] == "inv-123"
        assert data["last_error"] is None

    def test_timestamps_are_isoformatted(self):
        membership = self._build_membership()
        data = membership.to_dict()
        assert data["invited_at"] == "2026-05-30T12:00:00"
        assert data["grace_expires_at"] == "2026-06-30T12:00:00"
        assert data["created_at"] == "2026-05-01T09:00:00"
        assert data["updated_at"] == "2026-05-02T09:00:00"

    def test_carries_package_slug_and_name(self):
        membership = self._build_membership()
        data = membership.to_dict()
        assert data["package_slug"] == "my-pkg"
        assert data["package_name"] == "My Package"

    def test_handles_missing_package_and_null_timestamps(self):
        membership = GhrmRepoMembership()
        membership.id = uuid4()
        membership.user_id = uuid4()
        membership.package_id = uuid4()
        membership.status = MembershipStatus.INVITED
        membership.invitation_id = None
        membership.invited_at = None
        membership.grace_expires_at = None
        membership.last_error = None
        membership.created_at = None
        membership.updated_at = None
        membership.package = None
        data = membership.to_dict()
        assert data["invited_at"] is None
        assert data["grace_expires_at"] is None
        assert data["created_at"] is None
        assert data["updated_at"] is None
        assert data["package_slug"] is None
        assert data["package_name"] is None
