import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.database import OFFSET_MAXIMO
from app.modules.auth.dependencies import AdminUser
from app.modules.auth.schemas import MessageResponse
from app.modules.sessions.dependencies import AdminODocente, EspacioServiceDep
from app.modules.sessions.schemas import EspacioCreate, EspacioList, EspacioRead, EspacioStatusUpdate, EspacioUpdate

router = APIRouter(prefix="/academic-spaces", tags=["Espacios académicos"])

_ERRORES = {
    401: {"model": MessageResponse, "description": "No autenticado."},
    403: {"model": MessageResponse, "description": "Rol insuficiente."},
    404: {"model": MessageResponse, "description": "Espacio inexistente (o inactivo, para docentes)."},
}
_CONFLICTO = {409: {"model": MessageResponse, "description": "Ya existe un espacio con ese nombre y periodo."}}


@router.post(
    "", response_model=EspacioRead, status_code=status.HTTP_201_CREATED, summary="Crear espacio académico (administrador)",
    responses={**_ERRORES, **_CONFLICTO},
)
def create_space(data: EspacioCreate, _: AdminUser, service: EspacioServiceDep) -> EspacioRead:
    return EspacioRead.model_validate(service.crear(data))


@router.get(
    "", response_model=EspacioList, summary="Listar espacios (los docentes solo ven los activos)", responses=_ERRORES
)
def list_spaces(
    usuario: AdminODocente,
    service: EspacioServiceDep,
    activo: bool | None = None,
    skip: Annotated[int, Query(ge=0, le=OFFSET_MAXIMO)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> EspacioList:
    espacios, total = service.listar(usuario, activo=activo, skip=skip, limit=limit)
    return EspacioList(items=[EspacioRead.model_validate(e) for e in espacios], total=total, skip=skip, limit=limit)


@router.get("/{space_id}", response_model=EspacioRead, summary="Consultar espacio", responses=_ERRORES)
def get_space(space_id: uuid.UUID, usuario: AdminODocente, service: EspacioServiceDep) -> EspacioRead:
    return EspacioRead.model_validate(service.obtener(usuario, space_id))


@router.patch(
    "/{space_id}", response_model=EspacioRead, summary="Actualizar espacio (administrador)", responses={**_ERRORES, **_CONFLICTO}
)
def update_space(space_id: uuid.UUID, data: EspacioUpdate, _: AdminUser, service: EspacioServiceDep) -> EspacioRead:
    return EspacioRead.model_validate(service.actualizar(space_id, data))


@router.patch("/{space_id}/status", response_model=EspacioRead, summary="Activar o desactivar (administrador)", responses=_ERRORES)
def update_space_status(
    space_id: uuid.UUID, data: EspacioStatusUpdate, _: AdminUser, service: EspacioServiceDep
) -> EspacioRead:
    return EspacioRead.model_validate(service.cambiar_estado(space_id, data.activo))
