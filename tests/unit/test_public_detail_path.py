"""Unit tests for the admin package ``public_detail_path`` enrichment.

The fe-admin GHRM package list + editor render a "view on frontend" link that
opens the fe-user public detail page of a package. The backend serializes a
ready-to-use path (``public_detail_path``) on each admin package serialization
so the frontend does not have to reconstruct the category → catalogue routing.

The path shape mirrors the fe-user detail route
(``/<catalogue_page_slug>/:category_slug/:package_slug``):

    /<catalogue_page_slug>/<category_slug>/<package_slug>

Category resolution: the package's ``tariff_plan_id`` is mapped to the first
configured ``software_category_slugs`` entry whose plans include it (via the
subscription-owned ``CatalogReadModel``); when the plan is in no configured
category we fall back to the first configured category (the detail page fetches
by package slug regardless, so the link still works). When NO categories are
configured at all, ``public_detail_path`` is ``None``.

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY; Liskov (uncategorized plan → fallback, never a crash); no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
from unittest.mock import MagicMock, patch


def _build_resolver_with(config: dict, fallback: dict, plan_ids_by_category: dict):
    """Build the resolver with ``_cfg`` / config.json fallback / catalog stubbed.

    ``plan_ids_by_category`` maps ``category_slug -> [plan_id, ...]`` and drives
    the stubbed ``CatalogReadModel.plan_ids_in_category``.
    """
    from plugins.ghrm.src import routes

    catalog_instance = MagicMock()
    catalog_instance.plan_ids_in_category.side_effect = (
        lambda category_slug: plan_ids_by_category.get(category_slug, [])
    )

    with patch.object(routes, "_cfg", return_value=config), patch.object(
        routes, "_load_config_json_fallback", return_value=fallback
    ), patch(
        "plugins.subscription.subscription.services.catalog_read_model.CatalogReadModel",
        return_value=catalog_instance,
    ):
        return routes._build_public_detail_path_resolver()


class TestPublicDetailPathResolver:
    def test_resolves_configured_category_for_categorized_plan(self):
        resolver = _build_resolver_with(
            config={
                "software_category_slugs": ["backend", "fe-user"],
                "software_catalogue_cms_page_slug": "ghrm-software-catalogue",
            },
            fallback={},
            plan_ids_by_category={
                "backend": ["plan-be"],
                "fe-user": ["plan-fe"],
            },
        )

        path = resolver({"tariff_plan_id": "plan-fe", "slug": "my-pkg"})

        assert path == "/ghrm-software-catalogue/fe-user/my-pkg"

    def test_first_configured_category_wins_when_plan_in_several(self):
        resolver = _build_resolver_with(
            config={
                "software_category_slugs": ["backend", "fe-user"],
                "software_catalogue_cms_page_slug": "ghrm-software-catalogue",
            },
            fallback={},
            plan_ids_by_category={
                "backend": ["plan-shared"],
                "fe-user": ["plan-shared"],
            },
        )

        path = resolver({"tariff_plan_id": "plan-shared", "slug": "shared"})

        assert path == "/ghrm-software-catalogue/backend/shared"

    def test_falls_back_to_first_category_for_uncategorized_plan(self):
        resolver = _build_resolver_with(
            config={
                "software_category_slugs": ["backend", "fe-user"],
                "software_catalogue_cms_page_slug": "ghrm-software-catalogue",
            },
            fallback={},
            plan_ids_by_category={"backend": ["plan-be"]},
        )

        path = resolver({"tariff_plan_id": "plan-unknown", "slug": "orphan"})

        assert path == "/ghrm-software-catalogue/backend/orphan"

    def test_uses_config_json_fallback_when_db_config_empty(self):
        resolver = _build_resolver_with(
            config={},
            fallback={
                "software_category_slugs": ["backend"],
                "software_catalogue_cms_page_slug": "ghrm-software-catalogue",
            },
            plan_ids_by_category={"backend": ["plan-be"]},
        )

        path = resolver({"tariff_plan_id": "plan-be", "slug": "from-fallback"})

        assert path == "/ghrm-software-catalogue/backend/from-fallback"

    def test_returns_none_when_no_categories_configured(self):
        resolver = _build_resolver_with(
            config={"software_catalogue_cms_page_slug": "ghrm-software-catalogue"},
            fallback={},
            plan_ids_by_category={},
        )

        path = resolver({"tariff_plan_id": "plan-be", "slug": "no-cats"})

        assert path is None
