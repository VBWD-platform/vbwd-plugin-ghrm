"""Unit tests for the rewritten GithubAccessService (S49.0 + S49.3).

Entitlement-scoped, per-(user, package) collaborator lifecycle with the
INVITED -> ACTIVE model and ERROR surfacing. Uses MagicMock repos, a
MockGithubAppClient, and a STUBBED ISubscriptionEntitlements — the
subscription plugin is never imported here (DIP via the ghrm-owned port).
"""
import logging

import pytest
from unittest.mock import MagicMock

from plugins.ghrm.src.services.github_access_service import (
    GithubAccessService,
    GhrmOAuthError,
)
from plugins.ghrm.src.services.github_app_client import MockGithubAppClient
from plugins.ghrm.src.services.github_app_client_real import GithubAppClientError
from plugins.ghrm.src.models.ghrm_repo_membership import MembershipStatus


class _StubEntitlements:
    """In-test ISubscriptionEntitlements — no subscription import."""

    def __init__(self, plan_ids=None):
        self._plan_ids = list(plan_ids or [])

    def active_plan_ids(self, user_id):
        return list(self._plan_ids)


def _make_service(
    access_repo=None,
    membership_repo=None,
    log_repo=None,
    package_repo=None,
    github=None,
    entitlements=None,
    grace_period_fallback_days=7,
    allow_extensive_permissions=True,
):
    return GithubAccessService(
        access_repo=access_repo or MagicMock(),
        membership_repo=membership_repo or MagicMock(),
        log_repo=log_repo or MagicMock(),
        package_repo=package_repo or MagicMock(),
        github=github or MockGithubAppClient(),
        entitlements=entitlements or _StubEntitlements(),
        oauth_client_id="test-client-id",
        oauth_client_secret="test-client-secret",
        oauth_redirect_uri="http://localhost/callback",
        grace_period_fallback_days=grace_period_fallback_days,
        allow_extensive_permissions=allow_extensive_permissions,
    )


def _make_access(user_id="user-1", username="octocat", github_user_id="99"):
    access = MagicMock()
    access.id = "access-id-1"
    access.user_id = user_id
    access.github_username = username
    access.github_user_id = github_user_id
    access.oauth_token = "existing-token"
    access.to_dict.return_value = {
        "id": "access-id-1",
        "user_id": user_id,
        "github_username": username,
        "github_user_id": github_user_id,
    }
    return access


def _make_package(pkg_id="pkg-1", slug="my-pkg", owner="acme", repo="my-repo"):
    pkg = MagicMock()
    pkg.id = pkg_id
    pkg.slug = slug
    pkg.github_owner = owner
    pkg.github_repo = repo
    pkg.collaborator_permission = "pull"
    pkg.package_kind = "single"
    pkg.bundle_repos = []
    # Honour the S59 repo_targets() seam so single packages resolve to their one
    # representative repo (the fake obeys the production contract — Liskov).
    pkg.repo_targets.return_value = [(owner, repo)]
    return pkg


def _configure_oauth(github, code, login="octocat", user_id="99"):
    github.oauth_token_map[code] = f"tok-{code}"
    github.oauth_user_map[f"tok-{code}"] = {"login": login, "id": user_id}


