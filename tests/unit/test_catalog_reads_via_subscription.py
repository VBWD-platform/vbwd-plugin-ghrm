"""S50.1 — GHRM reads the plan catalog through subscription's CatalogReadModel.

Core's ``catalog_read_model`` port has been removed (S50: core is event-aware,
not domain-aware). GHRM declares ``dependencies=["subscription"]`` and now
consumes the subscription-owned ``CatalogReadModel`` directly. These tests
pin that GHRM's two catalog call sites — the ``/ghrm/categories`` route
(``category_labels_by_slugs``) and the repository category filter
(``plan_ids_in_category``) — go through that class.
"""
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from flask import Flask

from plugins.ghrm.src.routes import ghrm_bp


def _make_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(ghrm_bp)
    return app


@pytest.fixture
def app():
    return _make_app()


class TestCategoriesRouteUsesSubscriptionCatalog:
    def test_labels_resolved_via_subscription_catalog_read_model(self, app):
        fake_catalog = MagicMock()
        fake_catalog.category_labels_by_slugs.return_value = {"backend": "Backend"}
        with app.test_client() as client:
            with patch(
                "plugins.ghrm.src.routes._cfg",
                return_value={"software_category_slugs": ["backend", "fe-user"]},
            ):
                with patch(
                    "plugins.subscription.subscription.services."
                    "catalog_read_model.CatalogReadModel",
                    return_value=fake_catalog,
                ):
                    response = client.get("/api/v1/ghrm/categories")

        assert response.status_code == 200
        fake_catalog.category_labels_by_slugs.assert_called_once_with(
            ["backend", "fe-user"]
        )
        categories = {c["slug"]: c["label"] for c in response.get_json()["categories"]}
        # DB-resolved label for "backend", slug-derived title fallback for "fe-user".
        assert categories["backend"] == "Backend"
        assert categories["fe-user"] == "Fe User"


class TestRepositoryCategoryFilterUsesSubscriptionCatalog:
    def test_plan_ids_resolved_via_subscription_catalog_read_model(self):
        from plugins.ghrm.src.repositories.software_package_repository import (
            GhrmSoftwarePackageRepository,
        )

        plan_id = uuid4()
        fake_catalog = MagicMock()
        fake_catalog.plan_ids_in_category.return_value = [plan_id]

        # The query chain returns concrete values so find_all completes and we
        # can assert on the catalog interaction, not on incidental arithmetic.
        query = MagicMock()
        query.filter.return_value = query
        query.order_by.return_value = query
        query.offset.return_value = query
        query.limit.return_value = query
        query.count.return_value = 0
        query.all.return_value = []
        session = MagicMock()
        session.query.return_value = query
        repo = GhrmSoftwarePackageRepository(session=session)

        with patch(
            "plugins.subscription.subscription.services."
            "catalog_read_model.CatalogReadModel",
            return_value=fake_catalog,
        ):
            repo.find_all(category_slug="backend")

        fake_catalog.plan_ids_in_category.assert_called_once_with("backend")
