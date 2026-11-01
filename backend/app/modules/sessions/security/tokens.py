"""Token de acceso estudiantil, con alcance limitado a una convocatoria.

Reutiliza la infraestructura JWT del módulo 1 (`app.core.security`): misma clave,
algoritmo y validación de firma/expiración. Se distingue por `type = "student"`:
`decode_access_token` (endpoints administrativos y docentes) solo acepta
`type = "access"`, de modo que un token estudiantil nunca sirve allí, y viceversa.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from app.core import security

TIPO_TOKEN_ESTUDIANTIL = "student"


@dataclass(frozen=True, slots=True)
class ClaimsEstudiante:
    estudiante_id: uuid.UUID
    convocatoria_id: uuid.UUID
    sesion_id: uuid.UUID
    version_usuario: int
    huella_codigo: str


def crear_token_estudiante(
    *,
    estudiante_id: uuid.UUID,
    convocatoria_id: uuid.UUID,
    sesion_id: uuid.UUID,
    version_usuario: int,
    huella_codigo: str,
    duracion: timedelta,
) -> str:
    claims = {
        "sub": str(estudiante_id),
        "conv": str(convocatoria_id),
        "ses": str(sesion_id),
        "ver": version_usuario,
        "cod": huella_codigo,
    }
    return security.encode_token(claims, TIPO_TOKEN_ESTUDIANTIL, duracion)


def decodificar_token_estudiante(token: str) -> ClaimsEstudiante:
    payload = security.decode_token(token, TIPO_TOKEN_ESTUDIANTIL, ["conv", "ses", "ver", "cod"])
    try:
        return ClaimsEstudiante(
            estudiante_id=uuid.UUID(payload["sub"]),
            convocatoria_id=uuid.UUID(payload["conv"]),
            sesion_id=uuid.UUID(payload["ses"]),
            version_usuario=security.entero_estricto(payload["ver"]),
            huella_codigo=str(payload["cod"]),
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise security.InvalidTokenError() from exc