class TestConnectEntitlementResolution:
    def test_one_entitlement_invited_creates_invited_membership(self):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a")
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        access_repo.save.side_effect = lambda a: a

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg

        membership_repo = MagicMock()
        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
            entitlements=_StubEntitlements(plan_ids=["plan-A"]),
        )
        svc.handle_oauth_callback("user-1", "code-a")

        package_repo.find_by_tariff_plan_id.assert_called_once_with("plan-A")
        membership_repo.upsert.assert_called_once()
        call = membership_repo.upsert.call_args
        assert call.args[0] == "user-1"
        assert call.args[1] == pkg.id
        assert call.kwargs["status"] == MembershipStatus.INVITED.value
        assert call.kwargs["invitation_id"] is not None
        key = (pkg.github_owner, pkg.github_repo)
        assert "octocat" in github.collaborators.get(key, set())

    def test_one_entitlement_already_member_creates_active_membership(self):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a")
        pkg = _make_package()
        github.members_already.add((pkg.github_owner, pkg.github_repo, "octocat"))

        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        access_repo.save.side_effect = lambda a: a
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
            entitlements=_StubEntitlements(plan_ids=["plan-A"]),
        )
        svc.handle_oauth_callback("user-1", "code-a")

        assert (
            membership_repo.upsert.call_args.kwargs["status"]
            == MembershipStatus.ACTIVE.value
        )

    def test_no_entitlement_creates_no_membership_and_does_not_call_add(self):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a")
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        access_repo.save.side_effect = lambda a: a
        package_repo = MagicMock()
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
            entitlements=_StubEntitlements(plan_ids=[]),
        )
        svc.handle_oauth_callback("user-1", "code-a")

        membership_repo.upsert.assert_not_called()
        package_repo.find_by_tariff_plan_id.assert_not_called()
        assert github.collaborators == {}

    def test_two_entitlements_two_memberships_unrelated_package_not_added(self):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a")
        pkg_a = _make_package(pkg_id="pkg-a", owner="acme", repo="repo-a")
        pkg_b = _make_package(pkg_id="pkg-b", owner="acme", repo="repo-b")

        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.side_effect = lambda plan: {
            "plan-A": pkg_a,
            "plan-B": pkg_b,
        }.get(plan)

        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        access_repo.save.side_effect = lambda a: a
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
            entitlements=_StubEntitlements(plan_ids=["plan-A", "plan-B"]),
        )
        svc.handle_oauth_callback("user-1", "code-a")

        assert membership_repo.upsert.call_count == 2
        assert "octocat" in github.collaborators.get(("acme", "repo-a"), set())
        assert "octocat" in github.collaborators.get(("acme", "repo-b"), set())
        assert ("acme", "repo-unrelated") not in github.collaborators

    def test_skips_plan_with_no_package(self):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a")
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = None
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        access_repo.save.side_effect = lambda a: a
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
            entitlements=_StubEntitlements(plan_ids=["plan-no-pkg"]),
        )
        svc.handle_oauth_callback("user-1", "code-a")
        membership_repo.upsert.assert_not_called()

    def test_stores_verified_identity(self):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a", login="octocat", user_id="99")
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        saved = {}
        access_repo.save.side_effect = (
            lambda a: saved.update(
                {"username": a.github_username, "user_id": a.github_user_id}
            )
            or a
        )

        svc = _make_service(
            access_repo=access_repo,
            entitlements=_StubEntitlements(plan_ids=[]),
            github=github,
        )
        svc.handle_oauth_callback("user-1", "code-a")
        assert saved["username"] == "octocat"
        assert saved["user_id"] == "99"

    def test_raises_oauth_error_on_exchange_failure(self):
        github = MockGithubAppClient()
        github.raise_on_exchange = Exception("network timeout")
        svc = _make_service(github=github)
        with pytest.raises(GhrmOAuthError, match="OAuth exchange failed"):
            svc.handle_oauth_callback("user-1", "bad-code")


class TestEnsureCollaboratorErrorSurfacing:
    def test_add_collaborator_error_records_error_and_logs_warning(self, caplog):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a")
        github.raise_on_add_collaborator = GithubAppClientError("403 forbidden")

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        access_repo.save.side_effect = lambda a: a
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
            entitlements=_StubEntitlements(plan_ids=["plan-A"]),
        )

        with caplog.at_level(logging.WARNING):
            svc.handle_oauth_callback("user-1", "code-a")  # must NOT raise

        call = membership_repo.upsert.call_args
        assert call.kwargs["status"] == MembershipStatus.ERROR.value
        assert "403 forbidden" in call.kwargs["last_error"]
        assert any("add_collaborator failed" in rec.message for rec in caplog.records)

    def test_non_client_errors_propagate(self):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a")
        github.raise_on_add_collaborator = ValueError("unexpected boom")
        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        access_repo.save.side_effect = lambda a: a

        svc = _make_service(
            access_repo=access_repo,
            package_repo=package_repo,
            github=github,
            entitlements=_StubEntitlements(plan_ids=["plan-A"]),
        )
        with pytest.raises(ValueError, match="unexpected boom"):
            svc.handle_oauth_callback("user-1", "code-a")


