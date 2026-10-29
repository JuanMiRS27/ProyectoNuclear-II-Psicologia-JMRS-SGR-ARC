"""Dependencias del módulo 2. La autenticación y los roles provienen del módulo 1.

Política de acceso:
- Lectura (consultar, validar, exportar DSL): `DocenteUser` + autoría del caso.
- Escritura (crear, editar, eliminar, publicar, archivar): `CaseCreator`
  (docente activo con `puede_crear_casos`) + autoría del caso.
- Administradores y estudiantes reciben 403: el rol de administrador no otorga
  permisos de autor.
"""

from typing import Annotated

from fastapi import Depends

from app.modules.auth.dependencies import CaseCreator, DocenteUser
from app.modules.auth.dependencies.auth import DbSession
from app.modules.cases.services import CasoService, EscenarioService, OpcionService, PreguntaService, RubricaService


def get_caso_service(db: DbSession) -> CasoService:
    return CasoService(db)


def get_escenario_service(db: DbSession) -> EscenarioService:
    return EscenarioService(db)


def get_pregunta_service(db: DbSession) -> PreguntaService:
    return PreguntaService(db)


def get_opcion_service(db: DbSession) -> OpcionService:
    return OpcionService(db)


def get_rubrica_service(db: DbSession) -> RubricaService:
    return RubricaService(db)


CasoServiceDep = Annotated[CasoService, Depends(get_caso_service)]
EscenarioServiceDep = Annotated[EscenarioService, Depends(get_escenario_service)]
PreguntaServiceDep = Annotated[PreguntaService, Depends(get_pregunta_service)]
OpcionServiceDep = Annotated[OpcionService, Depends(get_opcion_service)]
RubricaServiceDep = Annotated[RubricaService, Depends(get_rubrica_service)]

Lector = DocenteUser
Editor = CaseCreator

__all__ = [
    "CasoServiceDep",
    "Editor",
    "EscenarioServiceDep",
    "Lector",
    "OpcionServiceDep",
    "PreguntaServiceDep",
    "RubricaServiceDep",
]
