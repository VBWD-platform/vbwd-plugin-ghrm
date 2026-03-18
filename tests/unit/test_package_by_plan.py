"""Unit tests for find_by_tariff_plan_id (repo), get_by_tariff_plan_id (service), and the route."""
from unittest.mock import MagicMock, patch


# ─── Repository ──────────────────────────────────────────────────────────────


class TestFindByTariffPlanId:
    def _make_repo(self, query_result):
        """Build a GhrmSoftwarePackageRepository with a mocked db.session."""
        from plugins.ghrm.src.repositories.software_package_repository import (
            GhrmSoftwarePackageRepository,
        )

        repo = GhrmSoftwarePackageRepository(session=MagicMock())

        mock_query = MagicMock()
        mock_query.filter_by.return_value.first.return_value = query_result

        with patch(
            "plugins.ghrm.src.repositories.software_package_repository.db"
        ) as mock_db:
            mock_db.session.query.return_value = mock_query
            repo._mock_db = mock_db
            repo._mock_query = mock_query
            # Capture what is returned
            repo._last_result = query_result
        return repo, mock_db, mock_query

    def test_returns_none_for_invalid_uuid(self):
        from plugins.ghrm.src.repositories.software_package_repository import (
            GhrmSoftwarePackageRepository,
        )

        repo = GhrmSoftwarePackageRepository(session=MagicMock())
        result = repo.find_by_tariff_plan_id("not-a-uuid")
        assert result is None

    def test_returns_none_for_empty_string(self):
        from plugins.ghrm.src.repositories.software_package_repository import (
            GhrmSoftwarePackageRepository,
        )

        repo = GhrmSoftwarePackageRepository(session=MagicMock())
        result = repo.find_by_tariff_plan_id("")
        assert result is None

    def test_calls_db_session_query_for_valid_uuid(self):
        from uuid import UUID
        from plugins.ghrm.src.repositories.software_package_repository import (
            GhrmSoftwarePackageRepository,
        )
        from plugins.ghrm.src.models.ghrm_software_package import GhrmSoftwarePackage

        mock_pkg = MagicMock()
        mock_query = MagicMock()
        mock_query.filter_by.return_value.first.return_value = mock_pkg

        repo = GhrmSoftwarePackageRepository(session=MagicMock())
        valid_uuid = "12345678-1234-5678-1234-567812345678"

        with patch(
            "plugins.ghrm.src.repositories.software_package_repository.db"
        ) as mock_db:
            mock_db.session.query.return_value = mock_query
            result = repo.find_by_tariff_plan_id(valid_uuid)

        mock_db.session.query.assert_called_once_with(GhrmSoftwarePackage)
        mock_query.filter_by.assert_called_once_with(tariff_plan_id=UUID(valid_uuid))
        assert result is mock_pkg

    def test_returns_none_when_not_found(self):
        from plugins.ghrm.src.repositories.software_package_repository import (
            GhrmSoftwarePackageRepository,
        )

        mock_query = MagicMock()
        mock_query.filter_by.return_value.first.return_value = None

        repo = GhrmSoftwarePackageRepository(session=MagicMock())
        valid_uuid = "12345678-1234-5678-1234-567812345678"

        with patch(
            "plugins.ghrm.src.repositories.software_package_repository.db"
        ) as mock_db:
            mock_db.session.query.return_value = mock_query
            result = repo.find_by_tariff_plan_id(valid_uuid)

        assert result is None


# ─── Service ─────────────────────────────────────────────────────────────────


class TestGetByTariffPlanId:
    def _make_service(self, repo=None):
        from plugins.ghrm.src.services.software_package_service import (
            SoftwarePackageService,
        )

        return SoftwarePackageService(
            package_repo=repo or MagicMock(),
            sync_repo=MagicMock(),
            github=MagicMock(),
        )

    def test_delegates_to_repo(self):
        mock_pkg = MagicMock()
        repo = MagicMock()
        repo.find_by_tariff_plan_id.return_value = mock_pkg

        svc = self._make_service(repo)
        result = svc.get_by_tariff_plan_id("some-plan-id")

        repo.find_by_tariff_plan_id.assert_called_once_with("some-plan-id")
        assert result is mock_pkg

    def test_returns_none_when_not_found(self):
        repo = MagicMock()
        repo.find_by_tariff_plan_id.return_value = None

        svc = self._make_service(repo)
        result = svc.get_by_tariff_plan_id("missing-plan-id")

        assert result is None


# ─── Route ───────────────────────────────────────────────────────────────────


class TestGetPackageByPlanRoute:
    def _app(self):
        from vbwd.app import create_app

        return create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "RATELIMIT_ENABLED": False,
                "RATELIMIT_STORAGE_URL": "memory://",
            }
        )

    def test_returns_404_when_no_package(self):
        app = self._app()
        with app.test_client() as client:
            with patch("plugins.ghrm.src.routes._pkg_svc") as mock_svc_factory:
                mock_svc = MagicMock()
                mock_svc.get_by_tariff_plan_id.return_value = None
                mock_svc_factory.return_value = mock_svc

                resp = client.get(
                    "/api/v1/ghrm/packages/by-plan/12345678-1234-5678-1234-567812345678"
                )

        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_returns_200_with_package_dict(self):
        app = self._app()
        with app.test_client() as client:
            with patch("plugins.ghrm.src.routes._pkg_svc") as mock_svc_factory:
                mock_pkg = MagicMock()
                mock_pkg.to_dict.return_value = {
                    "id": "pkg-1",
                    "slug": "my-pkg",
                    "name": "My Package",
                }
                mock_svc = MagicMock()
                mock_svc.get_by_tariff_plan_id.return_value = mock_pkg
                mock_svc_factory.return_value = mock_svc

                resp = client.get(
                    "/api/v1/ghrm/packages/by-plan/12345678-1234-5678-1234-567812345678"
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["slug"] == "my-pkg"
        assert data["name"] == "My Package"