class TestCollaboratorPermissionFromPackage:
    """The collaborator grant uses the *package's* configured level (S51).

    The permission is configurable per package (GHRM's per-plan entity), so a
    ``push`` package grants ``push`` while a package with no level falls back
    to the least-privilege default ``"pull"``. This supersedes the S49
    fixed-``pull`` guard.
    """

    def _run_grant_and_capture(self, package_permission, allow_extensive=True):
        github = MockGithubAppClient()
        _configure_oauth(github, "code-a")
        captured_permissions = []
        original_add_collaborator = github.add_collaborator

        def spy_add_collaborator(owner, repo, username, permission="pull"):
            captured_permissions.append(permission)
            return original_add_collaborator(owner, repo, username, permission)

        github.add_collaborator = spy_add_collaborator

        pkg = _make_package()
        pkg.collaborator_permission = package_permission
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        access_repo.save.side_effect = lambda a: a

        svc = _make_service(
            access_repo=access_repo,
            package_repo=package_repo,
            github=github,
            entitlements=_StubEntitlements(plan_ids=["plan-A"]),
            allow_extensive_permissions=allow_extensive,
        )
        svc.handle_oauth_callback("user-1", "code-a")
        return captured_permissions

    def test_push_package_grants_push(self):
        assert self._run_grant_and_capture("push") == ["push"]

    def test_package_without_level_falls_back_to_pull(self):
        assert self._run_grant_and_capture(None) == ["pull"]


class TestGrantClampWhenExtensiveDisabled:
    """D3 layer 2: the grant is clamped to ``pull`` whenever the flag is off.

    Defense in depth — even if a package carries a write+ value (stored while
    the flag was on, then turned off), the effective grant must be ``pull``.
    When the flag is on the package's configured level is honoured.
    """

    _capture = TestCollaboratorPermissionFromPackage._run_grant_and_capture

    def test_flag_off_clamps_push_package_to_pull(self):
        assert self._capture("push", allow_extensive=False) == ["pull"]

    def test_flag_off_clamps_admin_package_to_pull(self):
        assert self._capture("admin", allow_extensive=False) == ["pull"]

    def test_flag_off_pull_package_stays_pull(self):
        assert self._capture("pull", allow_extensive=False) == ["pull"]

    def test_flag_on_push_package_grants_push(self):
        assert self._capture("push", allow_extensive=True) == ["push"]


class TestOnSubscriptionActivated:
    def test_connected_ensures_that_one_package(self):
        github = MockGithubAppClient()
        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.on_subscription_activated("user-1", "plan-1")

        package_repo.find_by_tariff_plan_id.assert_called_once_with("plan-1")
        membership_repo.upsert.assert_called_once()
        assert access.github_username in github.collaborators.get(
            (pkg.github_owner, pkg.github_repo), set()
        )

    def test_disconnected_is_noop(self):
        github = MockGithubAppClient()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        package_repo = MagicMock()
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.on_subscription_activated("user-1", "plan-1")

        package_repo.find_by_tariff_plan_id.assert_not_called()
        membership_repo.upsert.assert_not_called()
        assert github.collaborators == {}


class TestOnSubscriptionCancelled:
    def test_sets_membership_grace_with_expiry(self):
        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        membership = MagicMock()
        membership_repo = MagicMock()
        membership_repo.find_by_user_and_package.return_value = membership

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
        )
        svc.on_subscription_cancelled("user-1", "plan-1", trailing_days=14)

        call = membership_repo.upsert.call_args
        assert call.args[0] == "user-1"
        assert call.args[1] == pkg.id
        assert call.kwargs["status"] == MembershipStatus.GRACE.value
        assert call.kwargs["grace_expires_at"] is not None

    def test_noop_when_no_package(self):
        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = None
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
        )
        svc.on_subscription_cancelled("user-1", "plan-1", trailing_days=14)
        membership_repo.upsert.assert_not_called()


