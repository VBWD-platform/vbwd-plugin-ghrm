"""S154 P2 / D9 — with cms enabled, the live sitemap lists ghrm's package chunk.

The app boots the way production does (manifest-enabled cms + ghrm; ghrm's
``on_enable`` registers its providers with cms's registry — no manual wiring
here). ``/sitemap.xml`` must list ``sitemap-ghrm-packages-1.xml`` and that chunk
must hold an active package at ``/<live catalogue base>/<slug>`` and never an
inactive one. The repository's sitemap reads are checked against the real DB:
COUNT and the id-ordered pages agree and skip inactive packages. The route tests
skip when cms is absent or not enabled (cms is an optional dependency, D3).

Engineering requirements (binding, restated): TDD-first · DevOps-first · SOLID ·
DI · DRY · Liskov · clean code · NO OVERENGINEERING.
"""
from uuid import uuid4

import pytest

from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage
from plugins.ghrm.src.repositories.software_package_repository import (
    GhrmSoftwarePackageRepository,
)
from plugins.ghrm.src.services.plugin_config import live_catalogue_base
from vbwd.plugins.base import PluginStatus

PACKAGES_CHUNK_PATH = "/sitemap-ghrm-packages-1.xml"


@pytest.fixture
def cms_enabled(app):
    pytest.importorskip("plugins.cms.src.services.seo_registry")
    cms_plugin = app.plugin_manager.get_plugin("cms")
    if cms_plugin is None or cms_plugin.status != PluginStatus.ENABLED:
        pytest.skip("cms is not enabled in this environment (optional dependency)")


def _seed_package(db, is_active):
    slug = f"sitemap-{uuid4().hex[:10]}"
    return GhrmSoftwarePackageRepository(db.session).save(
        GhrmSoftwarePackage(
            id=uuid4(),
            name=slug,
            slug=slug,
            github_owner="acme",
            github_repo=slug,
            is_active=is_active,
        )
    )


def test_sitemap_index_lists_the_ghrm_packages_chunk(db, app, cms_enabled):
    _seed_package(db, is_active=True)

    index_body = app.test_client().get("/sitemap.xml").get_data(as_text=True)

    assert f"{PACKAGES_CHUNK_PATH}</loc>" in index_body


def test_packages_chunk_lists_active_packages_under_the_catalogue_base(
    db, app, cms_enabled
):
    active = _seed_package(db, is_active=True)
    inactive = _seed_package(db, is_active=False)
    catalogue_base = live_catalogue_base().strip("/")

    response = app.test_client().get(PACKAGES_CHUNK_PATH)

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert f"/{catalogue_base}/{active.slug}</loc>" in body
    assert f"/{catalogue_base}/{inactive.slug}</loc>" not in body


def test_repository_count_and_pages_agree_on_active_packages_only(db):
    active_slugs = {_seed_package(db, is_active=True).slug for _ in range(3)}
    inactive_slug = _seed_package(db, is_active=False).slug
    repository = GhrmSoftwarePackageRepository(db.session)

    total = repository.count_active()
    walked = [
        slug
        for offset in range(0, total, 2)
        for slug, _updated_at in repository.list_active_sitemap_rows(offset, 2)
    ]

    assert len(walked) == total
    assert len(set(walked)) == total
    assert active_slugs <= set(walked)
    assert inactive_slug not in walked
