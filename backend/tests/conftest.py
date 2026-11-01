"""Configuración de pruebas.

Las pruebas usan una base PostgreSQL EXCLUSIVA (TEST_DATABASE_URL), cuyo esquema
se crea ejecutando las migraciones de Alembic. Nunca se usa SQLite.
"""

import base64
import os
import secrets
from datetime import UTC, datetime, timedelta
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
os.environ.setdefault("CONVOCATION_CODE_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import security  # noqa: E402
from app.core.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.auth.models import RolUsuario, Usuario  # noqa: E402
from app.modules.sessions.security import get_limitador  # noqa: E402

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
        # CASCADE vacía también las tablas dependientes (casos, sesiones y convocatorias).
        conn.execute(text("TRUNCATE TABLE usuario, espacio_academico CASCADE"))
    # El limitador de intentos estudiantiles vive en memoria: se vacía entre pruebas.
    get_limitador().reiniciar()


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


# --- Módulo 2 -----------------------------------------------------------------


@pytest.fixture
def creador(make_user: Callable[..., Usuario]) -> Usuario:
    """Docente con permiso para crear casos."""
    return make_user(RolUsuario.DOCENTE, puede_crear_casos=True)


@pytest.fixture
def creador_headers(client: TestClient, creador: Usuario) -> dict[str, str]:
    return bearer(login(client, creador.correo))


@pytest.fixture
def otro_creador_headers(client: TestClient, make_user: Callable[..., Usuario]) -> dict[str, str]:
    otro = make_user(RolUsuario.DOCENTE, puede_crear_casos=True)
    return bearer(login(client, otro.correo))


@pytest.fixture
def estudiante_headers(make_user: Callable[..., Usuario]) -> dict[str, str]:
    # Los estudiantes aún no pueden iniciar sesión (módulo 3): se emite un token válido directamente.
    estudiante = make_user(RolUsuario.ESTUDIANTE, clave=None)
    return bearer(security.create_access_token(estudiante.id, estudiante.rol.value, estudiante.version_token))


# --- Módulo 3 -----------------------------------------------------------------


class RelojFijo:
    """Reloj controlable para las reglas temporales (inicio, límite de ingreso, cierre automático)."""

    def __init__(self, actual: datetime) -> None:
        self.actual = actual

    def ahora(self) -> datetime:
        return self.actual

    def avanzar(self, **delta: float) -> None:
        self.actual += timedelta(**delta)


@pytest.fixture
def reloj() -> Iterator[RelojFijo]:
    """Sustituye el reloj de la aplicación. Por defecto: 3 de noviembre de 2026, 07:00 UTC."""
    from app.core.clock import get_reloj

    fijo = RelojFijo(datetime(2026, 11, 3, 7, 0, tzinfo=UTC))
    app.dependency_overrides[get_reloj] = lambda: fijo
    yield fijo
    app.dependency_overrides.pop(get_reloj, None)


@pytest.fixture
def espacio(db: Session):
    from app.modules.sessions.models import EspacioAcademico

    nuevo = EspacioAcademico(nombre="Psicología Social I", periodo="2026-2", activo=True)
    db.add(nuevo)
    db.commit()
    return nuevo


@pytest.fixture
def caso_publicado(client: TestClient, creador_headers: dict[str, str]) -> dict:
    """Caso del docente `creador`, publicado (tiempo estimado: 45 min)."""
    from tests.cases_helpers import crear_caso_completo, publicar

    caso = crear_caso_completo(client, creador_headers)
    response = publicar(client, creador_headers, caso["id"])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def estudiantes(make_user: Callable[..., Usuario]) -> list[Usuario]:
    return [make_user(RolUsuario.ESTUDIANTE, clave=None) for _ in range(3)]
