"""Unit tests for per-package collaborator-permission validation (S51 + D3).

The granted GitHub permission is configurable per package (the package is
GHRM's per-plan entity). It is stored as the raw GitHub permission string,
validated against the allowed set, and defaults to ``"pull"`` (least
privilege) when omitted.

D3 security guardrail: write-and-above permissions (anything other than
``"pull"``) are only valid when the plugin config flag
``allow_extensive_github_permissions`` is enabled. When the flag is off the
validator accepts only ``"pull"`` (omitted normalises to ``"pull"``) and
rejects every other value with a clear :class:`GhrmValidationError`.
"""
import pytest

from plugins.ghrm.src.models.ghrm_software_package import (
    ALLOWED_COLLABORATOR_PERMISSIONS,
    DEFAULT_COLLABORATOR_PERMISSION,
)
from plugins.ghrm.src.services.software_package_service import (
    GhrmValidationError,
    validate_collaborator_permission,
)


class TestAllowedValues:
    def test_full_github_set_is_the_single_source_of_truth(self):
        assert ALLOWED_COLLABORATOR_PERMISSIONS == (
            "pull",
            "triage",
            "push",
            "maintain",
            "admin",
        )

    def test_default_is_least_privilege_pull(self):
        assert DEFAULT_COLLABORATOR_PERMISSION == "pull"


class TestValidateCollaboratorPermissionExtensiveAllowed:
    """When extensive permissions are enabled the full set is valid."""

    @pytest.mark.parametrize(
        "permission", ["pull", "triage", "push", "maintain", "admin"]
    )
    def test_valid_permission_is_returned_unchanged(self, permission):
        assert (
            validate_collaborator_permission(permission, allow_extensive=True)
            == permission
        )

    def test_omitted_defaults_to_pull(self):
        assert validate_collaborator_permission(None, allow_extensive=True) == "pull"

    def test_invalid_value_raises_validation_error(self):
        with pytest.raises(GhrmValidationError) as excinfo:
            validate_collaborator_permission("owner", allow_extensive=True)
        assert "owner" in str(excinfo.value)

    def test_empty_string_is_treated_as_invalid(self):
        with pytest.raises(GhrmValidationError):
            validate_collaborator_permission("", allow_extensive=True)


class TestValidateCollaboratorPermissionExtensiveDisabled:
    """D3: with the flag off, only Read (``pull``) is permitted anywhere."""

    def test_omitted_defaults_to_pull(self):
        assert validate_collaborator_permission(None, allow_extensive=False) == "pull"

    def test_explicit_pull_is_allowed(self):
        assert validate_collaborator_permission("pull", allow_extensive=False) == "pull"

    @pytest.mark.parametrize("permission", ["triage", "push", "maintain", "admin"])
    def test_write_and_above_is_rejected(self, permission):
        with pytest.raises(GhrmValidationError) as excinfo:
            validate_collaborator_permission(permission, allow_extensive=False)
        message = str(excinfo.value).lower()
        assert "extensive" in message
        assert "read" in message

    def test_unknown_value_is_rejected(self):
        with pytest.raises(GhrmValidationError):
            validate_collaborator_permission("owner", allow_extensive=False)
