import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.dependencies import AdminUser, get_usuario_service, require_admin
from app.modules.auth.models import RolUsuario
from app.modules.auth.schemas import (
    CasePermissionUpdate,
    DocenteCreate,
    MessageResponse,
    UsuarioList,
    UsuarioRead,
    UsuarioStatusUpdate,
    UsuarioUpdate,
)
from app.modules.auth.services import UsuarioService

# Todas las rutas de este router exigen un administrador autenticado y activo.
router = APIRouter(
    prefix="/users",
    tags=["Gestión de usuarios"],
    dependencies=[Depends(require_admin)],
    responses={
        401: {"model": MessageResponse, "description": "Token ausente, inválido o expirado."},
        403: {"model": MessageResponse, "description": "Se requiere rol ADMINISTRADOR."},
    },
)

UsuarioServiceDep = Annotated[UsuarioService, Depends(get_usuario_service)]
_NOT_FOUND = {404: {"model": MessageResponse, "description": "Usuario no encontrado."}}


@router.post(
    "/teachers",
    response_model=UsuarioRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un docente",
    responses={409: {"model": MessageResponse, "description": "Correo o código institucional duplicado."}},
)
def create_teacher(data: DocenteCreate, service: UsuarioServiceDep) -> UsuarioRead:
    return UsuarioRead.model_validate(service.create_docente(data))


@router.get("", response_model=UsuarioList, summary="Listar usuarios")
def list_users(
    service: UsuarioServiceDep,
    rol: RolUsuario | None = None,
    activo: bool | None = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> UsuarioList:
    usuarios, total = service.list(rol=rol, activo=activo, skip=skip, limit=limit)
    return UsuarioList(
        items=[UsuarioRead.model_validate(u) for u in usuarios], total=total, skip=skip, limit=limit
    )


@router.get("/{usuario_id}", response_model=UsuarioRead, summary="Consultar un usuario", responses=_NOT_FOUND)
def get_user(usuario_id: uuid.UUID, service: UsuarioServiceDep) -> UsuarioRead:
    return UsuarioRead.model_validate(service.get(usuario_id))


@router.patch(
    "/{usuario_id}",
    response_model=UsuarioRead,
    summary="Actualizar datos personales",
    responses={**_NOT_FOUND, 409: {"model": MessageResponse, "description": "Correo o código duplicado."}},
)
def update_user(
    usuario_id: uuid.UUID, data: UsuarioUpdate, admin: AdminUser, service: UsuarioServiceDep
) -> UsuarioRead:
    return UsuarioRead.model_validate(service.update(admin, usuario_id, data))


@router.patch(
    "/{usuario_id}/status",
    response_model=UsuarioRead,
    summary="Activar o desactivar una cuenta",
    responses={**_NOT_FOUND, 400: {"model": MessageResponse, "description": "Regla de negocio infringida."}},
)
def update_user_status(
    usuario_id: uuid.UUID, data: UsuarioStatusUpdate, admin: AdminUser, service: UsuarioServiceDep
) -> UsuarioRead:
    return UsuarioRead.model_validate(service.set_status(admin, usuario_id, data.activo))


@router.patch(
    "/{usuario_id}/case-permission",
    response_model=UsuarioRead,
    summary="Conceder o revocar el permiso de crear casos",
    responses={**_NOT_FOUND, 400: {"model": MessageResponse, "description": "El usuario no es docente."}},
)
def update_case_permission(
    usuario_id: uuid.UUID, data: CasePermissionUpdate, service: UsuarioServiceDep
) -> UsuarioRead:
    return UsuarioRead.model_validate(service.set_case_permission(usuario_id, data.puede_crear_casos))
