"""Unit tests for bundle package_kind / bundle_repos validation (S59).

``validate_package_kind`` normalises the discriminator (default ``single``,
reject anything outside ``ALLOWED_PACKAGE_KINDS``). ``validate_bundle_repos``
requires a non-empty, deduped list of ``{owner, repo}`` (trimmed, non-blank)
for a bundle and forces ``[]`` for a single package. Both reuse
``GhrmValidationError``.
"""
import pytest

from plugins.ghrm.src.services.software_package_service import (
    GhrmValidationError,
    validate_package_kind,
    validate_bundle_repos,
)


class TestValidatePackageKind:
    def test_omitted_defaults_to_single(self):
        assert validate_package_kind(None) == "single"

    def test_single_is_returned_unchanged(self):
        assert validate_package_kind("single") == "single"

    def test_bundle_is_returned_unchanged(self):
        assert validate_package_kind("bundle") == "bundle"

    def test_unknown_kind_is_rejected(self):
        with pytest.raises(GhrmValidationError) as excinfo:
            validate_package_kind("megabundle")
        assert "megabundle" in str(excinfo.value)


class TestValidateBundleReposSingle:
    def test_single_forces_empty_list_regardless_of_input(self):
        result = validate_bundle_repos(
            [{"owner": "acme", "repo": "alpha"}], kind="single"
        )
        assert result == []

    def test_single_with_none_returns_empty(self):
        assert validate_bundle_repos(None, kind="single") == []


class TestValidateBundleReposBundle:
    def test_bundle_returns_trimmed_pairs(self):
        result = validate_bundle_repos(
            [{"owner": " acme ", "repo": " alpha "}], kind="bundle"
        )
        assert result == [{"owner": "acme", "repo": "alpha"}]

    def test_empty_bundle_list_is_rejected(self):
        with pytest.raises(GhrmValidationError):
            validate_bundle_repos([], kind="bundle")

    def test_none_bundle_list_is_rejected(self):
        with pytest.raises(GhrmValidationError):
            validate_bundle_repos(None, kind="bundle")

    def test_missing_owner_is_rejected(self):
        with pytest.raises(GhrmValidationError):
            validate_bundle_repos([{"repo": "alpha"}], kind="bundle")

    def test_missing_repo_is_rejected(self):
        with pytest.raises(GhrmValidationError):
            validate_bundle_repos([{"owner": "acme"}], kind="bundle")

    def test_blank_owner_is_rejected(self):
        with pytest.raises(GhrmValidationError):
            validate_bundle_repos([{"owner": "  ", "repo": "alpha"}], kind="bundle")

    def test_duplicates_are_deduped(self):
        result = validate_bundle_repos(
            [
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "alpha"},
                {"owner": "acme", "repo": "beta"},
            ],
            kind="bundle",
        )
        assert result == [
            {"owner": "acme", "repo": "alpha"},
            {"owner": "acme", "repo": "beta"},
        ]
