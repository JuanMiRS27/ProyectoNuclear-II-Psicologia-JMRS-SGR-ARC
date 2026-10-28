import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.core.enums import RolUsuario

Nombre = Annotated[str, Field(min_length=3, max_length=150)]
Clave = Annotated[str, Field(min_length=8, max_length=128)]
CodigoInstitucional = Annotated[str, Field(min_length=3, max_length=30)]
Periodo = Annotated[str, Field(pattern=r"^\d{4}-[12]$", examples=["2026-2"])]


def validar_reglas_rol(
    rol: RolUsuario, puede_crear_casos: bool, codigo_institucional: str | None
) -> None:
    """Reglas que dependen del rol; se usan al crear y al actualizar."""
    if puede_crear_casos and rol != RolUsuario.DOCENTE:
        raise ValueError("Solo un docente puede tener el permiso de crear casos (RF-02)")
    if rol == RolUsuario.ESTUDIANTE and not codigo_institucional:
        raise ValueError("El estudiante debe tener código institucional")
    if rol != RolUsuario.ESTUDIANTE and codigo_institucional:
        raise ValueError("Solo los estudiantes tienen código institucional")


# ---------- Autenticación ----------


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------- Usuarios ----------


class UsuarioCrear(BaseModel):
    nombre_completo: Nombre
    correo: EmailStr
    clave: Clave
    rol: RolUsuario
    codigo_institucional: CodigoInstitucional | None = None
    puede_crear_casos: bool = False

    @model_validator(mode="after")
    def _reglas_rol(self) -> "UsuarioCrear":
        validar_reglas_rol(self.rol, self.puede_crear_casos, self.codigo_institucional)
        return self


class UsuarioActualizar(BaseModel):
    """Campos opcionales: solo se cambian los que llegan."""

    nombre_completo: Nombre | None = None
    clave: Clave | None = None
    rol: RolUsuario | None = None
    codigo_institucional: CodigoInstitucional | None = None
    puede_crear_casos: bool | None = None
    activo: bool | None = None


class UsuarioRespuesta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre_completo: str
    correo: str
    rol: RolUsuario
    codigo_institucional: str | None
    puede_crear_casos: bool
    activo: bool
    creado_en: datetime


# ---------- Espacios académicos ----------


class EspacioCrear(BaseModel):
    nombre: Nombre
    periodo: Periodo


class EspacioActualizar(BaseModel):
    nombre: Nombre | None = None
    periodo: Periodo | None = None
    activo: bool | None = None


class EspacioRespuesta(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre: str
    periodo: str
    activo: bool
    creado_en: datetime
