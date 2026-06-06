"""Unit tests for the GhrmSoftwarePackage.collaborator_permission field (S51)."""
from plugins.ghrm.src.models.ghrm_software_package import (
    GhrmSoftwarePackage,
    DEFAULT_COLLABORATOR_PERMISSION,
)


class TestCollaboratorPermissionSerialization:
    def test_to_dict_includes_collaborator_permission(self):
        package = GhrmSoftwarePackage(
            tariff_plan_id="00000000-0000-0000-0000-000000000001",
            name="Basic",
            slug="basic",
            github_owner="acme",
            github_repo="basic-repo",
            collaborator_permission="push",
        )
        data = package.to_dict()
        assert data["collaborator_permission"] == "push"

    def test_column_default_is_pull(self):
        column = GhrmSoftwarePackage.__table__.columns["collaborator_permission"]
        assert column.default.arg == DEFAULT_COLLABORATOR_PERMISSION
        assert column.nullable is False
