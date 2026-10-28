"""Dependencias compartidas de autenticación y autorización.

Contrato para todos los módulos: proteger endpoints con
    usuario: Usuario = Depends(get_current_user)
    _ = Depends(require_rol(RolUsuario.DOCENTE))

Las firmas son definitivas. La implementación con JWT llega en el módulo de
usuarios; mientras tanto, en las pruebas se reemplazan con
app.dependency_overrides[get_current_user] = lambda: usuario_de_prueba.
"""

from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from app.core.enums import RolUsuario
from app.usuarios.models import Usuario


def get_current_user() -> Usuario:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Autenticación pendiente de implementar",
    )


def require_rol(*roles: RolUsuario) -> Callable[..., Usuario]:
    def verificar(usuario: Usuario = Depends(get_current_user)) -> Usuario:
        if usuario.rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para esta operación",
            )
        return usuario

    return verificar
