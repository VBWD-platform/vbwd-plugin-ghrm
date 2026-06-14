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

    # Build the full schema exactly ONCE for the whole session, resetting the
    # public schema first (clearing any table or ENUM type left by a prior
    # crashed run or a sibling suite sharing this ``*_test`` DB). A per-test
    # create_all()/drop_all() strands standalone PG ENUM types and races other
    # suites on the shared catalog — see vbwd/testing/integration_db.py.
    # create_app() has already registered every enabled plugin's models, so
    # create_all() emits the full table set. Each test isolates by TRUNCATE.
    with app.app_context():
        from vbwd.extensions import db as _db
        from vbwd.testing.integration_db import reset_schema_and_create_all

        reset_schema_and_create_all(_db)

    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    from vbwd.extensions import db

    with app.app_context():
        from vbwd.testing.integration_db import truncate_all_tables

        truncate_all_tables(db)
        yield db
        db.session.remove()
