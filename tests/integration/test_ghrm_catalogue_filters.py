"""Integration tests — GHRM catalogue in-widget filters (S127).

The catalogue collapsed from "one CMS page per category" to ONE catalogue page
whose widget filters the packages list. This exercises the new query surface of
``GET /api/v1/ghrm/packages``:

  * ``kind`` — ``single`` | ``bundle`` (any other value => 400),
  * ``tags`` — comma-separated slugs, AND semantics (a package must carry EVERY
    requested slug),
  * each list item carries a ``tags`` array, populated with ONE bulk tag call
    (no N+1),
  * filters compose (``category_slug`` + ``kind`` + ``tags`` + ``q``),

plus the new ``GET /api/v1/ghrm/tags`` endpoint feeding the widget's tag-filter
options.

Data is seeded through the ORM models / the core tags port (never raw SQL). Each
test runs inside the rolled-back ``db`` transaction (self-cleaning, no wipe).

Engineering requirements (binding, restated): TDD-first; DevOps-first;
SOLID/DI/DRY; Liskov (degrade to empty tags, never a crash); no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin ghrm --full``.
"""
import os
import uuid

os.environ["GHRM_USE_MOCK_GITHUB"] = "true"

from unittest.mock import MagicMock  # noqa: E402

from vbwd.models.enums import BillingPeriod  # noqa: E402
from plugins.subscription.subscription.models.tarif_plan import (  # noqa: E402
    TarifPlan,
)
from plugins.subscription.subscription.models.tarif_plan_category import (  # noqa: E402
    TarifPlanCategory,
)
from plugins.ghrm.src.models.ghrm_software_package import (  # noqa: E402
    GhrmSoftwarePackage,
)
from plugins.ghrm.src.repositories import (  # noqa: E402
    software_package_repository as repo_module,
)

_ENTITY_TYPE = "ghrm_software_package"


def _make_plan(db) -> TarifPlan:
    plan = TarifPlan(
        name=f"Plan {uuid.uuid4().hex[:8]}",
        slug=f"plan-{uuid.uuid4().hex[:8]}",
        price=9.0,
        billing_period=BillingPeriod.MONTHLY,
    )
    db.session.add(plan)
    db.session.flush()
    return plan


def _make_package(
    db, slug: str, *, kind: str = "single", is_active: bool = True
) -> GhrmSoftwarePackage:
    plan = _make_plan(db)
    package = GhrmSoftwarePackage(
        tariff_plan_id=plan.id,
        name=f"Package {slug}",
        slug=slug,
        github_owner="acme",
        github_repo=slug,
        github_installation_id="install-123",
        package_kind=kind,
        bundle_repos=([{"owner": "acme", "repo": slug}] if kind == "bundle" else []),
        is_active=is_active,
    )
    db.session.add(package)
    db.session.flush()
    return package


def _tag(app, package, slugs) -> None:
    with app.app_context():
        app.container.tags_and_custom_fields().set_tags(_ENTITY_TYPE, package.id, slugs)


def _slugs(payload) -> list:
    return [item["slug"] for item in payload["items"]]


class TestKindFilter:
    def test_kind_single_returns_only_single(self, app, db, client):
        single = _make_package(db, f"single-{uuid.uuid4().hex[:8]}", kind="single")
        bundle = _make_package(db, f"bundle-{uuid.uuid4().hex[:8]}", kind="bundle")
        db.session.commit()

        slugs = _slugs(
            client.get("/api/v1/ghrm/packages?kind=single&per_page=100").get_json()
        )

        assert single.slug in slugs
        assert bundle.slug not in slugs

    def test_kind_bundle_returns_only_bundle(self, app, db, client):
        single = _make_package(db, f"single-{uuid.uuid4().hex[:8]}", kind="single")
        bundle = _make_package(db, f"bundle-{uuid.uuid4().hex[:8]}", kind="bundle")
        db.session.commit()

        slugs = _slugs(
            client.get("/api/v1/ghrm/packages?kind=bundle&per_page=100").get_json()
        )

        assert bundle.slug in slugs
        assert single.slug not in slugs

    def test_kind_garbage_returns_400(self, app, db, client):
        response = client.get("/api/v1/ghrm/packages?kind=garbage")
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_no_kind_returns_both(self, app, db, client):
        single = _make_package(db, f"single-{uuid.uuid4().hex[:8]}", kind="single")
        bundle = _make_package(db, f"bundle-{uuid.uuid4().hex[:8]}", kind="bundle")
        db.session.commit()

        slugs = _slugs(client.get("/api/v1/ghrm/packages?per_page=100").get_json())

        assert single.slug in slugs
        assert bundle.slug in slugs


