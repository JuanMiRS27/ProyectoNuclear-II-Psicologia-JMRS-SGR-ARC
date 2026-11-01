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


def encode_token(claims: dict[str, Any], token_type: str, expires_delta: timedelta) -> str:
    """Firma un JWT con la clave y el algoritmo de la configuración.

    `type` distingue el propósito del token: cada decodificador acepta solo su tipo,
    de modo que un token de un propósito no sirve para otro.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {**claims, "type": token_type, "iat": now, "exp": now + expires_delta}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, token_type: str, required_claims: list[str]) -> dict[str, Any]:
    """Valida firma, algoritmo, expiración, claims obligatorios y tipo. Lanza `InvalidTokenError`."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat", "type", *required_claims]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc
    if payload["type"] != token_type:
        raise InvalidTokenError()
    return payload


def entero_estricto(valor: Any) -> int:
    """Claim entero (bool es subclase de int y se rechaza explícitamente)."""
    if not isinstance(valor, int) or isinstance(valor, bool):
        raise InvalidTokenError()
    return valor


def create_access_token(
    subject: uuid.UUID, role: str, token_version: int, expires_delta: timedelta | None = None
) -> str:
    expira = expires_delta or timedelta(minutes=get_settings().ACCESS_TOKEN_EXPIRE_MINUTES)
    return encode_token({"sub": str(subject), "rol": role, "ver": token_version}, "access", expira)


def decode_access_token(token: str) -> AccessTokenClaims:
    """Valida firma, algoritmo, expiración y claims.

    El claim `rol` es solo informativo: la autorización siempre se basa en el
    estado actual del usuario en la base de datos, incluida su `version_token`.
    """
    payload = decode_token(token, "access", ["ver"])
    try:
        return AccessTokenClaims(user_id=uuid.UUID(payload["sub"]), token_version=entero_estricto(payload["ver"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidTokenError() from exc
