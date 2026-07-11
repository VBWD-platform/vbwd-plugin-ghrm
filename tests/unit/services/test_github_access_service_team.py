"""Service dispatch over team access targets (S132) — the heart of the sprint.

A ``team`` package maps to ONE GitHub team. The proof test encodes the whole
point: the first team grant leaves the membership INVITED (one org invitation
pending); once the developer accepts the org invite, ``verify_acceptance``
promotes it to ACTIVE; a SECOND team package then grants INSTANTLY (active, no
new pending invite). ``add_team_membership`` is called ONCE per package (not
once per repo) — that is the "99 invitations" fix.
"""
from unittest.mock import MagicMock

from plugins.ghrm.src.services.github_access_service import GithubAccessService
from plugins.ghrm.src.services.github_app_client import MockGithubAppClient
from plugins.ghrm.src.models.ghrm_repo_membership import MembershipStatus


class _StubEntitlements:
    def __init__(self, plan_ids=None):
        self._plan_ids = list(plan_ids or [])

    def active_plan_ids(self, user_id):
        return list(self._plan_ids)


class _TeamPackage:
    """A ``team``-kind package honouring the access-target seam (Liskov)."""

    def __init__(self, pkg_id, org, team_slug):
        self.id = pkg_id
        self.slug = f"slug-{pkg_id}"
        self.access_kind = "team"
        self.github_org = org
        self.github_team_slug = team_slug
        # Representative repo columns stay populated (NOT NULL in prod).
        self.github_owner = org
        self.github_repo = "representative"
        self.package_kind = "single"
        self.bundle_repos = []
        self.collaborator_permission = "pull"

    def repo_targets(self):
        return [(self.github_owner, self.github_repo)]


def _make_service(
    access_repo=None,
    membership_repo=None,
    log_repo=None,
    package_repo=None,
    github=None,
    entitlements=None,
):
    return GithubAccessService(
        access_repo=access_repo or MagicMock(),
        membership_repo=membership_repo or MagicMock(),
        log_repo=log_repo or MagicMock(),
        package_repo=package_repo or MagicMock(),
        github=github or MockGithubAppClient(),
        entitlements=entitlements or _StubEntitlements(),
        allow_extensive_permissions=True,
    )


def _make_access(username="octocat"):
    access = MagicMock()
    access.id = "access-id-1"
    access.user_id = "user-1"
    access.github_username = username
    return access


class TestTeamGrant:
    def test_team_grant_calls_add_team_membership_once_and_records_team_entry(self):
        github = MockGithubAppClient()
        captured = []
        original = github.add_team_membership

        def spy(org, team_slug, username):
            captured.append((org, team_slug, username))
            return original(org, team_slug, username)

        github.add_team_membership = spy

        pkg = _TeamPackage("pkg-a", org="acme-inc", team_slug="developers")
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.on_subscription_activated("user-1", "plan-A")

        # ONE org-level call, not one per repo.
        assert captured == [("acme-inc", "developers", "octocat")]
        call = membership_repo.upsert.call_args
        grants = call.kwargs["repo_grants"]
        assert len(grants) == 1
        assert grants[0]["kind"] == "team"
        assert grants[0]["org"] == "acme-inc"
        assert grants[0]["team_slug"] == "developers"
        assert grants[0]["status"] == MembershipStatus.INVITED.value
        # Not yet an org member -> pending -> rolled-up INVITED.
        assert call.kwargs["status"] == MembershipStatus.INVITED.value