class TestTagFilter:
    def test_single_tag_matches(self, app, db, client):
        tag = f"vue-{uuid.uuid4().hex[:8]}"
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, package, [tag])

        slugs = _slugs(client.get(f"/api/v1/ghrm/packages?tags={tag}").get_json())

        assert slugs == [package.slug]

    def test_and_semantics_excludes_partial_match(self, app, db, client):
        tag_a = f"a-{uuid.uuid4().hex[:8]}"
        tag_b = f"b-{uuid.uuid4().hex[:8]}"
        both = _make_package(db, f"both-{uuid.uuid4().hex[:8]}")
        only_a = _make_package(db, f"only-a-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, both, [tag_a, tag_b])
        _tag(app, only_a, [tag_a])

        both_only = _slugs(
            client.get(f"/api/v1/ghrm/packages?tags={tag_a},{tag_b}").get_json()
        )
        assert both.slug in both_only
        assert only_a.slug not in both_only

        # A single requested tag still matches both packages carrying it.
        with_a = _slugs(client.get(f"/api/v1/ghrm/packages?tags={tag_a}").get_json())
        assert both.slug in with_a
        assert only_a.slug in with_a

    def test_empty_segments_ignored(self, app, db, client):
        tag_a = f"a-{uuid.uuid4().hex[:8]}"
        tag_b = f"b-{uuid.uuid4().hex[:8]}"
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, package, [tag_a, tag_b])

        slugs = _slugs(
            client.get(f"/api/v1/ghrm/packages?tags={tag_a},,{tag_b}").get_json()
        )
        assert package.slug in slugs

    def test_unknown_tag_returns_empty(self, app, db, client):
        _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()

        payload = client.get(
            f"/api/v1/ghrm/packages?tags=nope-{uuid.uuid4().hex[:8]}"
        ).get_json()

        assert payload["items"] == []
        assert payload["total"] == 0

    def test_total_and_pages_reflect_filtered_set(self, app, db, client):
        tag = f"grp-{uuid.uuid4().hex[:8]}"
        packages = [_make_package(db, f"pkg-{uuid.uuid4().hex[:8]}") for _ in range(3)]
        other = _make_package(db, f"other-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        for package in packages:
            _tag(app, package, [tag])
        _tag(app, other, [f"unrelated-{uuid.uuid4().hex[:8]}"])

        payload = client.get(f"/api/v1/ghrm/packages?tags={tag}&per_page=2").get_json()

        assert payload["total"] == 3
        assert payload["pages"] == 2
        assert len(payload["items"]) == 2

    def test_pagination_across_tag_filtered_set(self, app, db, client):
        tag = f"grp-{uuid.uuid4().hex[:8]}"
        packages = [_make_package(db, f"pkg-{uuid.uuid4().hex[:8]}") for _ in range(2)]
        db.session.commit()
        for package in packages:
            _tag(app, package, [tag])

        page1 = client.get(
            f"/api/v1/ghrm/packages?tags={tag}&per_page=1&page=1"
        ).get_json()
        page2 = client.get(
            f"/api/v1/ghrm/packages?tags={tag}&per_page=1&page=2"
        ).get_json()

        assert page1["total"] == 2
        assert page2["total"] == 2
        assert len(page1["items"]) == 1
        assert len(page2["items"]) == 1
        assert page1["items"][0]["slug"] != page2["items"][0]["slug"]
        assert {page1["items"][0]["slug"], page2["items"][0]["slug"]} == {
            packages[0].slug,
            packages[1].slug,
        }


class TestFiltersCompose:
    def test_category_kind_tags_query_together(self, app, db, client):
        tag = f"grp-{uuid.uuid4().hex[:8]}"
        category_slug = f"cat-{uuid.uuid4().hex[:8]}"
        category = TarifPlanCategory(name="Cat", slug=category_slug)
        db.session.add(category)
        db.session.flush()

        # The target: bundle kind, tagged, in the category, name matches the query.
        target = _make_package(db, f"target-{uuid.uuid4().hex[:8]}", kind="bundle")
        target.name = f"Findable {uuid.uuid4().hex[:8]}"
        category.tarif_plans.append(db.session.get(TarifPlan, target.tariff_plan_id))
        # Distractors, each failing exactly one predicate.
        wrong_kind = _make_package(db, f"single-{uuid.uuid4().hex[:8]}", kind="single")
        category.tarif_plans.append(
            db.session.get(TarifPlan, wrong_kind.tariff_plan_id)
        )
        wrong_cat = _make_package(db, f"nocat-{uuid.uuid4().hex[:8]}", kind="bundle")
        db.session.commit()
        _tag(app, target, [tag])
        _tag(app, wrong_kind, [tag])
        _tag(app, wrong_cat, [tag])

        query_term = target.name.split()[-1]
        payload = client.get(
            f"/api/v1/ghrm/packages?category_slug={category_slug}"
            f"&kind=bundle&tags={tag}&q={query_term}"
        ).get_json()

        assert _slugs(payload) == [target.slug]


class TestListItemTags:
    def test_each_item_carries_tags(self, app, db, client):
        tag = f"vue-{uuid.uuid4().hex[:8]}"
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, package, [tag])

        items = client.get("/api/v1/ghrm/packages?per_page=100").get_json()["items"]
        match = next(item for item in items if item["slug"] == package.slug)

        assert match["tags"] == [tag]

    def test_untagged_item_carries_empty_tags(self, app, db, client):
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()

        items = client.get("/api/v1/ghrm/packages?per_page=100").get_json()["items"]
        match = next(item for item in items if item["slug"] == package.slug)

        assert match["tags"] == []

    def test_tags_use_one_bulk_call(self, app, db, client, monkeypatch):
        _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()

        real_provider = repo_module.resolve_tags_and_custom_fields()
        spy = MagicMock(wraps=real_provider)
        monkeypatch.setattr(repo_module, "resolve_tags_and_custom_fields", lambda: spy)

        response = client.get("/api/v1/ghrm/packages?per_page=100")

        assert response.status_code == 200
        assert spy.get_tags_bulk.call_count == 1


