"""GHRM entity exchangers for the S46 data-exchange seam (S46.6).

Exposes the GHRM software-package catalog through the core ``EntityExchanger``
contract so it appears on the generic Settings → Import/Export page and the
per-list controls.

Entity (v1 — packages only):

* ``ghrm_packages`` (``GhrmSoftwarePackage``, natural key ``slug``) —
  import+export.

Design notes:

* **Reused perms** — the plugin already ships ``ghrm.packages.view`` /
  ``ghrm.packages.manage``; the exchanger maps ``export_permission`` /
  ``import_permission`` onto those (single source of truth).
* **Secrets** — ``sync_api_key`` (the per-package push secret) and
  ``github_installation_id`` are stripped on export and never written on import,
  so a transported catalog cannot leak credentials; the model's
  ``sync_api_key`` default regenerates a fresh secret on (re-)create.
* **DRY** — reuses :class:`BaseModelExchanger`; only the narrow
  ``_SessionModelRepository`` adapter is added (mirrors core / CMS).
* **No core change** — registration happens in ``GhrmPlugin.on_enable`` through
  the shared ``db.session``; core imports no ``plugins.*`` module.

Engineering requirements (binding, restated): TDD-first; DevOps-first; SOLID
(one exchanger, narrow port); DI (session injected); DRY; Liskov; clean code;
no overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin ghrm
--full``.
"""
from typing import Any, List, Optional

from vbwd.services.data_exchange.base_model_exchanger import BaseModelExchanger
from vbwd.services.data_exchange.port import CLUSTER_SALES, EntityExchanger
from vbwd.services.data_exchange.registry import data_exchange_registry

# Existing GHRM permissions (single source — GhrmPlugin.admin_permissions).
PERM_PACKAGES_VIEW = "ghrm.packages.view"
PERM_PACKAGES_MANAGE = "ghrm.packages.manage"


class _SessionModelRepository:
    """Narrow model repo satisfying the ``BaseModelExchanger`` contract (ISP).

    Mirrors core's / CMS's adapter: the GHRM package repository exposes domain
    finders rather than the four flat methods the base exchanger needs.
    """

    def __init__(self, session: Any, model_class: type, natural_key: str) -> None:
        self._session = session
        self._model_class = model_class
        self._natural_key = natural_key

    def find_all(self) -> List[Any]:
        return self._session.query(self._model_class).all()

    def find_by_natural_key(self, value: Any) -> Optional[Any]:
        column = getattr(self._model_class, self._natural_key)
        return self._session.query(self._model_class).filter(column == value).first()

    def add(self, instance: Any) -> None:
        self._session.add(instance)

    def delete_all(self) -> None:
        self._session.query(self._model_class).delete()


class _PermissionMappedModelExchanger(BaseModelExchanger):
    """A ``BaseModelExchanger`` whose perms map onto existing GHRM perms."""

    def __init__(
        self,
        *,
        view_permission: str,
        manage_permission: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._view_permission = view_permission
        self._manage_permission = manage_permission

    @property
    def export_permission(self) -> str:
        return self._view_permission

    @property
    def import_permission(self) -> str:
        return self._manage_permission


def build_ghrm_exchangers(session: Any) -> List[EntityExchanger]:
    """Construct the GHRM exchangers bound to ``session``."""
    from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage

    return [
        _PermissionMappedModelExchanger(
            entity_key="ghrm_packages",
            label="GHRM Packages",
            cluster=CLUSTER_SALES,
            natural_key="slug",
            model_class=GhrmSoftwarePackage,
            repository=_SessionModelRepository(session, GhrmSoftwarePackage, "slug"),
            session=session,
            public_fields=[
                "slug",
                "name",
                "tariff_plan_id",
                "author_name",
                "icon_url",
                "github_owner",
                "github_repo",
                "description",
                "github_protected_branch",
                "tech_specs",
                "related_slugs",
                "is_active",
                "sort_order",
                "collaborator_permission",
            ],
            secret_fields=frozenset({"sync_api_key", "github_installation_id"}),
            supported_formats=frozenset({"json", "csv"}),
            view_permission=PERM_PACKAGES_VIEW,
            manage_permission=PERM_PACKAGES_MANAGE,
        ),
    ]


def register_ghrm_exchangers(session: Any) -> None:
    """Register the GHRM exchangers into the registry (idempotent).

    Called from ``GhrmPlugin.on_enable``. Re-registering replaces by key, so a
    repeat enable (per-test app) is clear-safe.
    """
    for exchanger in build_ghrm_exchangers(session):
        data_exchange_registry.register(exchanger)
