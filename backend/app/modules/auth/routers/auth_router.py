from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.auth.dependencies import PasswordUser, get_auth_service
from app.modules.auth.schemas import LoginRequest, MessageResponse, PasswordChangeRequest, TokenResponse, UsuarioRead
from app.modules.auth.services import AuthService

router = APIRouter(prefix="/auth", tags=["Autenticación"])

AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]

_AUTH_ERRORS = {401: {"model": MessageResponse, "description": "Token ausente, inválido o expirado."}}


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Iniciar sesión (administradores y docentes)",
    responses={
        401: {"model": MessageResponse, "description": "Credenciales incorrectas."},
        403: {"model": MessageResponse, "description": "Cuenta desactivada."},
    },
)
def login(data: LoginRequest, service: AuthServiceDep) -> TokenResponse:
    return service.login(data.correo, data.clave)


@router.get("/me", response_model=UsuarioRead, summary="Consultar el usuario autenticado", responses=_AUTH_ERRORS)
def read_me(usuario: PasswordUser) -> UsuarioRead:
    return UsuarioRead.model_validate(usuario)


@router.patch(
    "/password",
    response_model=MessageResponse,
    summary="Cambiar la contraseña propia (invalida los tokens emitidos antes)",
    responses={**_AUTH_ERRORS, 400: {"model": MessageResponse, "description": "Contraseña actual incorrecta."}},
)
def change_password(data: PasswordChangeRequest, usuario: PasswordUser, service: AuthServiceDep) -> MessageResponse:
    service.change_password(usuario, data.clave_actual, data.clave_nueva)
    return MessageResponse(detail="Contraseña actualizada. Los tokens anteriores fueron invalidados; inicie sesión nuevamente.")
