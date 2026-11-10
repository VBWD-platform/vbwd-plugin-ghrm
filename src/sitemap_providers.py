"""GHRM sitemap providers (cms sitemap registry v2, S154 P2 / D9).

Two providers over the PUBLIC software catalogue, both rooted at the
operator-configurable catalogue base (``software`` on vbwd.cc), read live on
every call and never hardcoded:

- ``ghrm-packages``: every active package (the public catalogue API's rule) at
  ``/<base>/<package_slug>``. DB-paged: ``count()`` is a SQL COUNT and ``page()``
  reads one ``ORDER BY id`` OFFSET/LIMIT window of ``(slug, updated_at)``.
- ``ghrm-categories``: every category the public catalogue shows (the configured
  ``software_category_slugs``, a short config list) at
  ``/<base>/category/<category_slug>``.

The duck-typed contract (``name`` / ``count()`` / ``page(offset, limit)``) lives
in cms; ghrm never imports cms here. The entry type (cms ``SitemapEntry``) is
injected by the plugin wiring root, which registers these providers only when
cms is installed (optional dependency, D3). Each provider follows its fe-admin
switch, read live per call: OFF → ``count()`` 0 and no rows.
"""
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple

from plugins.ghrm.src.repositories.software_package_repository import (
    GhrmSoftwarePackageRepository,
)
from plugins.ghrm.src.services.plugin_config import (
    SEO_INDEX_CATEGORIES_KEY,
    SEO_INDEX_PACKAGES_KEY,
    live_catalogue_base,
    seo_indexing_enabled,
    shown_category_slugs,
)

SitemapRow = Tuple[str, Optional[datetime]]


class _GhrmSitemapProvider:
    """Switch gate, catalogue base and entry building shared by both providers."""

    name: str

    def __init__(
        self,
        catalogue_base: Callable[[], str],
        entry_factory: Callable[..., Any],
        switch_is_on: Callable[[], bool],
    ) -> None:
        self._catalogue_base = catalogue_base
        self._entry_factory = entry_factory
        self._switch_is_on = switch_is_on

    def count(self) -> int:
        if not self._switch_is_on():
            return 0
        return self._count_rows()

    def page(self, offset: int, limit: int) -> List[Any]:
        if not self._switch_is_on():
            return []
        base_path = "/" + self._catalogue_base().strip("/")
        return [
            self._entry_factory(
                loc=self._path(base_path, slug),
                lastmod=updated_at.isoformat() if updated_at else None,
            )
            for slug, updated_at in self._page_rows(offset, limit)
        ]

    def _path(self, base_path: str, slug: str) -> str:
        raise NotImplementedError

    def _count_rows(self) -> int:
        raise NotImplementedError

    def _page_rows(self, offset: int, limit: int) -> List[SitemapRow]:
        raise NotImplementedError


class GhrmPackageSitemapProvider(_GhrmSitemapProvider):
    """Active software packages at ``/<base>/<package_slug>``."""

    name = "ghrm-packages"

    def __init__(
        self,
        repository_factory: Callable[[], GhrmSoftwarePackageRepository],
        **shared: Any,
    ) -> None:
        super().__init__(**shared)
        self._repository_factory = repository_factory

    def _path(self, base_path: str, slug: str) -> str:
        return f"{base_path}/{slug}"

    def _count_rows(self) -> int:
        return self._repository_factory().count_active()

    def _page_rows(self, offset: int, limit: int) -> List[SitemapRow]:
        return self._repository_factory().list_active_sitemap_rows(offset, limit)


class GhrmCategorySitemapProvider(_GhrmSitemapProvider):
    """Shown software categories at ``/<base>/category/<category_slug>``."""

    name = "ghrm-categories"

    def __init__(self, category_slugs: Callable[[], List[str]], **shared: Any) -> None:
        super().__init__(**shared)
        self._category_slugs = category_slugs

    def _path(self, base_path: str, slug: str) -> str:
        return f"{base_path}/category/{slug}"

    def _count_rows(self) -> int:
        return len(self._category_slugs())

    def _page_rows(self, offset: int, limit: int) -> List[SitemapRow]:
        return [
            (slug, None) for slug in self._category_slugs()[offset : offset + limit]
        ]


def build_sitemap_providers(
    session_factory: Callable[[], Any], entry_factory: Callable[..., Any]
) -> List[_GhrmSitemapProvider]:
    """The ghrm providers wired to live sessions, config and fe-admin switches."""
    return [
        GhrmPackageSitemapProvider(
            repository_factory=lambda: GhrmSoftwarePackageRepository(session_factory()),
            catalogue_base=live_catalogue_base,
            entry_factory=entry_factory,
            switch_is_on=lambda: seo_indexing_enabled(SEO_INDEX_PACKAGES_KEY),
        ),
        GhrmCategorySitemapProvider(
            category_slugs=shown_category_slugs,
            catalogue_base=live_catalogue_base,
            entry_factory=entry_factory,
            switch_is_on=lambda: seo_indexing_enabled(SEO_INDEX_CATEGORIES_KEY),
        ),
    ]
