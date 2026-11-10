"""S154 P2 / D9 — ghrm sitemap providers (cms registry v2 contract), unit level.

Two providers over the PUBLIC catalogue, both rooted at the operator-configurable
catalogue base (read live, never hardcoded):

- ``ghrm-packages``   → ``/<base>/<package_slug>`` for every ACTIVE package (the
  public catalogue API's rule), DB-paged through the repository.
- ``ghrm-categories`` → ``/<base>/category/<category_slug>`` for every category the
  public catalogue shows (the configured ``software_category_slugs``).

Entries are built through an injected ``entry_factory`` (cms's ``SitemapEntry``
in production), so ghrm source never imports cms. Each provider follows its
fe-admin switch: OFF → count 0, no rows, and the source is never read.

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from unittest.mock import MagicMock

from plugins.ghrm.src.sitemap_providers import (
    GhrmCategorySitemapProvider,
    GhrmPackageSitemapProvider,
)

UPDATED_AT = datetime(2026, 11, 10, 9, 30, 0)


@dataclass
class _Entry:
    loc: str
    lastmod: Optional[str] = None


def _package_provider(repository, switch_on=True, base="software"):
    return GhrmPackageSitemapProvider(
        repository_factory=lambda: repository,
        catalogue_base=lambda: base,
        entry_factory=_Entry,
        switch_is_on=lambda: switch_on,
    )


def _category_provider(category_slugs: List[str], switch_on=True, base="software"):
    return GhrmCategorySitemapProvider(
        category_slugs=lambda: category_slugs,
        catalogue_base=lambda: base,
        entry_factory=_Entry,
        switch_is_on=lambda: switch_on,
    )


def test_provider_names_are_stable_public_url_ids():
    assert GhrmPackageSitemapProvider.name == "ghrm-packages"
    assert GhrmCategorySitemapProvider.name == "ghrm-categories"


def test_package_count_is_the_active_package_count():
    repository = MagicMock()
    repository.count_active.return_value = 123_456

    assert _package_provider(repository).count() == 123_456


def test_package_page_builds_catalogue_paths_with_lastmod():
    repository = MagicMock()
    repository.list_active_sitemap_rows.return_value = [
        ("cli-tool", UPDATED_AT),
        ("fe-kit", None),
    ]

    entries = list(_package_provider(repository).page(100, 2))

    repository.list_active_sitemap_rows.assert_called_once_with(100, 2)
    assert entries == [
        _Entry(loc="/software/cli-tool", lastmod=UPDATED_AT.isoformat()),
        _Entry(loc="/software/fe-kit", lastmod=None),
    ]


def test_package_paths_follow_a_non_default_catalogue_base():
    repository = MagicMock()
    repository.list_active_sitemap_rows.return_value = [("cli-tool", None)]

    entries = list(_package_provider(repository, base="/apps/").page(0, 1))

    assert entries == [_Entry(loc="/apps/cli-tool")]


def test_package_switch_off_yields_nothing_and_never_queries():
    repository = MagicMock()

    provider = _package_provider(repository, switch_on=False)

    assert provider.count() == 0
    assert list(provider.page(0, 50_000)) == []
    repository.count_active.assert_not_called()
    repository.list_active_sitemap_rows.assert_not_called()


def test_category_count_is_the_number_of_shown_categories():
    assert _category_provider(["backend", "fe-user", "fe-admin"]).count() == 3


def test_category_page_slices_the_categories_in_their_shown_order():
    provider = _category_provider(["backend", "fe-user", "fe-admin"])

    assert list(provider.page(1, 5)) == [
        _Entry(loc="/software/category/fe-user"),
        _Entry(loc="/software/category/fe-admin"),
    ]


def test_count_and_pages_agree_for_the_categories():
    provider = _category_provider(["a", "b", "c", "d", "e"])

    walked = [
        entry.loc for offset in range(0, 5, 2) for entry in provider.page(offset, 2)
    ]

    assert len(walked) == provider.count()
    assert len(set(walked)) == provider.count()


def test_category_paths_follow_a_non_default_catalogue_base():
    provider = _category_provider(["backend"], base="apps")

    assert list(provider.page(0, 1)) == [_Entry(loc="/apps/category/backend")]


def test_category_switch_off_yields_nothing_and_never_reads_categories():
    category_slugs = MagicMock(return_value=["backend"])
    provider = GhrmCategorySitemapProvider(
        category_slugs=category_slugs,
        catalogue_base=lambda: "software",
        entry_factory=_Entry,
        switch_is_on=lambda: False,
    )

    assert provider.count() == 0
    assert list(provider.page(0, 50_000)) == []
    category_slugs.assert_not_called()
