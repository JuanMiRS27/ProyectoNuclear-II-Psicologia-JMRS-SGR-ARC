"""Primitivas de seguridad: hash de contraseñas con Argon2 y tokens JWT.

Este módulo no conoce el modelo de usuario ni la base de datos; solo ofrece
funciones reutilizables por los servicios de cualquier módulo.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

_password_hasher = PasswordHasher()

# Hash de una cadena aleatoria. Se verifica contra él cuando el usuario no existe
# o no tiene contraseña, para que el tiempo de respuesta no revele qué correos existen.
_DUMMY_HASH = _password_hasher.hash(uuid.uuid4().hex)


class InvalidTokenError(Exception):
    """El token está mal formado, manipulado, expirado o le faltan claims."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: uuid.UUID
    token_version: int


def hash_password(plain_password: str) -> str:
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str | None) -> bool:
    try:
        valid = _password_hasher.verify(password_hash or _DUMMY_HASH, plain_password)
    except (VerificationError, InvalidHashError):
        return False
    return valid and password_hash is not None


def password_needs_rehash(password_hash: str) -> bool:
    return _password_hasher.check_needs_rehash(password_hash)


def create_access_token(
    subject: uuid.UUID, role: str, token_version: int, expires_delta: timedelta | None = None
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    payload: dict[str, Any] = {
        "sub": str(subject),
        "rol": role,
        "ver": token_version,
        "type": "access",
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> AccessTokenClaims:
    """Valida firma, algoritmo, expiración y claims.

    El claim `rol` es solo informativo: la autorización siempre se basa en el
    estado actual del usuario en la base de datos, incluida su `version_token`.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat", "type", "ver"]},
        )
        version = payload["ver"]
        # bool es subclase de int: se excluye explícitamente.
        if payload["type"] != "access" or not isinstance(version, int) or isinstance(version, bool):
            raise InvalidTokenError()
        return AccessTokenClaims(user_id=uuid.UUID(payload["sub"]), token_version=version)
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidTokenError() from exc