class TestOnSubscriptionPaymentFailed:
    def test_delegates_to_grace(self):
        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
        )
        svc.on_subscription_payment_failed("user-1", "plan-1", trailing_days=3)
        assert (
            membership_repo.upsert.call_args.kwargs["status"]
            == MembershipStatus.GRACE.value
        )


class TestOnSubscriptionRenewed:
    def test_reensures_collaborator_no_token_rotation(self):
        github = MockGithubAppClient()
        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access
        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg
        membership_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.on_subscription_renewed("user-1", "plan-1")

        membership_repo.upsert.assert_called_once()
        assert access.github_username in github.collaborators.get(
            (pkg.github_owner, pkg.github_repo), set()
        )
        # No deploy-token rotation occurs (deploy tokens removed in S49.2/S49.3).
        assert github.revoked_tokens == []


class TestRevokeExpiredGraceAccess:
    def test_active_membership_removes_collaborator_and_revokes(self):
        github = MockGithubAppClient()
        pkg = _make_package()
        github.collaborators[(pkg.github_owner, pkg.github_repo)] = {"octocat"}

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        membership = MagicMock()
        membership.user_id = "user-1"
        membership.package_id = pkg.id
        membership.status = MembershipStatus.ACTIVE.value
        membership.invitation_id = None
        membership.repo_grants = []  # legacy row -> representative-repo fallback
        membership.package = pkg
        membership_repo = MagicMock()
        membership_repo.find_grace_expired.return_value = [membership]
        membership_repo.find_by_user.return_value = [membership]

        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        count = svc.revoke_expired_grace_access()

        assert count == 1
        assert "octocat" not in github.collaborators.get(
            (pkg.github_owner, pkg.github_repo), set()
        )
        assert (
            membership_repo.upsert.call_args.kwargs["status"]
            == MembershipStatus.REVOKED.value
        )

    def test_invited_membership_cancels_invitation(self):
        github = MockGithubAppClient()
        pkg = _make_package()

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        membership = MagicMock()
        membership.user_id = "user-1"
        membership.package_id = pkg.id
        membership.status = MembershipStatus.INVITED.value
        membership.invitation_id = "inv-9"
        membership.repo_grants = []  # legacy row -> representative-repo fallback
        membership.package = pkg
        membership_repo = MagicMock()
        membership_repo.find_grace_expired.return_value = [membership]
        membership_repo.find_by_user.return_value = [membership]

        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        cancelled = {}
        original_cancel = github.cancel_invitation

        def spy_cancel(owner, repo, invitation_id):
            cancelled["args"] = (owner, repo, invitation_id)
            return original_cancel(owner, repo, invitation_id)

        github.cancel_invitation = spy_cancel

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        count = svc.revoke_expired_grace_access()

        assert count == 1
        assert cancelled["args"] == (pkg.github_owner, pkg.github_repo, "inv-9")
        assert (
            membership_repo.upsert.call_args.kwargs["status"]
            == MembershipStatus.REVOKED.value
        )


class TestVerifyAcceptance:
    def test_invited_and_accepted_becomes_active(self):
        github = MockGithubAppClient()
        pkg = _make_package()
        github.accepted.add((pkg.github_owner, pkg.github_repo, "octocat"))

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        membership = MagicMock()
        membership.user_id = "user-1"
        membership.package_id = pkg.id
        membership.status = MembershipStatus.INVITED.value
        membership.invitation_id = "inv-1"
        membership.package = pkg
        membership_repo = MagicMock()
        membership_repo.find_by_user.return_value = [membership]

        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.verify_acceptance("user-1")

        call = membership_repo.upsert.call_args
        assert call.kwargs["status"] == MembershipStatus.ACTIVE.value
        assert call.kwargs["invitation_id"] is None

    def test_invited_not_yet_accepted_stays_invited(self):
        github = MockGithubAppClient()
        pkg = _make_package()

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        membership = MagicMock()
        membership.user_id = "user-1"
        membership.package_id = pkg.id
        membership.status = MembershipStatus.INVITED.value
        membership.invitation_id = "inv-1"
        membership.package = pkg
        membership_repo = MagicMock()
        membership_repo.find_by_user.return_value = [membership]

        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.verify_acceptance("user-1")
        membership_repo.upsert.assert_not_called()


