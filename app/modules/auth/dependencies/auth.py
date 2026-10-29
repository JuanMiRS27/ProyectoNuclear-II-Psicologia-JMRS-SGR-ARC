"""Dependencias reutilizables de autenticación y autorización.

Uso desde cualquier módulo:

    from app.modules.auth.dependencies import CurrentUser, require_case_creator

    @router.post("/cases", dependencies=[Depends(require_case_creator)])
    def create_case(...): ...

o bien, para obtener el usuario:

    def create_case(docente: CaseCreator): ...
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.modules.auth.models import RolUsuario, Usuario
from app.modules.auth.services import AuthService, UsuarioService

_bearer_scheme = HTTPBearer(auto_error=False, description="Token JWT obtenido en /api/v1/auth/login")

DbSession = Annotated[Session, Depends(get_db)]


def get_auth_service(db: DbSession) -> AuthService:
    return AuthService(db)


def get_usuario_service(db: DbSession) -> UsuarioService:
    return UsuarioService(db)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> Usuario:
    """Exige un Bearer token válido y una cuenta existente y activa en la base de datos."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Se requiere autenticación.")
    return auth_service.get_user_from_token(credentials.credentials)


CurrentUser = Annotated[Usuario, Depends(get_current_user)]


def require_roles(*roles: RolUsuario) -> Callable[[Usuario], Usuario]:
    """Fábrica de dependencias que exige que el usuario tenga alguno de los roles indicados."""
    allowed = frozenset(roles)

    def dependency(usuario: CurrentUser) -> Usuario:
        if usuario.rol not in allowed:
            raise PermissionDeniedError()
        return usuario

    return dependency


require_admin = require_roles(RolUsuario.ADMINISTRADOR)
require_docente = require_roles(RolUsuario.DOCENTE)
# Perfil propio con contraseña: los estudiantes no tienen contraseña (acceso por convocatoria).
require_password_user = require_roles(RolUsuario.ADMINISTRADOR, RolUsuario.DOCENTE)


def require_case_creator(docente: Annotated[Usuario, Depends(require_docente)]) -> Usuario:
    """Exige un docente activo con el permiso `puede_crear_casos`. Pensada para el módulo 2."""
    if not docente.puede_crear_casos:
        raise PermissionDeniedError("No tiene permiso para crear casos académicos.")
    return docente


AdminUser = Annotated[Usuario, Depends(require_admin)]
DocenteUser = Annotated[Usuario, Depends(require_docente)]
PasswordUser = Annotated[Usuario, Depends(require_password_user)]
CaseCreator = Annotated[Usuario, Depends(require_case_creator)]
