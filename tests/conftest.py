"""Configuración de pruebas.

Las pruebas usan una base PostgreSQL EXCLUSIVA (TEST_DATABASE_URL), cuyo esquema
se crea ejecutando las migraciones de Alembic. Nunca se usa SQLite.
"""

import os
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from tests.db_guard import UnsafeTestDatabaseError, ensure_disposable_database, validate_test_database_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _TestEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    TEST_DATABASE_URL: str | None = None
    DATABASE_URL: str | None = None
    ENVIRONMENT: str | None = None
    ALLOW_REMOTE_TEST_DATABASE: bool = False


# Se leen los valores ORIGINALES (antes de redirigir DATABASE_URL) para poder compararlos.
_env = _TestEnv()
try:
    _test_url = validate_test_database_url(
        _env.TEST_DATABASE_URL, _env.DATABASE_URL, _env.ENVIRONMENT, _env.ALLOW_REMOTE_TEST_DATABASE
    )
except UnsafeTestDatabaseError as exc:
    raise pytest.UsageError(f"Base de datos de pruebas rechazada: {exc}") from exc
_test_db_url: str = _env.TEST_DATABASE_URL  # type: ignore[assignment]

# Debe ocurrir antes de importar la aplicación: el motor se crea al importar app.core.database.
os.environ["DATABASE_URL"] = _test_db_url
os.environ["ENVIRONMENT"] = "testing"
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import security  # noqa: E402
from app.core.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.auth.models import RolUsuario, Usuario  # noqa: E402

DEFAULT_PASSWORD = "ClaveSegura#2026"


def alembic_config() -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", _test_db_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    # Segunda barrera, ya conectados: nunca se ejecuta downgrade sobre una base no marcada con datos.
    try:
        with engine.begin() as conn:
            ensure_disposable_database(conn, _test_url.database or "")
    except UnsafeTestDatabaseError as exc:
        pytest.exit(f"Base de datos de pruebas rechazada: {exc}", returncode=4)

    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield
    engine.dispose()
    command.downgrade(config, "base")


@pytest.fixture(autouse=True)
def clean_tables() -> Iterator[None]:
    yield
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE usuario"))


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user(db: Session) -> Callable[..., Usuario]:
    counter = iter(range(1, 10_000))

    def _make_user(
        rol: RolUsuario = RolUsuario.DOCENTE,
        *,
        correo: str | None = None,
        clave: str | None = DEFAULT_PASSWORD,
        activo: bool = True,
        puede_crear_casos: bool = False,
    ) -> Usuario:
        n = next(counter)
        usuario = Usuario(
            nombre_completo=f"Usuario {rol.value.title()} {n}",
            correo=correo or f"{rol.value.lower()}{n}@universidad.edu.co",
            clave_hash=security.hash_password(clave) if clave else None,
            rol=rol,
            activo=activo,
            puede_crear_casos=puede_crear_casos,
        )
        db.add(usuario)
        db.commit()
        return usuario

    return _make_user


@pytest.fixture
def admin(make_user: Callable[..., Usuario]) -> Usuario:
    return make_user(RolUsuario.ADMINISTRADOR)


@pytest.fixture
def docente(make_user: Callable[..., Usuario]) -> Usuario:
    return make_user(RolUsuario.DOCENTE)


def login(client: TestClient, correo: str, clave: str = DEFAULT_PASSWORD) -> str:
    response = client.post("/api/v1/auth/login", json={"correo": correo, "clave": clave})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(client: TestClient, admin: Usuario) -> dict[str, str]:
    return bearer(login(client, admin.correo))


@pytest.fixture
def docente_headers(client: TestClient, docente: Usuario) -> dict[str, str]:
    return bearer(login(client, docente.correo))
