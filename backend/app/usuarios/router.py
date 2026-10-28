import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.db import obtener_sesion
from app.core.deps import get_current_user, require_rol
from app.core.enums import RolUsuario
from app.usuarios import service
from app.usuarios.models import Usuario
from app.usuarios.schemas import (
    EspacioActualizar,
    EspacioCrear,
    EspacioRespuesta,
    Token,
    UsuarioActualizar,
    UsuarioCrear,
    UsuarioRespuesta,
)

solo_admin = require_rol(RolUsuario.ADMINISTRADOR)
admin_o_docente = require_rol(RolUsuario.ADMINISTRADOR, RolUsuario.DOCENTE)

# ---------- Autenticación ----------

router_auth = APIRouter(prefix="/auth", tags=["autenticación"])


@router_auth.post("/login", response_model=Token)
def login(
    formulario: OAuth2PasswordRequestForm = Depends(),
    sesion: Session = Depends(obtener_sesion),
):
    """Inicia sesión. En `username` va el correo del usuario."""
    token = service.autenticar(sesion, formulario.username, formulario.password)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o clave incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=token)


@router_auth.get("/me", response_model=UsuarioRespuesta)
def usuario_actual(usuario: Usuario = Depends(get_current_user)):
    return usuario


# ---------- Usuarios (solo administrador) ----------

router_usuarios = APIRouter(prefix="/usuarios", tags=["usuarios"])


@router_usuarios.get("", response_model=list[UsuarioRespuesta])
def listar_usuarios(
    rol: RolUsuario | None = None,
    activo: bool | None = None,
    sesion: Session = Depends(obtener_sesion),
    _: Usuario = Depends(solo_admin),
):
    return service.listar_usuarios(sesion, rol, activo)


@router_usuarios.post(
    "", response_model=UsuarioRespuesta, status_code=status.HTTP_201_CREATED
)
def crear_usuario(
    datos: UsuarioCrear,
    sesion: Session = Depends(obtener_sesion),
    _: Usuario = Depends(solo_admin),
):
    return service.crear_usuario(sesion, datos)


@router_usuarios.get("/{usuario_id}", response_model=UsuarioRespuesta)
def obtener_usuario(
    usuario_id: uuid.UUID,
    sesion: Session = Depends(obtener_sesion),
    _: Usuario = Depends(solo_admin),
):
    return service.obtener_usuario(sesion, usuario_id)


@router_usuarios.patch("/{usuario_id}", response_model=UsuarioRespuesta)
def actualizar_usuario(
    usuario_id: uuid.UUID,
    datos: UsuarioActualizar,
    sesion: Session = Depends(obtener_sesion),
    actor: Usuario = Depends(solo_admin),
):
    return service.actualizar_usuario(sesion, usuario_id, datos, actor)


@router_usuarios.delete("/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT)
def desactivar_usuario(
    usuario_id: uuid.UUID,
    sesion: Session = Depends(obtener_sesion),
    actor: Usuario = Depends(solo_admin),
):
    """Baja lógica: marca el usuario como inactivo."""
    service.desactivar_usuario(sesion, usuario_id, actor)


# ---------- Espacios académicos ----------

router_espacios = APIRouter(prefix="/espacios-academicos", tags=["espacios académicos"])


@router_espacios.get("", response_model=list[EspacioRespuesta])
def listar_espacios(
    periodo: str | None = None,
    activo: bool | None = None,
    sesion: Session = Depends(obtener_sesion),
    _: Usuario = Depends(get_current_user),
):
    return service.listar_espacios(sesion, periodo, activo)


@router_espacios.post(
    "", response_model=EspacioRespuesta, status_code=status.HTTP_201_CREATED
)
def crear_espacio(
    datos: EspacioCrear,
    sesion: Session = Depends(obtener_sesion),
    _: Usuario = Depends(admin_o_docente),
):
    return service.crear_espacio(sesion, datos)


@router_espacios.get("/{espacio_id}", response_model=EspacioRespuesta)
def obtener_espacio(
    espacio_id: uuid.UUID,
    sesion: Session = Depends(obtener_sesion),
    _: Usuario = Depends(get_current_user),
):
    return service.obtener_espacio(sesion, espacio_id)


@router_espacios.patch("/{espacio_id}", response_model=EspacioRespuesta)
def actualizar_espacio(
    espacio_id: uuid.UUID,
    datos: EspacioActualizar,
    sesion: Session = Depends(obtener_sesion),
    _: Usuario = Depends(admin_o_docente),
):
    return service.actualizar_espacio(sesion, espacio_id, datos)


@router_espacios.delete("/{espacio_id}", status_code=status.HTTP_204_NO_CONTENT)
def desactivar_espacio(
    espacio_id: uuid.UUID,
    sesion: Session = Depends(obtener_sesion),
    _: Usuario = Depends(solo_admin),
):
    """Baja lógica: marca el espacio como inactivo."""
    service.desactivar_espacio(sesion, espacio_id)