class TestDisconnectGithub:
    def test_removes_all_memberships_and_deletes_identity(self):
        github = MockGithubAppClient()
        pkg = _make_package()
        github.collaborators[(pkg.github_owner, pkg.github_repo)] = {"octocat"}

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        membership = MagicMock()
        membership.user_id = "user-1"
        membership.package_id = pkg.id
        membership.status = MembershipStatus.ACTIVE.value
        membership.invitation_id = None
        membership.repo_grants = []  # legacy row -> representative-repo fallback
        membership.package = pkg
        membership_repo = MagicMock()
        membership_repo.find_by_user.return_value = [membership]

        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg
        log_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            log_repo=log_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.disconnect_github("user-1")

        assert "octocat" not in github.collaborators.get(
            (pkg.github_owner, pkg.github_repo), set()
        )
        membership_repo.delete_for_user.assert_called_once_with("user-1")
        access_repo.delete.assert_called_once_with("access-id-1")

    def test_github_teardown_failure_still_completes_local_disconnect(self):
        """A GitHub 403 on remove_collaborator must NOT 500 the disconnect.

        The App can lack permission to remove a collaborator ("Resource not
        accessible by integration"); the user's local identity + memberships
        must still be deleted (best-effort teardown).
        """
        github = MockGithubAppClient()
        pkg = _make_package()
        github.collaborators[(pkg.github_owner, pkg.github_repo)] = {"octocat"}
        github.raise_on_remove_collaborator = GithubAppClientError(
            'remove_collaborator failed: 403 {"message":"Resource not '
            'accessible by integration"}'
        )

        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        membership = MagicMock()
        membership.user_id = "user-1"
        membership.package_id = pkg.id
        membership.status = MembershipStatus.ACTIVE.value
        membership.invitation_id = None
        membership.repo_grants = []  # legacy row -> representative-repo fallback
        membership.package = pkg
        membership_repo = MagicMock()
        membership_repo.find_by_user.return_value = [membership]

        package_repo = MagicMock()
        package_repo.find_by_id.return_value = pkg

        svc = _make_service(
            access_repo=access_repo,
            membership_repo=membership_repo,
            package_repo=package_repo,
            github=github,
        )

        # Must NOT raise despite the GitHub-side 403.
        svc.disconnect_github("user-1")

        membership_repo.delete_for_user.assert_called_once_with("user-1")
        access_repo.delete.assert_called_once_with("access-id-1")

    def test_noop_when_no_access(self):
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        membership_repo = MagicMock()

        svc = _make_service(access_repo=access_repo, membership_repo=membership_repo)
        svc.disconnect_github("user-1")

        access_repo.delete.assert_not_called()
        membership_repo.delete_for_user.assert_not_called()


class TestGetAccessStatus:
    def test_connected_returns_memberships(self):
        access = _make_access()
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        membership = MagicMock()
        membership.to_dict.return_value = {"package_slug": "my-pkg", "status": "active"}
        membership_repo = MagicMock()
        membership_repo.find_by_user.return_value = [membership]

        svc = _make_service(access_repo=access_repo, membership_repo=membership_repo)
        result = svc.get_access_status("user-1")

        assert result["connected"] is True
        assert result["memberships"] == [{"package_slug": "my-pkg", "status": "active"}]

    def test_not_connected_returns_none(self):
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None
        svc = _make_service(access_repo=access_repo)
        assert svc.get_access_status("user-1") is None
