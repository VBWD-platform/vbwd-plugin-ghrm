"""GHRM software-package search provider (cross-entity search seam).

Contributes active software packages to the core ``search_provider_registry``
so the ``/search`` bot can find them. Searches public, non-personal fields
(name / slug, via the repository's ``find_all(query=...)``) and re-resolves a
tapped hit by slug.

Public URL choice
-----------------
The canonical public detail route is ``/category/<category_slug>/<package_slug>``,
but a package's category is owned by the subscription catalog (resolved through
the plan→category link) and is NOT cheaply available from the package row alone.
Rather than run a cross-plugin category lookup per hit, this provider uses the
always-available, query-driven fallback ``/category/search?q=<slug>`` (a public
fe-user route that resolves the package by slug). This keeps the provider cheap
and correct without coupling to the subscription category graph. GHRM has no
price column, so ``price`` is omitted.
"""
from __future__ import annotations

from typing import List, Optional

from vbwd.services.search import SearchHit

ENTITY_TYPE = "ghrm_package"
ENTITY_LABEL = "Software"
DETAIL_URL_TEMPLATE = "/category/search?q={slug}"


class GhrmPackageSearchProvider:
    """A ``SearchProvider`` for active GHRM software packages."""

    entity_type: str = ENTITY_TYPE
    entity_label: str = ENTITY_LABEL

    def search(self, query: str, *, limit: int = 5) -> List[SearchHit]:
        if not query or not query.strip():
            return []
        result = self._repository().find_all(
            page=1, per_page=limit, query=query.strip()
        )
        return [self._to_hit(package) for package in result["items"]]

    def get_detail(self, key: str) -> Optional[SearchHit]:
        repository = self._repository()
        package = repository.find_by_slug(key)
        if package is None:
            package = self._find_by_id(repository, key)
        if package is None or not package.is_active:
            return None
        return self._to_hit(package)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _find_by_id(repository, key: str):
        """Re-resolve by id only when ``key`` is a valid UUID. Issuing the query
        with a non-UUID key would raise a DataError and poison the session, so we
        validate the shape first and simply return None otherwise."""
        from uuid import UUID

        try:
            UUID(str(key))
        except (ValueError, TypeError):
            return None
        return repository.find_by_id(key)

    @staticmethod
    def _repository():
        from vbwd.extensions import db
        from plugins.ghrm.src.repositories.software_package_repository import (
            GhrmSoftwarePackageRepository,
        )

        return GhrmSoftwarePackageRepository(db.session)

    def _to_hit(self, package) -> SearchHit:
        return SearchHit(
            entity_type=self.entity_type,
            entity_label=self.entity_label,
            key=package.slug,
            title=package.name,
            snippet=self._snippet(package.description),
            url=DETAIL_URL_TEMPLATE.format(slug=package.slug),
            image_url=package.icon_url,
            price=None,
        )

    @staticmethod
    def _snippet(description: Optional[str], *, max_length: int = 160) -> str:
        if not description:
            return ""
        text = description.strip()
        if len(text) <= max_length:
            return text
        return text[: max_length - 1].rstrip() + "…"
