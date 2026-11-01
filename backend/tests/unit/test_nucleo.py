"""Infraestructura compartida por los cuatro módulos: configuración, reloj, excepciones y base de datos."""

import asyncio
import json
import logging
from datetime import UTC, timedelta
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import DataError

from app.core import clock, database, exceptions
from app.core.config import Settings
from app.modules.sessions.security.limitador import DemasiadosIntentosError


def _settings(**cambios) -> Settings:
    datos = {
        "DATABASE_URL": "postgresql://x",
        "JWT_SECRET_KEY": "k" * 32,
        "CONVOCATION_CODE_ENCRYPTION_KEY": "A" * 43 + "=",
    } | cambios
    return Settings(_env_file=None, **datos)


@pytest.mark.parametrize(
    "cambios",
    [
        {"JWT_SECRET_KEY": "k" * 31},
        {"JWT_ALGORITHM": "none"},
        {"JWT_ALGORITHM": "RS256"},
        {"ACCESS_TOKEN_EXPIRE_MINUTES": 0},
        {"ACCESS_TOKEN_EXPIRE_MINUTES": 60 * 24 + 1},
        {"ENVIRONMENT": "staging"},
        {"SESSION_AUTO_CLOSE_HOURS": -1},
        {"STUDENT_LOGIN_WINDOW_SECONDS": 0},
        {"STUDENT_TOKEN_EXPIRE_MINUTES": 0},
    ],
    ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()),
)
def test_configuracion_insegura_impide_arrancar(cambios):
    with pytest.raises(ValidationError):
        _settings(**cambios)


@pytest.mark.parametrize(("entorno", "docs"), [("development", True), ("testing", True), ("production", False)])
def test_documentacion_deshabilitada_en_produccion(entorno, docs):
    assert _settings(ENVIRONMENT=entorno).docs_enabled is docs


def test_reloj_del_sistema_siempre_en_utc():
    assert clock.RelojSistema().ahora().utcoffset() == timedelta(0)
    assert clock.RelojSistema().ahora().tzinfo is UTC
    assert isinstance(clock.get_reloj(), clock.RelojSistema)


@pytest.mark.parametrize(
    ("tipo", "codigo"),
    [
        (exceptions.BusinessRuleError, 400),
        (exceptions.AuthenticationError, 401),
        (exceptions.PermissionDeniedError, 403),
        (exceptions.NotFoundError, 404),
        (exceptions.ConflictError, 409),
        (exceptions.UnprocessableEntityError, 422),
    ],
)
def test_jerarquia_de_errores_y_mensaje_por_defecto(tipo, codigo):
    error = tipo()

    assert isinstance(error, exceptions.AppError)
    assert error.status_code == codigo
    assert error.detail == tipo.default_detail
    assert str(error) == tipo.default_detail


def _responder(error: exceptions.AppError):
    return asyncio.run(exceptions._app_error_handler(MagicMock(), error))


def test_manejador_incluye_detalle_y_campos_extra():
    respuesta = _responder(exceptions.UnprocessableEntityError("Inválido", extra={"valido": False}))

    assert respuesta.status_code == 422
    assert json.loads(respuesta.body) == {"detail": "Inválido", "valido": False}


def test_manejador_anade_www_authenticate_en_401():
    assert _responder(exceptions.AuthenticationError()).headers["WWW-Authenticate"] == "Bearer"
    assert "WWW-Authenticate" not in _responder(exceptions.NotFoundError()).headers


def test_manejador_respeta_cabeceras_propias_del_error():
    assert _responder(DemasiadosIntentosError(12)).headers["Retry-After"] == "12"


def test_error_de_datos_responde_422_sin_revelar_la_consulta(caplog):
    error = DataError("INSERT INTO usuario VALUES (%(clave)s)", {"clave": "secreto"}, ValueError("NUL"))

    with caplog.at_level(logging.WARNING, logger="iter_animi.errores"):
        respuesta = asyncio.run(exceptions._data_error_handler(MagicMock(), error))

    assert respuesta.status_code == 422
    assert list(json.loads(respuesta.body)) == ["detail"]
    assert b"secreto" not in respuesta.body and b"INSERT" not in respuesta.body
    assert "ValueError" in caplog.text and "secreto" not in caplog.text


def test_sesion_de_peticion_siempre_se_cierra(monkeypatch):
    sesion = MagicMock()
    monkeypatch.setattr(database, "SessionLocal", lambda: sesion)

    generador = database.get_db()
    assert next(generador) is sesion
    generador.close()

    sesion.close.assert_called_once()


def test_sesiones_no_expiran_al_confirmar():
    # Los servicios devuelven entidades tras el commit: dependen de esta configuración.
    assert database.SessionLocal.kw["expire_on_commit"] is False
    assert database.SessionLocal.kw["autoflush"] is False


def test_convencion_de_nombres_estable_para_migraciones():
    assert database.NAMING_CONVENTION["uq"] == "uq_%(table_name)s_%(column_0_name)s"
    assert database.Base.metadata.naming_convention["ck"] == "ck_%(table_name)s_%(constraint_name)s"
