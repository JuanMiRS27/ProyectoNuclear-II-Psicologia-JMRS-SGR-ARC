"""Pruebas de las salvaguardas sobre TEST_DATABASE_URL."""

import pytest
from sqlalchemy import text

from app.core.database import engine
from tests.db_guard import (
    TEST_DATABASE_MARKER,
    UnsafeTestDatabaseError,
    ensure_disposable_database,
    validate_test_database_url,
)

APP = "postgresql+psycopg://u:p@localhost:5432/simulador_psicologia"


@pytest.mark.parametrize(
    "test_url",
    [
        "postgresql+psycopg://u:p@localhost:5432/simulador_psicologia_test",
        "postgresql+psycopg://u:p@127.0.0.1:5433/test_simulador",
        "postgresql://u:p@[::1]/otro_test",
    ],
)
def test_valid_test_urls_are_accepted(test_url):
    validate_test_database_url(test_url, APP, "development")


@pytest.mark.parametrize(
    ("test_url", "app_url", "environment", "fragment"),
    [
        (None, APP, "development", "Defina TEST_DATABASE_URL"),
        ("esto no es una url", APP, "development", "URL de conexión válida"),
        ("sqlite:///simulador_test.db", APP, "development", "PostgreSQL"),
        ("postgresql://u:p@localhost/simulador_psicologia", None, "development", "test_"),
        ("postgresql://u:p@localhost/contest_db", None, "development", "test_"),
        ("postgresql://u:p@localhost/simulador_test", None, "production", "production"),
        ("postgresql://u:p@db.produccion.edu/simulador_test", None, "development", "localhost"),
        # Misma base que la aplicación, aunque se escriba con otro alias de host o sin puerto.
        (
            "postgresql+psycopg://otro:x@127.0.0.1/app_test",
            "postgresql+psycopg://u:p@localhost:5432/app_test",
            "development",
            "misma base",
        ),
    ],
)
def test_unsafe_test_urls_are_rejected(test_url, app_url, environment, fragment):
    with pytest.raises(UnsafeTestDatabaseError, match=fragment):
        validate_test_database_url(test_url, app_url, environment)


def test_remote_host_requires_explicit_opt_in():
    url = "postgresql://u:p@ci-postgres/simulador_test"
    with pytest.raises(UnsafeTestDatabaseError):
        validate_test_database_url(url, APP, "testing")
    validate_test_database_url(url, APP, "testing", allow_remote=True)


def _current_database() -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT current_database()")).scalar_one()


def _set_comment(value: str | None) -> None:
    name = engine.dialect.identifier_preparer.quote(_current_database())
    literal = "NULL" if value is None else f"'{value}'"
    with engine.begin() as conn:
        conn.execute(text(f"COMMENT ON DATABASE {name} IS {literal}"))


def test_marked_test_database_is_accepted():
    with engine.begin() as conn:
        ensure_disposable_database(conn, _current_database())


def test_unmarked_database_with_tables_is_rejected():
    """Simula una base de desarrollo: tiene tablas (las migradas) y no tiene la marca."""
    _set_comment(None)
    try:
        with engine.begin() as conn, pytest.raises(UnsafeTestDatabaseError, match="no tiene la marca"):
            ensure_disposable_database(conn, _current_database())
    finally:
        _set_comment(TEST_DATABASE_MARKER)


def test_connection_to_unexpected_database_is_rejected():
    with engine.begin() as conn, pytest.raises(UnsafeTestDatabaseError, match="en lugar de"):
        ensure_disposable_database(conn, "otra_base_test")
