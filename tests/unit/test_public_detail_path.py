"""Unit tests for the admin package ``public_detail_path`` enrichment (flattened).

The fe-admin GHRM package list + editor render a "view on frontend" link that
opens the fe-user public detail page of a package. The backend serializes a
ready-to-use path (``public_detail_path``) on each admin package serialization
so the frontend does not have to reconstruct the routing.

S127 flattened the catalogue: there is ONE catalogue page with in-widget
filters, so the fe-user detail route is now::

    /<catalogue_page_slug>/<package_slug>

There is no category segment: a package is always fetched by its slug, so every
package gets a path and the resolver no longer depends on the subscription
``CatalogReadModel`` or on any configured category.

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY; Liskov (every package → a path, never a crash); no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
from unittest.mock import patch


def _build_resolver_with(config: dict, fallback: dict):
    """Build the resolver with ``_cfg`` / config.json fallback stubbed."""
    from plugins.ghrm.src import routes

    with patch.object(routes, "_cfg", return_value=config), patch.object(
        routes, "_load_config_json_fallback", return_value=fallback
    ):
        return routes._build_public_detail_path_resolver()


class TestPublicDetailPathResolver:
    def test_flattens_to_catalogue_and_slug(self):
        resolver = _build_resolver_with(
            config={"software_catalogue_cms_page_slug": "software"},
            fallback={},
        )

        path = resolver({"tariff_plan_id": "plan-fe", "slug": "my-pkg"})

        assert path == "/software/my-pkg"

    def test_uncategorized_plan_still_gets_a_path(self):
        """A package whose plan is in NO configured category still resolves."""
        resolver = _build_resolver_with(
            config={
                "software_category_slugs": [],
                "software_catalogue_cms_page_slug": "software",
            },
            fallback={},
        )

        path = resolver({"tariff_plan_id": "plan-unknown", "slug": "orphan"})

        assert path == "/software/orphan"

    def test_uses_config_json_fallback_when_db_config_empty(self):
        resolver = _build_resolver_with(
            config={},
            fallback={"software_catalogue_cms_page_slug": "software"},
        )

        path = resolver({"tariff_plan_id": "plan-be", "slug": "from-fallback"})

        assert path == "/software/from-fallback"

    def test_no_dependency_on_tariff_plan_id(self):
        """The resolver never reads ``tariff_plan_id`` — a package with none works."""
        resolver = _build_resolver_with(
            config={"software_catalogue_cms_page_slug": "software"},
            fallback={},
        )

        path = resolver({"slug": "no-plan"})

        assert path == "/software/no-plan"
