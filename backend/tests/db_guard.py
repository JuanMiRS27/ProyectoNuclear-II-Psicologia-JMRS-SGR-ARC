"""Salvaguardas para no ejecutar migraciones destructivas fuera de la base de pruebas.

La suite ejecuta `alembic downgrade base`, que borra el esquema. Antes de hacerlo
se exigen dos niveles de comprobación:

1. Sobre la URL (sin conectarse): nombre de base de pruebas, host local, entorno
   distinto de producción y URL diferente de DATABASE_URL.
2. Sobre la base real (conectado): la base debe estar vacía o llevar la marca
   `COMMENT ON DATABASE ... IS '<TEST_DATABASE_MARKER>'` que la suite pone en
   la primera ejecución. Una base de desarrollo con tablas y sin la marca se rechaza.
"""

import re

from sqlalchemy import Connection, text
from sqlalchemy.engine import URL, make_url

TEST_DATABASE_MARKER = "simulador-psicologia:base-de-pruebas-desechable"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TEST_NAME_PATTERN = re.compile(r"(^test_|_test$)")


class UnsafeTestDatabaseError(Exception):
    pass


def _identity(url: URL) -> tuple[str, int, str]:
    host = (url.host or "localhost").lower()
    if host in LOCAL_HOSTS:
        host = "localhost"
    return host, url.port or 5432, (url.database or "").lower()


def validate_test_database_url(
    test_url: str | None, app_url: str | None, environment: str | None, allow_remote: bool = False
) -> URL:
    if not test_url:
        raise UnsafeTestDatabaseError("Defina TEST_DATABASE_URL (variable de entorno o .env) para ejecutar las pruebas.")
    try:
        url = make_url(test_url)
    except Exception as exc:
        raise UnsafeTestDatabaseError("TEST_DATABASE_URL no es una URL de conexión válida.") from exc

    if url.get_backend_name() != "postgresql":
        raise UnsafeTestDatabaseError("Las pruebas deben ejecutarse sobre PostgreSQL.")
    if (environment or "").lower() == "production":
        raise UnsafeTestDatabaseError("No se ejecutan pruebas con ENVIRONMENT=production.")
    if not _TEST_NAME_PATTERN.search((url.database or "").lower()):
        raise UnsafeTestDatabaseError(
            "El nombre de la base de pruebas debe empezar por 'test_' o terminar en '_test'."
        )
    if not allow_remote and (url.host or "localhost").lower() not in LOCAL_HOSTS:
        raise UnsafeTestDatabaseError(
            "La base de pruebas debe estar en localhost. Para un servidor dedicado remoto, "
            "defina ALLOW_REMOTE_TEST_DATABASE=true."
        )
    if app_url:
        try:
            same = _identity(make_url(app_url)) == _identity(url)
        except Exception:
            same = False
        if same:
            raise UnsafeTestDatabaseError("TEST_DATABASE_URL apunta a la misma base que DATABASE_URL.")
    return url


def ensure_disposable_database(conn: Connection, expected_database: str) -> None:
    """Verifica la base real y, si está vacía, la marca como base de pruebas."""
    current = conn.execute(text("SELECT current_database()")).scalar_one()
    if current != expected_database:
        raise UnsafeTestDatabaseError(f"Conectado a '{current}' en lugar de '{expected_database}'.")

    marker = conn.execute(
        text("SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = current_database()")
    ).scalar()
    if marker == TEST_DATABASE_MARKER:
        return

    # alembic_version puede quedar (vacía) tras un `downgrade base`; no indica datos.
    tables = conn.execute(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') AND table_name <> 'alembic_version'"
        )
    ).scalar_one()
    if tables:
        raise UnsafeTestDatabaseError(
            f"La base '{current}' contiene tablas y no tiene la marca de base de pruebas. "
            "Use una base PostgreSQL nueva y vacía, dedicada exclusivamente a las pruebas."
        )

    quoted = conn.dialect.identifier_preparer.quote(current)
    conn.execute(text(f"COMMENT ON DATABASE {quoted} IS '{TEST_DATABASE_MARKER}'"))