class TestOneInviteThenInstant:
    def test_first_team_pending_then_active_and_second_team_instant(self):
        """≤1 org invitation total: first grant pending, accept promotes to
        ACTIVE, a second team package grants instantly (active, no new pending).
        """
        github = MockGithubAppClient()
        pending_calls = []
        original = github.add_team_membership

        def spy(org, team_slug, username):
            result = original(org, team_slug, username)
            pending_calls.append((org, team_slug, result.state))
            return result

        github.add_team_membership = spy

        pkg_a = _TeamPackage("pkg-a", org="acme-inc", team_slug="developers")
        pkg_b = _TeamPackage("pkg-b", org="acme-inc", team_slug="qa")

        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.side_effect = lambda plan: {
            "plan-A": pkg_a,
            "plan-B": pkg_b,
        }.get(plan)
        package_repo.find_by_id.side_effect = lambda pid: {
            "pkg-a": pkg_a,
            "pkg-b": pkg_b,
        }.get(pid)

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        # A tiny in-memory membership store so verify_acceptance reads back the
        # INVITED row the first grant wrote.
        stored = {}

        def upsert(user_id, package_id, **kwargs):
            membership = stored.get(package_id) or MagicMock()
            membership.user_id = user_id
            membership.package_id = package_id
            membership.status = kwargs.get(
                "status", getattr(membership, "status", None)
            )
            membership.invitation_id = kwargs.get("invitation_id")
            membership.package = {"pkg-a": pkg_a, "pkg-b": pkg_b}[package_id]
            stored[package_id] = membership
            return membership

        membership_repo = MagicMock()
        membership_repo.upsert.side_effect = upsert
        membership_repo.find_by_user.side_effect = lambda uid: list(stored.values())

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )

        # 1) First team package -> pending -> membership INVITED (one org invite).
        svc.on_subscription_activated("user-1", "plan-A")
        assert stored["pkg-a"].status == MembershipStatus.INVITED.value
        assert pending_calls == [("acme-inc", "developers", "pending")]

        # 2) Developer accepts the single org invitation.
        github.org_members.setdefault("acme-inc", set()).add("octocat")
        svc.verify_acceptance("user-1")
        assert stored["pkg-a"].status == MembershipStatus.ACTIVE.value

        # 3) Second team package grants INSTANTLY (active, no new pending invite).
        svc.on_subscription_activated("user-1", "plan-B")
        assert stored["pkg-b"].status == MembershipStatus.ACTIVE.value
        assert pending_calls[-1] == ("acme-inc", "qa", "active")
        # Exactly one pending state across both grants -> ≤1 org invitation.
        assert [state for *_, state in pending_calls].count("pending") == 1


class TestTeamTearDown:
    def _membership(self, pkg, grants, status):
        membership = MagicMock()
        membership.user_id = "user-1"
        membership.package_id = pkg.id
        membership.status = status
        membership.invitation_id = None
        membership.repo_grants = grants
        membership.package = pkg
        return membership

    def test_grace_expiry_removes_team_membership(self):
        github = MockGithubAppClient()
        pkg = _TeamPackage("pkg-a", org="acme-inc", team_slug="developers")
        github.add_team_membership("acme-inc", "developers", "octocat")

        removed = []
        original = github.remove_team_membership

        def spy(org, team_slug, username):
            removed.append((org, team_slug, username))
            return original(org, team_slug, username)

        github.remove_team_membership = spy

        membership = self._membership(
            pkg,
            grants=[
                {
                    "kind": "team",
                    "org": "acme-inc",
                    "team_slug": "developers",
                    "status": "active",
                    "invitation_id": None,
                }
            ],
            status=MembershipStatus.ACTIVE.value,
        )
        membership_repo = MagicMock()
        membership_repo.find_grace_expired.return_value = [membership]
        membership_repo.find_by_user.return_value = [membership]

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.revoke_expired_grace_access()

        assert removed == [("acme-inc", "developers", "octocat")]

    def test_team_still_covered_by_another_live_membership_is_kept(self):
        github = MockGithubAppClient()
        # Two packages mapping to the SAME team; one expires, the other stays.
        expiring_pkg = _TeamPackage("pkg-a", org="acme-inc", team_slug="developers")
        live_pkg = _TeamPackage("pkg-b", org="acme-inc", team_slug="developers")
        github.add_team_membership("acme-inc", "developers", "octocat")

        removed = []
        original = github.remove_team_membership

        def spy(org, team_slug, username):
            removed.append((org, team_slug, username))
            return original(org, team_slug, username)

        github.remove_team_membership = spy

        expiring = self._membership(
            expiring_pkg,
            grants=[
                {
                    "kind": "team",
                    "org": "acme-inc",
                    "team_slug": "developers",
                    "status": "active",
                    "invitation_id": None,
                }
            ],
            status=MembershipStatus.ACTIVE.value,
        )
        live_other = MagicMock()
        live_other.user_id = "user-1"
        live_other.package_id = live_pkg.id
        live_other.status = MembershipStatus.ACTIVE.value
        live_other.package = live_pkg

        membership_repo = MagicMock()
        membership_repo.find_grace_expired.return_value = [expiring]
        membership_repo.find_by_user.return_value = [expiring, live_other]

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = expiring_pkg

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.revoke_expired_grace_access()

        # The team is still entitled via the other live membership -> not removed.
        assert removed == []
        assert (
            github.get_team_membership("acme-inc", "developers", "octocat") is not None
        )
