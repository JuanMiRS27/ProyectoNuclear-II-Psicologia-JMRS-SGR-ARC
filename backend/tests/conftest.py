"""Fixtures comunes.

Por defecto las pruebas usan SQLite en memoria con las tablas que no dependen
de tipos exclusivos de PostgreSQL. Para probar contra PostgreSQL, definir
URL_BD_PRUEBAS (ej. postgresql+psycopg://simulador:simulador@db:5432/simulador_test).
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import obtener_sesion
from app.core.enums import RolUsuario
from app.core.seguridad import crear_token_acceso, hashear_clave
from app.main import app
from app.models import (
    Base,
    CasoEstudio,
    Convocatoria,
    EspacioAcademico,
    Notificacion,
    Participacion,
    Rubrica,
    Sesion,
    Usuario,
)

URL_PG = os.getenv("URL_BD_PRUEBAS")
CLAVE_PRUEBA = "ClaveSegura123"

# Tablas que SQLite puede crear (las demás usan JSONB o funciones de PostgreSQL)
TABLAS_SQLITE = [
    m.__table__
    for m in (
        Usuario,
        EspacioAcademico,
        CasoEstudio,
        Rubrica,
        Sesion,
        Convocatoria,
        Participacion,
        Notificacion,
    )
]


@pytest.fixture
def fabrica_sesion():
    if URL_PG:
        motor = create_engine(URL_PG)
        tablas = None  # todas
    else:
        motor = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        @event.listens_for(motor, "connect")
        def _funciones_postgres(conexion, _):
            conexion.create_function("char_length", 1, len)

        tablas = TABLAS_SQLITE

    Base.metadata.create_all(motor, tables=tablas)
    yield sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)
    Base.metadata.drop_all(motor, tables=tablas)
    motor.dispose()


@pytest.fixture
def sesion(fabrica_sesion):
    with fabrica_sesion() as s:
        yield s


@pytest.fixture
def cliente(sesion: Session):
    app.dependency_overrides[obtener_sesion] = lambda: sesion
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _crear(sesion: Session, **datos) -> Usuario:
    usuario = Usuario(clave_hash=hashear_clave(CLAVE_PRUEBA), **datos)
    sesion.add(usuario)
    sesion.commit()
    return usuario


@pytest.fixture
def admin(sesion: Session) -> Usuario:
    return _crear(
        sesion,
        nombre_completo="Admin Pruebas",
        correo="admin@prueba.edu.co",
        rol=RolUsuario.ADMINISTRADOR,
    )


@pytest.fixture
def docente(sesion: Session) -> Usuario:
    return _crear(
        sesion,
        nombre_completo="Docente Pruebas",
        correo="docente@prueba.edu.co",
        rol=RolUsuario.DOCENTE,
        puede_crear_casos=True,
    )


@pytest.fixture
def estudiante(sesion: Session) -> Usuario:
    return _crear(
        sesion,
        nombre_completo="Estudiante Pruebas",
        correo="estudiante@prueba.edu.co",
        rol=RolUsuario.ESTUDIANTE,
        codigo_institucional="EST-0099",
    )


def cabecera(usuario: Usuario) -> dict[str, str]:
    return {"Authorization": f"Bearer {crear_token_acceso(usuario.id)}"}
