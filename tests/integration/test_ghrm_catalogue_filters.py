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


def _make_package(db, slug: str, *, kind: str = "single") -> GhrmSoftwarePackage:
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
        is_active=True,
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
    def test_lists_applicable_tags(self, app, db, client):
        tag = f"vue-{uuid.uuid4().hex[:8]}"
        package = _make_package(db, f"pkg-{uuid.uuid4().hex[:8]}")
        db.session.commit()
        _tag(app, package, [tag])

        response = client.get("/api/v1/ghrm/tags")

        assert response.status_code == 200
        slugs = {row["slug"] for row in response.get_json()["tags"]}
        assert tag in slugs
