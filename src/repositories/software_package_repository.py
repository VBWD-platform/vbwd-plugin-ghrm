"""GhrmSoftwarePackageRepository — data access for software packages."""
from typing import Optional, List, Dict, Any
from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from src.models.tarif_plan import TarifPlan
from src.models.tarif_plan_category import TarifPlanCategory, tarif_plan_category_plans
from src.extensions import db


class GhrmSoftwarePackageRepository:
    def __init__(self, session) -> None:
        self.session = session

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

    def find_all(
        self,
        page: int = 1,
        per_page: int = 20,
        category_slug: Optional[str] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        q = self.session.query(GhrmSoftwarePackage).filter(
            GhrmSoftwarePackage.is_active == True
        )  # noqa
        if category_slug:
            q = (
                q.join(TarifPlan, GhrmSoftwarePackage.tariff_plan_id == TarifPlan.id)
                .join(
                    tarif_plan_category_plans,
                    tarif_plan_category_plans.c.tarif_plan_id == TarifPlan.id,
                )
                .join(
                    TarifPlanCategory,
                    tarif_plan_category_plans.c.category_id == TarifPlanCategory.id,
                )
                .filter(TarifPlanCategory.slug == category_slug)
            )
        if query:
            term = f"%{query}%"
            q = q.filter(
                GhrmSoftwarePackage.name.ilike(term)
                | GhrmSoftwarePackage.slug.ilike(term)
            )
        total = q.count()
        items = (
            q.order_by(
                GhrmSoftwarePackage.sort_order.asc(), GhrmSoftwarePackage.name.asc()
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )
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
