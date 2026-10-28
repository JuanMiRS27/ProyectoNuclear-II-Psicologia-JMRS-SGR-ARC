"""Dependencias compartidas de autenticación y autorización.

Contrato para todos los módulos: proteger endpoints con
    usuario: Usuario = Depends(get_current_user)
    usuario: Usuario = Depends(require_rol(RolUsuario.DOCENTE))

En las pruebas se pueden reemplazar con
app.dependency_overrides[get_current_user] = lambda: usuario_de_prueba.
"""

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.db import obtener_sesion
from app.core.enums import RolUsuario
from app.core.seguridad import leer_token_acceso
from app.usuarios.models import Usuario

esquema_oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(esquema_oauth2),
    sesion: Session = Depends(obtener_sesion),
) -> Usuario:
    no_autenticado = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas o sesión expirada",
        headers={"WWW-Authenticate": "Bearer"},
    )
    usuario_id = leer_token_acceso(token)
    if usuario_id is None:
        raise no_autenticado
    usuario = sesion.get(Usuario, usuario_id)
    if usuario is None or not usuario.activo:
        raise no_autenticado
    return usuario


def require_rol(*roles: RolUsuario) -> Callable[..., Usuario]:
    def verificar(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        if usuario.rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para esta operación",
            )
        return usuario

    return verificar
