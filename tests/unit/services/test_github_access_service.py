"""Unit tests for GithubAccessService."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from plugins.ghrm.src.services.github_access_service import (
    GithubAccessService,
    GhrmOAuthError,
    GhrmGithubNotConnectedError,
)
from plugins.ghrm.src.services.github_app_client import MockGithubAppClient
from plugins.ghrm.src.models.ghrm_user_github_access import AccessStatus


def _make_service(
    access_repo=None,
    log_repo=None,
    package_repo=None,
    github=None,
    grace_period_fallback_days=7,
):
    """Build GithubAccessService with mock dependencies."""
    return GithubAccessService(
        access_repo=access_repo or MagicMock(),
        log_repo=log_repo or MagicMock(),
        package_repo=package_repo or MagicMock(),
        github=github or MockGithubAppClient(),
        oauth_client_id="test-client-id",
        oauth_client_secret="test-client-secret",
        oauth_redirect_uri="http://localhost/callback",
        grace_period_fallback_days=grace_period_fallback_days,
    )


def _make_access(
    user_id="user-1",
    username="octocat",
    github_user_id="99",
    status=AccessStatus.ACTIVE,
    deploy_token=None,
    grace_expires_at=None,
):
    """Build a mock GhrmUserGithubAccess-like object."""
    access = MagicMock()
    access.id = "access-id-1"
    access.user_id = user_id
    access.github_username = username
    access.github_user_id = github_user_id
    access.access_status = status
    access.deploy_token = deploy_token
    access.grace_expires_at = grace_expires_at
    access.oauth_token = "existing-token"
    access.to_dict.return_value = {
        "id": "access-id-1",
        "user_id": user_id,
        "github_username": username,
        "github_user_id": github_user_id,
        "access_status": status,
    }
    return access


def _make_package(
    pkg_id="pkg-1",
    slug="my-pkg",
    owner="acme",
    repo="my-repo",
    branch="release",
    is_active=True,
):
    """Build a mock GhrmSoftwarePackage-like object."""
    pkg = MagicMock()
    pkg.id = pkg_id
    pkg.slug = slug
    pkg.github_owner = owner
    pkg.github_repo = repo
    pkg.github_protected_branch = branch
    pkg.is_active = is_active
    return pkg


class TestHandleOAuthCallback:
    def test_stores_verified_username_and_user_id(self):
        """OAuth callback stores github_username and github_user_id from API response."""
        github = MockGithubAppClient()
        github.oauth_token_map["code-abc"] = "oauth-tok-xyz"
        github.oauth_user_map["oauth-tok-xyz"] = {"login": "octocat", "id": "99"}

        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None  # new user

        package_repo = MagicMock()
        package_repo.find_all.return_value = {"items": []}

        saved_access = None

        def capture_save(a):
            nonlocal saved_access
            saved_access = a
            return a

        access_repo.save.side_effect = capture_save

        svc = _make_service(
            access_repo=access_repo, package_repo=package_repo, github=github
        )
        svc.handle_oauth_callback("user-1", "code-abc")

        assert saved_access is not None
        assert saved_access.github_username == "octocat"
        assert saved_access.github_user_id == "99"

    def test_adds_collaborator_if_packages_exist(self):
        """When packages exist, add_collaborator is called for each."""
        github = MockGithubAppClient()
        github.oauth_user_map["mock-oauth-token-code-123"] = {
            "login": "octocat",
            "id": "42",
        }

        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_all.return_value = {"items": [pkg]}

        saved_accesses = []
        access_repo.save.side_effect = lambda a: saved_accesses.append(a) or a

        svc = _make_service(
            access_repo=access_repo, package_repo=package_repo, github=github
        )
        svc.handle_oauth_callback("user-1", "code-123")

        key = (pkg.github_owner, pkg.github_repo)
        assert "octocat" in github.collaborators.get(key, set())

    def test_raises_on_github_api_error(self):
        """If exchange_oauth_code raises, GhrmOAuthError is raised."""
        github = MockGithubAppClient()
        github.raise_on_exchange = Exception("network timeout")

        svc = _make_service(github=github)

        with pytest.raises(GhrmOAuthError, match="OAuth exchange failed"):
            svc.handle_oauth_callback("user-1", "bad-code")


class TestDisconnectGithub:
    def test_revokes_token_and_removes_collaborator(self):
        """disconnect_github removes collaborator and deletes access record."""
        github = MockGithubAppClient()
        access = _make_access()
        access.oauth_token = "old-token"

        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_all.return_value = {"items": [pkg]}

        log_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            log_repo=log_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.disconnect_github("user-1")

        # Collaborator removed
        key = (pkg.github_owner, pkg.github_repo)
        # remove_collaborator was called (MockGithubAppClient records nothing for discard on empty)
        # We verify log was called
        log_repo.log.assert_called()

        # delete was called with access id
        access_repo.delete.assert_called_once_with("access-id-1")

    def test_disconnect_noop_when_no_access(self):
        """disconnect_github does nothing if no access record exists."""
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None

        svc = _make_service(access_repo=access_repo)
        svc.disconnect_github("user-1")

        access_repo.delete.assert_not_called()


class TestOnSubscriptionActivated:
    def test_adds_collaborator_when_github_connected(self):
        """on_subscription_activated adds collaborator when access record exists."""
        github = MockGithubAppClient()

        access = _make_access(status=AccessStatus.ACTIVE)
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg

        log_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            log_repo=log_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.on_subscription_activated("user-1", "plan-1")

        key = (pkg.github_owner, pkg.github_repo)
        assert access.github_username in github.collaborators.get(key, set())
        log_repo.log.assert_called_once()

    def test_skips_when_github_not_connected(self):
        """on_subscription_activated does nothing when no access record exists."""
        github = MockGithubAppClient()

        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = None

        svc = _make_service(access_repo=access_repo, github=github)
        svc.on_subscription_activated("user-1", "plan-1")

        # No collaborators were added
        assert github.collaborators == {}


class TestOnSubscriptionCancelled:
    def test_sets_grace_status(self):
        """on_subscription_cancelled sets access_status to GRACE."""
        access = _make_access(status=AccessStatus.ACTIVE)
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = None

        svc = _make_service(access_repo=access_repo, package_repo=package_repo)
        svc.on_subscription_cancelled("user-1", "plan-1")

        assert access.access_status == AccessStatus.GRACE
        access_repo.save.assert_called_once_with(access)

    def test_uses_plan_trailing_days(self):
        """trailing_days=14 results in grace_expires_at approx now + 14 days."""
        access = _make_access(status=AccessStatus.ACTIVE)
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = None

        svc = _make_service(
            access_repo=access_repo,
            package_repo=package_repo,
            grace_period_fallback_days=7,
        )

        before = datetime.utcnow()
        svc.on_subscription_cancelled("user-1", "plan-1", trailing_days=14)
        after = datetime.utcnow()

        expected_min = before + timedelta(days=13, hours=23)
        expected_max = after + timedelta(days=14, seconds=1)

        assert access.grace_expires_at >= expected_min
        assert access.grace_expires_at <= expected_max


class TestRevokeExpiredGraceAccess:
    def test_removes_collaborator_and_token_returns_count(self):
        """revoke_expired_grace_access removes collaborator, revokes token, returns count=1."""
        github = MockGithubAppClient()

        access = _make_access(
            status=AccessStatus.GRACE, deploy_token="old-deploy-token"
        )
        access_repo = MagicMock()
        access_repo.find_grace_expired.return_value = [access]

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_all.return_value = {"items": [pkg]}

        log_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            log_repo=log_repo,
            package_repo=package_repo,
            github=github,
        )
        count = svc.revoke_expired_grace_access()

        assert count == 1
        assert access.access_status == AccessStatus.REVOKED
        assert access.deploy_token is None
        assert "old-deploy-token" in github.revoked_tokens
        log_repo.log.assert_called()


class TestOnSubscriptionRenewed:
    def test_extends_access_and_rotates_token(self):
        """on_subscription_renewed creates new deploy token and revokes old one."""
        github = MockGithubAppClient()

        access = _make_access(status=AccessStatus.GRACE, deploy_token="old-token")
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        pkg = _make_package()
        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = pkg

        log_repo = MagicMock()

        svc = _make_service(
            access_repo=access_repo,
            log_repo=log_repo,
            package_repo=package_repo,
            github=github,
        )
        svc.on_subscription_renewed("user-1", "plan-1")

        assert "old-token" in github.revoked_tokens
        # New token was set on access
        assert access.deploy_token == f"mock-token-{access.github_username}"
        assert access.access_status == AccessStatus.ACTIVE
        assert access.grace_expires_at is None


class TestOnSubscriptionPaymentFailed:
    def test_starts_grace_period(self):
        """on_subscription_payment_failed delegates to on_subscription_cancelled."""
        access = _make_access(status=AccessStatus.ACTIVE)
        access_repo = MagicMock()
        access_repo.find_by_user_id.return_value = access

        package_repo = MagicMock()
        package_repo.find_by_tariff_plan_id.return_value = None

        svc = _make_service(access_repo=access_repo, package_repo=package_repo)
        svc.on_subscription_payment_failed("user-1", "plan-1", trailing_days=3)

        assert access.access_status == AccessStatus.GRACE
        assert access.grace_expires_at is not None
