"""GhrmSoftwarePackageRepository — data access for software packages."""
from typing import Optional, List, Dict, Any
from uuid import UUID
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from vbwd.extensions import db
from vbwd.services.tags_and_custom_fields import (
    resolve_tags_and_custom_fields,
    UnknownEntityTypeError,
)

# The generic entity-type key GHRM registers (in ``on_enable``) so its packages
# are taggable through the core tags port. Tag reads/filters go through that
# port only — this module never imports the tags implementation (D2/agnostic).
_GHRM_PACKAGE_ENTITY_TYPE = "ghrm_software_package"


class GhrmSoftwarePackageRepository:
    def __init__(self, session) -> None:
        self.session = session

    def list_package_tags(self, package_ids: List[UUID]) -> Dict[UUID, List[str]]:
        """Tag slugs for many packages in ONE query via the core tags port (D6).

        Degrades to an empty map when the tags port has no provider or the
        ``ghrm_software_package`` entity type is unregistered (plugin loaded
        without ``on_enable`` having run): a tag filter then yields no matches
        and list items fall back to empty tags — never a 500.
        """
        if not package_ids:
            return {}
        provider = resolve_tags_and_custom_fields()
        if provider is None:
            return {}
        try:
            return provider.get_tags_bulk(_GHRM_PACKAGE_ENTITY_TYPE, list(package_ids))
        except UnknownEntityTypeError:
            return {}

    def list_active_package_ids(
        self, category_slug: Optional[str] = None
    ) -> List[UUID]:
        """Ids of ACTIVE software packages, optionally scoped to one category.

        When ``category_slug`` is given the ids are restricted to packages whose
        linked tariff plan sits in that category — resolved through the SAME
        subscription-owned catalog read model the list path (:meth:`find_all`)
        uses (ghrm declares ``dependencies=["subscription"]``; no subscription
        model import here). An unknown/empty category resolves to no plan ids, so
        ``in_([])`` matches nothing and the result is empty. Absent/blank ⇒ every
        active package (the catalogue tag-option source, unchanged).
        """
        query = self.session.query(GhrmSoftwarePackage.id).filter(
            GhrmSoftwarePackage.is_active.is_(True)
        )
        if category_slug:
            from plugins.subscription.subscription.services.catalog_read_model import (
                CatalogReadModel,
            )

            plan_ids = CatalogReadModel().plan_ids_in_category(category_slug)
            query = query.filter(GhrmSoftwarePackage.tariff_plan_id.in_(plan_ids))
        rows = query.all()
        return [row[0] for row in rows]

    def list_applicable_package_tags(self) -> List[Dict[str, Any]]:
        """Catalog rows applicable to ghrm packages (slug + label/color/metadata).

        Reads through the core tags port only (agnostic). Degrades to an empty
        list under the SAME guard as :meth:`list_package_tags` (no provider or
        the ``ghrm_software_package`` entity type unregistered) — never a 500.
        """
        provider = resolve_tags_and_custom_fields()
        if provider is None:
            return []
        try:
            return provider.list_applicable_tags(_GHRM_PACKAGE_ENTITY_TYPE)
        except UnknownEntityTypeError:
            return []

    def find_by_slug(self, slug: str) -> Optional[GhrmSoftwarePackage]:
        return (
            self.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.slug == slug)
            .first()
        )

    def find_by_id(self, pkg_id: str) -> Optional[GhrmSoftwarePackage]:
        return (
            self.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.id == pkg_id)
            .first()
        )

    def find_by_tariff_plan_id(self, plan_id: str) -> Optional[GhrmSoftwarePackage]:
        from uuid import UUID as _UUID

        try:
            uid = _UUID(str(plan_id))
        except ValueError:
            return None
        return (
            db.session.query(GhrmSoftwarePackage).filter_by(tariff_plan_id=uid).first()
        )

    def find_by_sync_key(self, api_key: str) -> Optional[GhrmSoftwarePackage]:
        return (
            self.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.sync_api_key == api_key)
            .first()
        )

    def find_by_slugs(self, slugs: List[str]) -> List[GhrmSoftwarePackage]:
        return (
            self.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.slug.in_(slugs))
            .all()
        )

    def find_by_tariff_plan_ids(self, plan_ids: List[Any]) -> List[GhrmSoftwarePackage]:
        """Packages linked to any of ``plan_ids`` (the vendor's "my packages").

        Ownership lives on the linked plan's ``vendor_id``; the caller resolves
        the vendor's plan ids and passes them here. Empty input ⇒ empty result
        (``in_([])`` matches nothing).
        """
        if not plan_ids:
            return []
        return (
            self.session.query(GhrmSoftwarePackage)
            .filter(GhrmSoftwarePackage.tariff_plan_id.in_(plan_ids))
            .order_by(
                GhrmSoftwarePackage.sort_order.asc(), GhrmSoftwarePackage.name.asc()
            )
            .all()
        )

    def find_all(
        self,
        page: int = 1,
        per_page: int = 20,
        category_slug: Optional[str] = None,
        query: Optional[str] = None,
        kind: Optional[str] = None,
        tag_slugs: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        q = self.session.query(GhrmSoftwarePackage).filter(
            GhrmSoftwarePackage.is_active == True  # noqa: E712
        )
        if category_slug:
            # Plans in the category come from the subscription-owned catalog
            # read model (ghrm declares dependencies=["subscription"]); no
            # subscription model import here. Empty list ⇒ no matching packages
            # (in_([]) matches nothing), mirroring the prior JOIN against an
            # empty category.
            from plugins.subscription.subscription.services.catalog_read_model import (
                CatalogReadModel,
            )

            plan_ids = CatalogReadModel().plan_ids_in_category(category_slug)
            q = q.filter(GhrmSoftwarePackage.tariff_plan_id.in_(plan_ids))
        if kind:
            q = q.filter(GhrmSoftwarePackage.package_kind == kind)
        if query:
            term = f"%{query}%"
            q = q.filter(
                GhrmSoftwarePackage.name.ilike(term)
                | GhrmSoftwarePackage.slug.ilike(term)
            )
        q = q.order_by(
            GhrmSoftwarePackage.sort_order.asc(), GhrmSoftwarePackage.name.asc()
        )
        if tag_slugs:
            return self._paginate_by_tags(q, page, per_page, tag_slugs)
        total = q.count()
        items = q.offset((page - 1) * per_page).limit(per_page).all()
        return self._page(items, total, page, per_page)

    def _paginate_by_tags(
        self, query, page: int, per_page: int, tag_slugs: List[str]
    ) -> Dict[str, Any]:
        """Filter the SQL candidate set by tag (AND) in Python, then paginate.

        Tags cannot be filtered in SQL without a core change (the tag rows are
        core-owned, resolved only through the port), so the SQL-filtered
        candidates are fetched unpaginated, matched against ``tag_slugs`` (a
        package matches only when it carries EVERY requested slug), and the page
        is sliced in Python. ``total``/``pages`` reflect the filtered set.
        """
        candidates = query.all()
        tags_by_id = self.list_package_tags([package.id for package in candidates])
        required = set(tag_slugs)
        matched = [
            package
            for package in candidates
            if required.issubset(set(tags_by_id.get(package.id, [])))
        ]
        total = len(matched)
        start = (page - 1) * per_page
        items = matched[start : start + per_page]
        return self._page(items, total, page, per_page)

    @staticmethod
    def _page(items: List[Any], total: int, page: int, per_page: int) -> Dict[str, Any]:
        return {
            "items": items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }

    def save(self, pkg: GhrmSoftwarePackage) -> GhrmSoftwarePackage:
        self.session.add(pkg)
        self.session.flush()
        self.session.commit()
        return pkg

    def delete(self, pkg_id: str) -> bool:
        pkg = self.find_by_id(pkg_id)
        if pkg:
            self.session.delete(pkg)
            self.session.flush()
            self.session.commit()
            return True
        return False

    def increment_downloads(self, slug: str) -> None:
        self.session.query(GhrmSoftwarePackage).filter(
            GhrmSoftwarePackage.slug == slug
        ).update(
            {
                GhrmSoftwarePackage.download_counter: GhrmSoftwarePackage.download_counter
                + 1
            }
        )
        self.session.flush()
        self.session.commit()
