"""Unit tests for SoftwarePackageService.copy_package + the copy-slug helper.

Product decision (make-a-copy bulk action): a copied ``GhrmSoftwarePackage``
lands UNLINKED from any tariff plan (``tariff_plan_id = None``) because the plan
link is UNIQUE 1:1 and a copy must not steal or duplicate the source's plan. The
copy is always inactive, gets a brand-new sync secret, a reset download counter,
and a fresh unique slug (``<base>-copy``, then ``-copy-2`` ...) that fits the
64-char slug column.

These tests use a mocked repository (no DB) so the pure copy logic and the slug
uniqueness helper are exercised in isolation. DB-level behaviour (many NULLs
under a UNIQUE column, route wiring, permissions) lives in the integration
suite.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID
(one reason to change — copy logic in the service, route stays thin); DI (repos
injected); DRY; Liskov (unknown id -> None, never a crash); clean code; no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
from unittest.mock import MagicMock

from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from plugins.ghrm.src.services.software_package_service import SoftwarePackageService


SOURCE_PLAN_ID = "00000000-0000-0000-0000-000000000001"


def _source_package(**overrides) -> GhrmSoftwarePackage:
    base = dict(
        tariff_plan_id=SOURCE_PLAN_ID,
        name="My Package",
        slug="my-package",
        author_name="Ada Lovelace",
        icon_url="https://example.com/icon.png",
        github_owner="acme",
        github_repo="widget",
        description="A widget.",
        github_protected_branch="release",
        github_installation_id="42",
        sync_api_key="source-secret-do-not-reuse",
        tech_specs={"language": "python"},
        related_slugs=["other-package"],
        download_counter=99,
        is_active=True,
        sort_order=7,
        collaborator_permission="pull",
        package_kind="single",
        bundle_repos=[],
    )
    base.update(overrides)
    return GhrmSoftwarePackage(**base)


def _service(package_repo) -> SoftwarePackageService:
    return SoftwarePackageService(
        package_repo=package_repo, sync_repo=MagicMock(), github=None
    )


class TestCopyPackage:
    def test_unknown_id_returns_none(self):
        repo = MagicMock()
        repo.find_by_id.return_value = None

        assert _service(repo).copy_package("missing-id") is None
        repo.save.assert_not_called()

    def test_copy_is_unlinked_and_inactive(self):
        source = _source_package()
        repo = MagicMock()
        repo.find_by_id.return_value = source
        repo.find_by_slug.return_value = None

        copy = _service(repo).copy_package("any-id")

        assert copy is not None
        assert copy.tariff_plan_id is None
        assert copy.is_active is False
        assert copy.name == "My Package (Copy)"
        repo.save.assert_called_once_with(copy)

    def test_copy_regenerates_sync_api_key(self):
        source = _source_package()
        repo = MagicMock()
        repo.find_by_id.return_value = source
        repo.find_by_slug.return_value = None

        copy = _service(repo).copy_package("any-id")

        assert copy.sync_api_key
        assert copy.sync_api_key != source.sync_api_key

    def test_copy_resets_download_counter(self):
        source = _source_package(download_counter=99)
        repo = MagicMock()
        repo.find_by_id.return_value = source
        repo.find_by_slug.return_value = None

        copy = _service(repo).copy_package("any-id")

        assert copy.download_counter == 0

    def test_copy_carries_content_fields(self):
        source = _source_package()
        repo = MagicMock()
        repo.find_by_id.return_value = source
        repo.find_by_slug.return_value = None

        copy = _service(repo).copy_package("any-id")

        assert copy.author_name == source.author_name
        assert copy.icon_url == source.icon_url
        assert copy.github_owner == source.github_owner
        assert copy.github_repo == source.github_repo
        assert copy.description == source.description
        assert copy.github_protected_branch == source.github_protected_branch
        assert copy.github_installation_id == source.github_installation_id
        assert copy.sort_order == source.sort_order
        assert copy.collaborator_permission == source.collaborator_permission
        assert copy.package_kind == source.package_kind

    def test_copy_deep_copies_mutable_json_fields(self):
        source = _source_package(
            tech_specs={"language": "python"},
            related_slugs=["other-package"],
        )
        repo = MagicMock()
        repo.find_by_id.return_value = source
        repo.find_by_slug.return_value = None

        copy = _service(repo).copy_package("any-id")

        assert copy.tech_specs == source.tech_specs
        assert copy.tech_specs is not source.tech_specs
        assert copy.related_slugs == source.related_slugs
        assert copy.related_slugs is not source.related_slugs

    def test_copy_of_bundle_keeps_bundle_repos_without_sharing_reference(self):
        bundle_repos = [
            {"owner": "acme", "repo": "alpha"},
            {"owner": "acme", "repo": "beta"},
        ]
        source = _source_package(package_kind="bundle", bundle_repos=bundle_repos)
        repo = MagicMock()
        repo.find_by_id.return_value = source
        repo.find_by_slug.return_value = None

        copy = _service(repo).copy_package("any-id")

        assert copy.package_kind == "bundle"
        assert copy.bundle_repos == bundle_repos
        assert copy.bundle_repos is not source.bundle_repos


class TestCopySlug:
    def test_first_copy_appends_copy_suffix(self):
        source = _source_package(slug="my-package")
        repo = MagicMock()
        repo.find_by_id.return_value = source
        repo.find_by_slug.return_value = None

        copy = _service(repo).copy_package("any-id")

        assert copy.slug == "my-package-copy"

    def test_taken_slug_bumps_to_copy_2(self):
        source = _source_package(slug="my-package")
        repo = MagicMock()
        repo.find_by_id.return_value = source
        # "my-package-copy" is taken, "my-package-copy-2" is free.
        repo.find_by_slug.side_effect = (
            lambda slug: object() if slug == "my-package-copy" else None
        )

        copy = _service(repo).copy_package("any-id")

        assert copy.slug == "my-package-copy-2"

    def test_slug_stays_within_64_chars_when_source_is_near_limit(self):
        # 62-char base: "<base>-copy" would be 67 chars, so the base is truncated.
        long_base = "a" * 62
        source = _source_package(slug=long_base)
        repo = MagicMock()
        repo.find_by_id.return_value = source
        repo.find_by_slug.return_value = None

        copy = _service(repo).copy_package("any-id")

        assert len(copy.slug) <= 64
        assert copy.slug.endswith("-copy")


class TestToDictNullPlan:
    def test_unlinked_package_serialises_plan_as_json_null(self):
        package = GhrmSoftwarePackage(
            tariff_plan_id=None,
            name="Unlinked",
            slug="unlinked",
            github_owner="acme",
            github_repo="widget",
        )

        assert package.to_dict()["tariff_plan_id"] is None

    def test_linked_package_serialises_plan_as_string(self):
        package = GhrmSoftwarePackage(
            tariff_plan_id=SOURCE_PLAN_ID,
            name="Linked",
            slug="linked",
            github_owner="acme",
            github_repo="widget",
        )

        assert package.to_dict()["tariff_plan_id"] == SOURCE_PLAN_ID


class TestPlanColumnNullable:
    def test_tariff_plan_id_column_is_nullable(self):
        column = GhrmSoftwarePackage.__table__.columns["tariff_plan_id"]
        assert column.nullable is True

    def test_tariff_plan_id_column_remains_unique(self):
        column = GhrmSoftwarePackage.__table__.columns["tariff_plan_id"]
        assert column.unique is True