class TestTagsEndpoint:
    def test_lists_tags_used_by_an_active_package(self, app, db, client):
        tag = f"vue-{uuid.uuid4().hex[:8]}"
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, package, [tag])

        response = client.get("/api/v1/ghrm/tags")

        assert response.status_code == 200
        slugs = {row["slug"] for row in response.get_json()["tags"]}
        assert tag in slugs

    def test_excludes_tag_only_on_an_inactive_package(self, app, db, client):
        """A global tag carried only by an INACTIVE package is not offered."""
        tag = f"inactive-{uuid.uuid4().hex[:8]}"
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}", is_active=False)
        db.session.commit()
        # set_tags auto-creates the slug as a GLOBAL catalog tag (so it WOULD
        # appear in list_applicable_tags), yet only an inactive package uses it.
        _tag(app, package, [tag])

        response = client.get("/api/v1/ghrm/tags")

        assert response.status_code == 200
        slugs = {row["slug"] for row in response.get_json()["tags"]}
        assert tag not in slugs

    def test_offers_used_tag_but_not_a_global_unused_one(self, app, db, client):
        """Only tags on an active package survive; a global unused tag drops."""
        used_tag = f"used-{uuid.uuid4().hex[:8]}"
        unused_tag = f"unused-{uuid.uuid4().hex[:8]}"
        active = _make_package(db, f"active-{uuid.uuid4().hex[:8]}")
        inactive = _make_package(
            db, f"inactive-{uuid.uuid4().hex[:8]}", is_active=False
        )
        db.session.commit()
        _tag(app, active, [used_tag])
        _tag(app, inactive, [unused_tag])

        response = client.get("/api/v1/ghrm/tags")

        assert response.status_code == 200
        slugs = {row["slug"] for row in response.get_json()["tags"]}
        assert used_tag in slugs
        assert unused_tag not in slugs

    def test_uses_one_bulk_get_tags_bulk_call(self, app, db, client, monkeypatch):
        """Options are built from ONE bulk tag call over the active packages."""
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, package, [f"vue-{uuid.uuid4().hex[:8]}"])

        real_provider = repo_module.resolve_tags_and_custom_fields()
        spy = MagicMock(wraps=real_provider)
        monkeypatch.setattr(repo_module, "resolve_tags_and_custom_fields", lambda: spy)

        response = client.get("/api/v1/ghrm/tags")

        assert response.status_code == 200
        assert spy.get_tags_bulk.call_count == 1


