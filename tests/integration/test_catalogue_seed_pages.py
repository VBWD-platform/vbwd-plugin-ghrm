"""Integration tests — the GHRM catalogue seed produces ONE ``software`` root
page and removes the retired ``/category`` pages (S127).

The catalogue collapsed from "one CMS page per category" to ONE catalogue page
(``software``) whose widget carries the category/tag/kind filters. The seed
therefore:

  * creates a single ``software`` page — published, ``index,follow``, on the
    catalogue layout that hosts the ``GhrmCatalogueContent`` widget (this is the
    page the fe-user ``base`` route renders via ``CmsPage(slug=cataloguePageSlug)``),
  * destructively removes the retired ``/category`` root page and every
    ``category/<slug>`` page it used to create — and NOTHING else.

``populate_ghrm.py`` is dev/demo seeding that never runs on deploy, so the
destructive cleanup is safe. Each test runs inside the rolled-back ``db``
transaction (self-cleaning, no wipe); the seed is invoked through its own
``seed_catalog`` function (no raw SQL).

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY; Liskov (missing shared widgets/styles degrade, never crash); no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import os

os.environ["GHRM_USE_MOCK_GITHUB"] = "true"

from plugins.ghrm.src.bin import populate_ghrm  # noqa: E402
from plugins.cms.src.models.cms_post import CmsPost  # noqa: E402
from plugins.cms.src.models.cms_layout import CmsLayout  # noqa: E402
from plugins.cms.src.models.cms_widget import CmsWidget  # noqa: E402
from plugins.cms.src.models.cms_layout_widget import CmsLayoutWidget  # noqa: E402


def _make_stale_category_page(db, slug: str) -> None:
    page = CmsPost(
        slug=slug,
        type="page",
        title=f"Stale {slug}",
        language="en",
        content_json={"type": "doc", "content": []},
        status="published",
    )
    db.session.add(page)
    db.session.flush()


class TestCatalogueSeedPages:
    def test_seed_creates_published_indexed_software_page_on_catalogue_layout(self, db):
        populate_ghrm.seed_catalog(db.session)

        software = (
            db.session.query(CmsPost).filter_by(slug="software", type="page").one()
        )
        assert software.status == "published"
        assert software.robots == "index,follow"

        layout = db.session.get(CmsLayout, software.layout_id)
        assert layout is not None
        assert layout.slug == populate_ghrm.CATALOGUE_LAYOUT_SLUG

    def test_seed_removes_retired_category_pages_only(self, db):
        _make_stale_category_page(db, "category")
        _make_stale_category_page(db, "category/backend")
        _make_stale_category_page(db, "category/fe-user")
        # A look-alike page that must SURVIVE (scoped delete, not a prefix match).
        _make_stale_category_page(db, "category-guide")

        populate_ghrm.seed_catalog(db.session)

        remaining_category = (
            db.session.query(CmsPost)
            .filter(
                CmsPost.type == "page",
                (CmsPost.slug == "category") | (CmsPost.slug.like("category/%")),
            )
            .all()
        )
        assert remaining_category == []

        survivor = (
            db.session.query(CmsPost)
            .filter_by(slug="category-guide", type="page")
            .first()
        )
        assert survivor is not None

    def test_catalogue_layout_hosts_the_categories_widget_with_prop(self, db):
        populate_ghrm.seed_catalog(db.session)

        layout = (
            db.session.query(CmsLayout)
            .filter_by(slug=populate_ghrm.CATALOGUE_LAYOUT_SLUG)
            .one()
        )
        widget = db.session.query(CmsWidget).filter_by(slug="ghrm-categories").one()
        link = (
            db.session.query(CmsLayoutWidget)
            .filter_by(
                layout_id=layout.id, widget_id=widget.id, area_name="ghrm-categories"
            )
            .first()
        )
        assert link is not None
        assert widget.content_json["props"]["items_per_page"] == 12
