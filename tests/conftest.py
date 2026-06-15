"""Test fixtures for GHRM plugin tests."""
import pytest
import os
import sys

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
)

os.environ["FLASK_ENV"] = "testing"
os.environ["TESTING"] = "true"


def _test_db_url() -> str:
    base = os.getenv("DATABASE_URL", "postgresql://vbwd:vbwd@postgres:5432/vbwd")
    prefix, _, dbname = base.rpartition("/")
    dbname = dbname.split("?")[0]
    return f"{prefix}/{dbname}_test"


def _ensure_test_db(url: str) -> None:
    from sqlalchemy import create_engine, text

    main_url = url.rsplit("/", 1)[0] + "/postgres"
    dbname = url.rsplit("/", 1)[1].split("?")[0]
    engine = create_engine(main_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": dbname}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()


def _ensure_ghrm_enabled(flask_app) -> None:
    """Enable ghrm (+ its ``subscription`` dep) so ``on_enable`` runs and
    registers the ghrm package entity-type / repos.

    A fresh per-plugin CI clone has no ``plugins.json``, so ghrm is otherwise
    discovered-but-not-enabled and the entity-type / tags tests see nothing.
    Idempotent — a no-op when already enabled (e.g. local dev state).
    """
    from vbwd.plugins.base import PluginStatus

    manager = getattr(flask_app, "plugin_manager", None)
    if manager is None:
        return
    with flask_app.app_context():
        for name in ("subscription", "ghrm"):  # dependency first
            plugin = manager.get_plugin(name)
            if plugin is None or plugin.status == PluginStatus.ENABLED:
                continue
            try:
                manager.enable_plugin(name)
            except ValueError:
                if plugin.status == PluginStatus.INITIALIZED:
                    plugin.enable()


@pytest.fixture(scope="session")
def app():
    from vbwd.app import create_app

    url = _test_db_url()
    _ensure_test_db(url)
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": url,
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "RATELIMIT_ENABLED": True,
        "RATELIMIT_STORAGE_URL": "memory://",
    }
    app = create_app(test_config)
    from vbwd.extensions import limiter

    limiter.reset()

    # Build the schema once per process (create_all, checkfirst — never drops,
    # so it cannot wipe data) and commit baseline reference rows once. Each test
    # then isolates itself via a rolled-back transaction (no TRUNCATE, no DROP) —
    # see vbwd/testing/integration_db.py. create_app() has already registered
    # every enabled plugin's models, so create_all() emits the full table set.
    with app.app_context():
        from vbwd.extensions import db as _db
        from vbwd.testing.integration_db import ensure_schema_and_baseline

        ensure_schema_and_baseline(_db)

    _ensure_ghrm_enabled(app)

    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    """Isolate each test in a rolled-back transaction (self-cleaning, no wipe).

    The schema + baseline reference rows are built once in the ``app`` fixture;
    each test runs inside a transaction that is rolled back at teardown, so
    nothing it writes persists. See vbwd/testing/integration_db.py.
    """
    from vbwd.extensions import db

    with app.app_context():
        from vbwd.testing.integration_db import rollback_isolation

        with rollback_isolation(db):
            yield db