class TestTagsEndpointCategoryAware:
    """``GET /ghrm/tags?category_slug=`` scopes options to that category (S127)."""

    def _category(self, db, slug: str) -> TarifPlanCategory:
        category = TarifPlanCategory(name=f"Cat {slug}", slug=slug)
        db.session.add(category)
        db.session.flush()
        return category

    def _attach(self, db, category, package) -> None:
        category.tarif_plans.append(db.session.get(TarifPlan, package.tariff_plan_id))

    def _tag_slugs(self, response) -> set:
        return {row["slug"] for row in response.get_json()["tags"]}

    def test_category_scopes_options_to_that_category(self, app, db, client):
        cat_a = self._category(db, f"cata-{uuid.uuid4().hex[:8]}")
        cat_b = self._category(db, f"catb-{uuid.uuid4().hex[:8]}")
        tag_a = f"taga-{uuid.uuid4().hex[:8]}"
        tag_b = f"tagb-{uuid.uuid4().hex[:8]}"
        pkg_a = _make_package(db, f"pkga-{uuid.uuid4().hex[:8]}")
        pkg_b = _make_package(db, f"pkgb-{uuid.uuid4().hex[:8]}")
        self._attach(db, cat_a, pkg_a)
        self._attach(db, cat_b, pkg_b)
        db.session.commit()
        _tag(app, pkg_a, [tag_a])
        _tag(app, pkg_b, [tag_b])

        in_a = self._tag_slugs(
            client.get(f"/api/v1/ghrm/tags?category_slug={cat_a.slug}")
        )
        in_b = self._tag_slugs(
            client.get(f"/api/v1/ghrm/tags?category_slug={cat_b.slug}")
        )

        assert tag_a in in_a
        assert tag_a not in in_b
        assert tag_b in in_b
        assert tag_b not in in_a

    def test_no_category_returns_union(self, app, db, client):
        cat_a = self._category(db, f"cata-{uuid.uuid4().hex[:8]}")
        tag_in_cat = f"incat-{uuid.uuid4().hex[:8]}"
        tag_no_cat = f"nocat-{uuid.uuid4().hex[:8]}"
        pkg_in = _make_package(db, f"pkgin-{uuid.uuid4().hex[:8]}")
        pkg_out = _make_package(db, f"pkgout-{uuid.uuid4().hex[:8]}")
        self._attach(db, cat_a, pkg_in)
        db.session.commit()
        _tag(app, pkg_in, [tag_in_cat])
        _tag(app, pkg_out, [tag_no_cat])

        slugs = self._tag_slugs(client.get("/api/v1/ghrm/tags"))

        assert tag_in_cat in slugs
        assert tag_no_cat in slugs

    def test_unknown_category_returns_empty(self, app, db, client):
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, package, [f"vue-{uuid.uuid4().hex[:8]}"])

        response = client.get(
            f"/api/v1/ghrm/tags?category_slug=nope-{uuid.uuid4().hex[:8]}"
        )

        assert response.status_code == 200
        assert response.get_json()["tags"] == []

    def test_empty_category_param_behaves_as_unscoped(self, app, db, client):
        tag = f"vue-{uuid.uuid4().hex[:8]}"
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, package, [tag])

        slugs = self._tag_slugs(client.get("/api/v1/ghrm/tags?category_slug="))

        assert tag in slugs

    def test_category_scoped_uses_one_bulk_call(self, app, db, client, monkeypatch):
        category = self._category(db, f"cat-{uuid.uuid4().hex[:8]}")
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        self._attach(db, category, package)
        db.session.commit()
        _tag(app, package, [f"vue-{uuid.uuid4().hex[:8]}"])

        real_provider = repo_module.resolve_tags_and_custom_fields()
        spy = MagicMock(wraps=real_provider)
        monkeypatch.setattr(repo_module, "resolve_tags_and_custom_fields", lambda: spy)

        response = client.get(f"/api/v1/ghrm/tags?category_slug={category.slug}")

        assert response.status_code == 200
        assert spy.get_tags_bulk.call_count == 1


