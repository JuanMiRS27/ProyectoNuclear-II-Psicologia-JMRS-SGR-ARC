import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.modules.auth.models import RolUsuario

PASSWORD_MIN_LENGTH = 8
# Límite superior para evitar hashes costosos con entradas gigantes.
PASSWORD_MAX_LENGTH = 128


def normalize_email(value: str) -> str:
    return value.strip().lower()


Correo = Annotated[EmailStr, Field(max_length=150), AfterValidator(normalize_email)]
NombreCompleto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
CodigoInstitucional = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30, pattern=r"^[A-Za-z0-9._-]+$")
]
Clave = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


class UsuarioRead(BaseModel):
    """Representación pública de un usuario. Nunca incluye `clave_hash`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    nombre_completo: str
    correo: str
    rol: RolUsuario
    codigo_institucional: str | None
    puede_crear_casos: bool
    activo: bool
    creado_en: datetime


class UsuarioList(BaseModel):
    items: list[UsuarioRead]
    total: int
    skip: int
    limit: int


class DocenteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre_completo: NombreCompleto
    correo: Correo
    clave: Clave
    codigo_institucional: CodigoInstitucional | None = None


class UsuarioUpdate(BaseModel):
    """Datos personales modificables. El rol, la contraseña y los permisos tienen endpoints propios."""

    model_config = ConfigDict(extra="forbid")

    nombre_completo: NombreCompleto | None = None
    correo: Correo | None = None
    codigo_institucional: CodigoInstitucional | None = None


class UsuarioStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activo: bool


class CasePermissionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    puede_crear_casos: bool
