import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import RolUsuario
from app.core.errores import Conflicto, NoEncontrado, ReglaNegocio
from app.core.seguridad import crear_token_acceso, hashear_clave, verificar_clave
from app.usuarios.models import EspacioAcademico, Usuario
from app.usuarios.schemas import (
    EspacioActualizar,
    EspacioCrear,
    UsuarioActualizar,
    UsuarioCrear,
    validar_reglas_rol,
)

# ---------- Autenticación ----------


def autenticar(sesion: Session, correo: str, clave: str) -> str | None:
    """Devuelve un token de acceso, o None si las credenciales no son válidas."""
    usuario = sesion.scalar(select(Usuario).where(Usuario.correo == correo.lower()))
    if usuario is None or not usuario.activo:
        return None
    if not verificar_clave(clave, usuario.clave_hash):
        return None
    return crear_token_acceso(usuario.id)


# ---------- Usuarios ----------


def listar_usuarios(
    sesion: Session, rol: RolUsuario | None = None, activo: bool | None = None
) -> list[Usuario]:
    consulta = select(Usuario).order_by(Usuario.nombre_completo)
    if rol is not None:
        consulta = consulta.where(Usuario.rol == rol)
    if activo is not None:
        consulta = consulta.where(Usuario.activo == activo)
    return list(sesion.scalars(consulta))


def obtener_usuario(sesion: Session, usuario_id: uuid.UUID) -> Usuario:
    usuario = sesion.get(Usuario, usuario_id)
    if usuario is None:
        raise NoEncontrado("Usuario no encontrado")
    return usuario


def _verificar_codigo_libre(
    sesion: Session, codigo: str | None, excepto_id: uuid.UUID | None = None
) -> None:
    if codigo is None:
        return
    existente = sesion.scalar(
        select(Usuario).where(Usuario.codigo_institucional == codigo)
    )
    if existente is not None and existente.id != excepto_id:
        raise Conflicto("Ya existe un usuario con ese código institucional")


def crear_usuario(sesion: Session, datos: UsuarioCrear) -> Usuario:
    correo = datos.correo.lower()
    if sesion.scalar(select(Usuario).where(Usuario.correo == correo)) is not None:
        raise Conflicto("Ya existe un usuario con ese correo")
    _verificar_codigo_libre(sesion, datos.codigo_institucional)

    usuario = Usuario(
        nombre_completo=datos.nombre_completo,
        correo=correo,
        clave_hash=hashear_clave(datos.clave),
        rol=datos.rol,
        codigo_institucional=datos.codigo_institucional,
        puede_crear_casos=datos.puede_crear_casos,
    )
    sesion.add(usuario)
    sesion.commit()
    sesion.refresh(usuario)
    return usuario


def actualizar_usuario(
    sesion: Session, usuario_id: uuid.UUID, datos: UsuarioActualizar, actor: Usuario
) -> Usuario:
    usuario = obtener_usuario(sesion, usuario_id)
    # Un null explícito solo tiene sentido en el código institucional (quitarlo)
    cambios = {
        campo: valor
        for campo, valor in datos.model_dump(exclude_unset=True).items()
        if valor is not None or campo == "codigo_institucional"
    }

    if usuario.id == actor.id and (
        cambios.get("activo") is False
        or cambios.get("rol", RolUsuario.ADMINISTRADOR) != RolUsuario.ADMINISTRADOR
    ):
        raise ReglaNegocio("No puede desactivarse ni quitarse el rol de administrador")

    # Al cambiar de rol se limpian los campos que dejan de aplicar
    rol = cambios.get("rol", usuario.rol)
    if rol != RolUsuario.DOCENTE:
        cambios.setdefault("puede_crear_casos", False)
    if rol != RolUsuario.ESTUDIANTE:
        cambios.setdefault("codigo_institucional", None)

    puede_crear = cambios.get("puede_crear_casos", usuario.puede_crear_casos)
    codigo = cambios.get("codigo_institucional", usuario.codigo_institucional)
    try:
        validar_reglas_rol(rol, puede_crear, codigo)
    except ValueError as error:
        raise ReglaNegocio(str(error)) from error
    _verificar_codigo_libre(sesion, codigo, excepto_id=usuario.id)

    if "clave" in cambios:
        usuario.clave_hash = hashear_clave(cambios.pop("clave"))
    for campo, valor in cambios.items():
        setattr(usuario, campo, valor)

    sesion.commit()
    sesion.refresh(usuario)
    return usuario


def desactivar_usuario(sesion: Session, usuario_id: uuid.UUID, actor: Usuario) -> None:
    """Baja lógica: los usuarios no se borran porque tienen registros académicos."""
    usuario = obtener_usuario(sesion, usuario_id)
    if usuario.id == actor.id:
        raise ReglaNegocio("No puede desactivar su propio usuario")
    usuario.activo = False
    sesion.commit()


# ---------- Espacios académicos ----------


def listar_espacios(
    sesion: Session, periodo: str | None = None, activo: bool | None = None
) -> list[EspacioAcademico]:
    consulta = select(EspacioAcademico).order_by(
        EspacioAcademico.periodo.desc(), EspacioAcademico.nombre
    )
    if periodo is not None:
        consulta = consulta.where(EspacioAcademico.periodo == periodo)
    if activo is not None:
        consulta = consulta.where(EspacioAcademico.activo == activo)
    return list(sesion.scalars(consulta))


def obtener_espacio(sesion: Session, espacio_id: uuid.UUID) -> EspacioAcademico:
    espacio = sesion.get(EspacioAcademico, espacio_id)
    if espacio is None:
        raise NoEncontrado("Espacio académico no encontrado")
    return espacio


def _verificar_espacio_unico(
    sesion: Session, nombre: str, periodo: str, excepto_id: uuid.UUID | None = None
) -> None:
    existente = sesion.scalar(
        select(EspacioAcademico).where(
            EspacioAcademico.nombre == nombre, EspacioAcademico.periodo == periodo
        )
    )
    if existente is not None and existente.id != excepto_id:
        raise Conflicto("Ya existe un espacio académico con ese nombre en ese periodo")


def crear_espacio(sesion: Session, datos: EspacioCrear) -> EspacioAcademico:
    _verificar_espacio_unico(sesion, datos.nombre, datos.periodo)
    espacio = EspacioAcademico(nombre=datos.nombre, periodo=datos.periodo)
    sesion.add(espacio)
    sesion.commit()
    sesion.refresh(espacio)
    return espacio


def actualizar_espacio(
    sesion: Session, espacio_id: uuid.UUID, datos: EspacioActualizar
) -> EspacioAcademico:
    espacio = obtener_espacio(sesion, espacio_id)
    cambios = datos.model_dump(exclude_unset=True, exclude_none=True)
    _verificar_espacio_unico(
        sesion,
        cambios.get("nombre", espacio.nombre),
        cambios.get("periodo", espacio.periodo),
        excepto_id=espacio.id,
    )
    for campo, valor in cambios.items():
        setattr(espacio, campo, valor)
    sesion.commit()
    sesion.refresh(espacio)
    return espacio


def desactivar_espacio(sesion: Session, espacio_id: uuid.UUID) -> None:
    """Baja lógica: las sesiones históricas siguen apuntando al espacio."""
    espacio = obtener_espacio(sesion, espacio_id)
    espacio.activo = False
    sesion.commit()