def _seed_default_currency(db) -> None:
    """Re-seed the EUR catalog row the core ``PriceFactory`` needs.

    Plugin integration tests truncate the shared catalog between tests, so the
    baseline currency row is created here through the model (never raw SQL),
    mirroring the subscription plugin's conftest.
    """
    from decimal import Decimal

    from vbwd.models.currency import Currency

    if not db.session.query(Currency).filter_by(code="EUR").first():
        db.session.add(
            Currency(
                code="EUR",
                name="Euro",
                symbol="€",
                exchange_rate=Decimal("1.0"),
                decimal_places=2,
            )
        )
        db.session.commit()


class TestListItemPrice:
    """Each catalogue item carries its linked plan's price block (S-price)."""

    def test_each_item_carries_price_block(self, app, db, client):
        _seed_default_currency(db)
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")  # plan price 9.0
        db.session.commit()

        items = client.get("/api/v1/ghrm/packages?per_page=100").get_json()["items"]
        match = next(item for item in items if item["slug"] == package.slug)

        assert match["price"] is not None
        assert match["price"]["price"]["currency"]  # resolved (e.g. EUR)
        assert float(match["price"]["gross_amount"]) == 9.0
        assert match["price"]["billing_period"] == "MONTHLY"

    def test_zero_priced_plan_yields_free_block(self, app, db, client):
        _seed_default_currency(db)
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.get(TarifPlan, package.tariff_plan_id).price = None  # -> raw 0.0
        db.session.commit()

        items = client.get("/api/v1/ghrm/packages?per_page=100").get_json()["items"]
        match = next(item for item in items if item["slug"] == package.slug)

        # an unpriced plan resolves to a valid 0.00 block (rendered as "Free"),
        # not a missing price
        assert match["price"] is not None
        assert float(match["price"]["gross_amount"]) == 0.0

    def test_prices_use_one_bulk_call(self, app, db, client, monkeypatch):
        _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()

        from plugins.subscription.subscription.services.catalog_read_model import (
            CatalogReadModel,
        )

        calls = {"count": 0}
        real = CatalogReadModel.plan_prices_by_ids

        def _spy(self, plan_ids):
            calls["count"] += 1
            return real(self, plan_ids)

        monkeypatch.setattr(CatalogReadModel, "plan_prices_by_ids", _spy)

        response = client.get("/api/v1/ghrm/packages?per_page=100")

        assert response.status_code == 200
        assert calls["count"] == 1  # one bulk call for the whole page, no N+1
