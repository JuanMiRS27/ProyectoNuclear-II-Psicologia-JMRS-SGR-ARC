"""Dependencias del módulo 4. Toda la autenticación es la estudiantil del módulo 3 (sin duplicarla)."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import AuthenticationError
from app.modules.auth.dependencies.auth import DbSession
from app.modules.participations.services import ParticipacionService, RespuestaService
from app.modules.sessions.dependencies import AccesoServiceDep, RelojDep
from app.modules.sessions.services import AccesoEstudiantil

_bearer = HTTPBearer(auto_error=False, description="Token estudiantil obtenido en /api/v1/auth/student/login")


def acceso_estudiantil_actual(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)], service: AccesoServiceDep
) -> AccesoEstudiantil:
    """Token estudiantil vigente (mismas verificaciones del módulo 3), para rutas sin `session_id`.

    El alcance queda fijado por el token: la convocatoria y la sesión de las que se derivan
    todas las participaciones accesibles.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Se requiere autenticación estudiantil.")
    return service.resolver(credentials.credentials)


AccesoTokenDep = Annotated[AccesoEstudiantil, Depends(acceso_estudiantil_actual)]


def get_participacion_service(db: DbSession, reloj: RelojDep) -> ParticipacionService:
    return ParticipacionService(db, reloj)


def get_respuesta_service(db: DbSession, reloj: RelojDep) -> RespuestaService:
    return RespuestaService(db, reloj)


ParticipacionServiceDep = Annotated[ParticipacionService, Depends(get_participacion_service)]
RespuestaServiceDep = Annotated[RespuestaService, Depends(get_respuesta_service)]
