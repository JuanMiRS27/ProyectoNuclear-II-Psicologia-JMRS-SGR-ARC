"""Hash de claves (argon2) y tokens de acceso (JWT)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import obtener_configuracion

_hasher = PasswordHasher()


def hashear_clave(clave: str) -> str:
    return _hasher.hash(clave)


def verificar_clave(clave: str, clave_hash: str) -> bool:
    try:
        return _hasher.verify(clave_hash, clave)
    except (VerifyMismatchError, InvalidHashError):
        return False


def crear_token_acceso(usuario_id: uuid.UUID) -> str:
    config = obtener_configuracion()
    ahora = datetime.now(UTC)
    carga = {
        "sub": str(usuario_id),
        "iat": ahora,
        "exp": ahora + timedelta(minutes=config.jwt_minutos_expiracion),
    }
    return jwt.encode(carga, config.jwt_secreto, algorithm=config.jwt_algoritmo)


def leer_token_acceso(token: str) -> uuid.UUID | None:
    """Devuelve el id del usuario, o None si el token es inválido o expiró."""
    config = obtener_configuracion()
    try:
        carga = jwt.decode(token, config.jwt_secreto, algorithms=[config.jwt_algoritmo])
        return uuid.UUID(carga["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
