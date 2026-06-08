"""Unit tests for GithubAccessService bundle grant/revoke (S59).

A bundle package resolves to many repos via ``repo_targets()``; grant loops it
(add_collaborator once per repo) and records a per-repo ``repo_grants`` entry
plus a rolled-up membership status. Revoke (tear-down) loops the recorded
``repo_grants`` and is repo-aware (D6): a repo still covered by another live
membership is left intact; an INVITED repo is cancel_invitation-d, an ACTIVE
repo is remove_collaborator-d. Uses MagicMock repos + the MockGithubAppClient.
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


class _StubPackage:
    """Real package stub honouring the ``repo_targets()`` seam (Liskov)."""

    def __init__(self, pkg_id, owner, repo, kind="single", bundle_repos=None):
        self.id = pkg_id
        self.slug = f"slug-{pkg_id}"
        self.github_owner = owner
        self.github_repo = repo
        self.package_kind = kind
        self.bundle_repos = bundle_repos or []
        self.collaborator_permission = "pull"

    def repo_targets(self):
        if self.package_kind == "bundle":
            seen = []
            for entry in self.bundle_repos:
                pair = (entry["owner"], entry["repo"])
                if pair not in seen:
                    seen.append(pair)
            return seen
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


class TestBundleGrant:
    def test_bundle_grants_once_per_repo_at_package_permission(self):
        github = MockGithubAppClient()
        captured = []
        original = github.add_collaborator

        def spy(owner, repo, username, permission="pull"):
            captured.append((owner, repo, permission))
            return original(owner, repo, username, permission)

        github.add_collaborator = spy

        pkg = _StubPackage(
            "pkg-bundle",
            owner="acme",
            repo="showcase",
            kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
            ],
        )
        pkg.collaborator_permission = "push"
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

        assert captured == [
            ("acme", "alpha", "push"),
            ("acme", "beta", "push"),
        ]

    def test_bundle_records_repo_grants_and_rolls_up_status(self):
        github = MockGithubAppClient()
        pkg = _StubPackage(
            "pkg-bundle",
            owner="acme",
            repo="showcase",
            kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
            ],
        )
        # alpha already a member (active), beta pending (invited) -> rollup INVITED.
        github.members_already.add(("acme", "alpha", "octocat"))

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

        call = membership_repo.upsert.call_args
        repo_grants = call.kwargs["repo_grants"]
        assert {(g["owner"], g["repo"]) for g in repo_grants} == {
            ("acme", "alpha"),
            ("acme", "beta"),
        }
        statuses = {(g["owner"], g["repo"]): g["status"] for g in repo_grants}
        assert statuses[("acme", "alpha")] == "active"
        assert statuses[("acme", "beta")] == "invited"
        # Mixed active/pending -> rollup INVITED.
        assert call.kwargs["status"] == MembershipStatus.INVITED.value

    def test_bundle_all_active_rolls_up_active(self):
        github = MockGithubAppClient()
        pkg = _StubPackage(
            "pkg-bundle",
            owner="acme",
            repo="showcase",
            kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
            ],
        )
        github.members_already.add(("acme", "alpha", "octocat"))
        github.members_already.add(("acme", "beta", "octocat"))

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
        call = membership_repo.upsert.call_args
        assert call.kwargs["status"] == MembershipStatus.ACTIVE.value

    def test_single_package_still_grants_its_one_repo(self):
        github = MockGithubAppClient()
        pkg = _StubPackage("pkg-single", owner="acme", repo="solo", kind="single")
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
        assert "octocat" in github.collaborators.get(("acme", "solo"), set())
        repo_grants = membership_repo.upsert.call_args.kwargs["repo_grants"]
        assert [(g["owner"], g["repo"]) for g in repo_grants] == [("acme", "solo")]


class TestBundleRevoke:
    def _bundle_membership(self, pkg, repo_grants, status):
        membership = MagicMock()
        membership.user_id = "user-1"
        membership.package_id = pkg.id
        membership.status = status
        membership.invitation_id = None
        membership.repo_grants = repo_grants
        membership.package = pkg
        return membership

    def test_grace_expiry_removes_each_bundle_repo(self):
        github = MockGithubAppClient()
        pkg = _StubPackage(
            "pkg-bundle",
            owner="acme",
            repo="showcase",
            kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
            ],
        )
        github.collaborators[("acme", "alpha")] = {"octocat"}
        github.collaborators[("acme", "beta")] = {"octocat"}

        membership = self._bundle_membership(
            pkg,
            repo_grants=[
                {
                    "owner": "acme",
                    "repo": "alpha",
                    "status": "active",
                    "invitation_id": None,
                },
                {
                    "owner": "acme",
                    "repo": "beta",
                    "status": "active",
                    "invitation_id": None,
                },
            ],
            status=MembershipStatus.ACTIVE.value,
        )
        membership_repo = MagicMock()
        membership_repo.find_grace_expired.return_value = [membership]
        # No other memberships -> nothing still entitled.
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

        assert "octocat" not in github.collaborators.get(("acme", "alpha"), set())
        assert "octocat" not in github.collaborators.get(("acme", "beta"), set())

    def test_repo_covered_by_another_live_membership_is_not_removed(self):
        github = MockGithubAppClient()
        bundle_pkg = _StubPackage(
            "pkg-bundle",
            owner="acme",
            repo="showcase",
            kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "shared"},
                {"owner": "acme", "repo": "alpha"},
            ],
        )
        other_pkg = _StubPackage(
            "pkg-other", owner="acme", repo="shared", kind="single"
        )
        github.collaborators[("acme", "shared")] = {"octocat"}
        github.collaborators[("acme", "alpha")] = {"octocat"}

        expiring = self._bundle_membership(
            bundle_pkg,
            repo_grants=[
                {
                    "owner": "acme",
                    "repo": "shared",
                    "status": "active",
                    "invitation_id": None,
                },
                {
                    "owner": "acme",
                    "repo": "alpha",
                    "status": "active",
                    "invitation_id": None,
                },
            ],
            status=MembershipStatus.ACTIVE.value,
        )
        # A live ACTIVE membership on the single 'shared' package.
        live_other = MagicMock()
        live_other.user_id = "user-1"
        live_other.package_id = other_pkg.id
        live_other.status = MembershipStatus.ACTIVE.value
        live_other.package = other_pkg

        membership_repo = MagicMock()
        membership_repo.find_grace_expired.return_value = [expiring]
        membership_repo.find_by_user.return_value = [expiring, live_other]

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        package_repo = MagicMock()
        package_repo.find_by_id.return_value = bundle_pkg

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.revoke_expired_grace_access()

        # 'shared' is still entitled via the other live membership -> kept.
        assert "octocat" in github.collaborators.get(("acme", "shared"), set())
        # 'alpha' is unique to the bundle -> removed.
        assert "octocat" not in github.collaborators.get(("acme", "alpha"), set())

    def test_invited_repo_is_cancelled_active_repo_is_removed(self):
        github = MockGithubAppClient()
        pkg = _StubPackage(
            "pkg-bundle",
            owner="acme",
            repo="showcase",
            kind="bundle",
            bundle_repos=[
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
            ],
        )
        github.collaborators[("acme", "beta")] = {"octocat"}
        github.invitations[("acme", "alpha")] = [{"id": 555}]

        cancelled = []
        removed = []
        original_cancel = github.cancel_invitation
        original_remove = github.remove_collaborator

        def spy_cancel(owner, repo, invitation_id):
            cancelled.append((owner, repo, invitation_id))
            return original_cancel(owner, repo, invitation_id)

        def spy_remove(owner, repo, username):
            removed.append((owner, repo, username))
            return original_remove(owner, repo, username)

        github.cancel_invitation = spy_cancel
        github.remove_collaborator = spy_remove

        membership = self._bundle_membership(
            pkg,
            repo_grants=[
                {
                    "owner": "acme",
                    "repo": "alpha",
                    "status": "invited",
                    "invitation_id": "555",
                },
                {
                    "owner": "acme",
                    "repo": "beta",
                    "status": "active",
                    "invitation_id": None,
                },
            ],
            status=MembershipStatus.GRACE.value,
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

        assert ("acme", "alpha", "555") in cancelled
        assert ("acme", "beta", "octocat") in removed

    def test_legacy_membership_without_repo_grants_falls_back_to_representative(self):
        github = MockGithubAppClient()
        pkg = _StubPackage("pkg-single", owner="acme", repo="solo", kind="single")
        github.collaborators[("acme", "solo")] = {"octocat"}

        membership = self._bundle_membership(
            pkg, repo_grants=[], status=MembershipStatus.ACTIVE.value
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

        assert "octocat" not in github.collaborators.get(("acme", "solo"), set())
