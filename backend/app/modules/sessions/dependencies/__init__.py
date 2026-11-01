"""Dependencias del módulo 3.

- Espacios académicos: escritura `AdminUser`; lectura administrador o docente.
- Sesiones y convocatorias: `DocenteUser` + ser el docente responsable (404 si no).
- Acceso estudiantil: token de tipo "student" (no sirve en ningún otro endpoint).
"""

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.clock import Reloj, get_reloj
from app.core.exceptions import AuthenticationError
from app.modules.auth.dependencies import require_roles
from app.modules.auth.dependencies.auth import DbSession
from app.modules.auth.models import RolUsuario, Usuario
from app.modules.sessions import integracion
from app.modules.sessions.security import LimitadorIntentos, get_limitador
from app.modules.sessions.services import (
    AccesoEstudiantil,
    AccesoEstudiantilService,
    PoliticaReingreso,
    ConvocatoriaService,
    EspacioService,
    SesionService,
)

RelojDep = Annotated[Reloj, Depends(get_reloj)]
LimitadorDep = Annotated[LimitadorIntentos, Depends(get_limitador)]

AdminODocente = Annotated[Usuario, Depends(require_roles(RolUsuario.ADMINISTRADOR, RolUsuario.DOCENTE))]

_bearer_estudiante = HTTPBearer(auto_error=False, description="Token estudiantil obtenido en /api/v1/auth/student/login")


def get_espacio_service(db: DbSession, reloj: RelojDep) -> EspacioService:
    return EspacioService(db, reloj)


def get_sesion_service(db: DbSession, reloj: RelojDep) -> SesionService:
    return SesionService(db, reloj)


def get_convocatoria_service(db: DbSession, reloj: RelojDep) -> ConvocatoriaService:
    return ConvocatoriaService(db, reloj)


def get_politica_reingreso(db: DbSession) -> PoliticaReingreso:
    """Política configurada en `integracion` (el módulo 4 registra la suya; por defecto, `SinReingreso`)."""
    return integracion.politica_reingreso(db)


def get_acceso_service(
    db: DbSession, reloj: RelojDep, reingreso: Annotated[PoliticaReingreso, Depends(get_politica_reingreso)]
) -> AccesoEstudiantilService:
    return AccesoEstudiantilService(db, reloj, reingreso=reingreso)


EspacioServiceDep = Annotated[EspacioService, Depends(get_espacio_service)]
SesionServiceDep = Annotated[SesionService, Depends(get_sesion_service)]
ConvocatoriaServiceDep = Annotated[ConvocatoriaService, Depends(get_convocatoria_service)]
AccesoServiceDep = Annotated[AccesoEstudiantilService, Depends(get_acceso_service)]


def acceso_estudiantil_a_sesion(
    session_id: uuid.UUID,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_estudiante)],
    service: AccesoServiceDep,
) -> AccesoEstudiantil:
    """Exige un token estudiantil vigente y emitido para la sesión de la ruta."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Se requiere autenticación estudiantil.")
    return service.resolver(credentials.credentials, sesion_id=session_id)


AccesoEstudiantilDep = Annotated[AccesoEstudiantil, Depends(acceso_estudiantil_a_sesion)]
