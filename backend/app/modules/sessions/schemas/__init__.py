"""Esquemas del módulo 3.

Ningún esquema de respuesta incluye `codigo_autorizacion`, salvo `CodigoConvocatoriaRead`,
que solo devuelven los endpoints dedicados al docente responsable.
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.modules.auth.schemas.usuario import Correo
from app.modules.cases.models import TIEMPO_MAXIMO_MIN
from app.modules.sessions.models import EstadoSesionEnum


def _a_utc(valor: datetime) -> datetime:
    try:
        return valor.astimezone(UTC)
    except OverflowError as exc:  # p. ej., año 1 con desplazamiento positivo: no existe en UTC
        raise ValueError("La fecha está fuera del rango admitido.") from exc


# Se exige zona horaria explícita (p. ej. "2026-11-03T08:00:00-05:00"); se normaliza a UTC.
Instante = Annotated[AwareDatetime, AfterValidator(_a_utc)]
Nombre150 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Periodo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10, pattern=r"^[A-Za-z0-9._-]+$")]
Lugar = Annotated[str, StringConstraints(strip_whitespace=True, max_length=150)]
Mensaje = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]
TiempoMaximo = Annotated[int, Field(ge=1, le=TIEMPO_MAXIMO_MIN)]
Motivo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


def _vacio_a_none(valor: str | None) -> str | None:
    return valor or None


def _sin_repetidos(correos: list[str]) -> list[str]:
    if len(set(correos)) != len(correos):
        raise ValueError("La lista contiene correos repetidos.")
    return correos


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Salida(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Espacios académicos --------------------------------------------------------------


class EspacioCreate(_Entrada):
    nombre: Nombre150
    periodo: Periodo


class EspacioUpdate(_Entrada):
    nombre: Nombre150 | None = None
    periodo: Periodo | None = None


class EspacioStatusUpdate(_Entrada):
    activo: bool


class EspacioRead(_Salida):
    id: uuid.UUID
    nombre: str
    periodo: str
    activo: bool
    creado_en: datetime


class EspacioList(BaseModel):
    items: list[EspacioRead]
    total: int
    skip: int
    limit: int


# --- Sesiones -------------------------------------------------------------------------


ListaCorreos = Annotated[list[Correo], Field(max_length=500), AfterValidator(_sin_repetidos)]


class SesionCreate(_Entrada):
    caso_id: uuid.UUID
    espacio_academico_id: uuid.UUID
    inicio: Instante
    fin: Instante
    tiempo_max_min: TiempoMaximo
    lugar: Annotated[Lugar | None, AfterValidator(_vacio_a_none)] = None
    mensaje: Annotated[Mensaje | None, AfterValidator(_vacio_a_none)] = None
    estudiantes: ListaCorreos = Field(default_factory=list, description="Correos institucionales de los estudiantes.")

    @model_validator(mode="after")
    def _ventana_valida(self) -> "SesionCreate":
        if self.inicio >= self.fin:
            raise ValueError("La hora de inicio debe ser anterior a la de finalización.")
        return self


class SesionUpdate(_Entrada):
    """Cambios parciales. Con la sesión EN_CURSO solo se aceptan `lugar` y `mensaje`."""

    caso_id: uuid.UUID | None = None
    espacio_academico_id: uuid.UUID | None = None
    inicio: Instante | None = None
    fin: Instante | None = None
    tiempo_max_min: TiempoMaximo | None = None
    lugar: Annotated[Lugar | None, AfterValidator(_vacio_a_none)] = None
    mensaje: Annotated[Mensaje | None, AfterValidator(_vacio_a_none)] = None


class SesionRead(_Salida):
    id: uuid.UUID
    caso_id: uuid.UUID
    espacio_academico_id: uuid.UUID
    docente_id: uuid.UUID
    inicio: datetime
    fin: datetime
    limite_ingreso: datetime = Field(description="Último instante (incluido) para ingresar: mitad de la ventana.")
    tiempo_max_min: int
    lugar: str | None
    mensaje: str | None
    estado: EstadoSesionEnum
    creada_en: datetime
    total_convocatorias: int


class SesionList(BaseModel):
    items: list[SesionRead]
    total: int
    skip: int
    limit: int


# --- Convocatorias --------------------------------------------------------------------


class EstudianteResumen(_Salida):
    id: uuid.UUID
    nombre_completo: str
    correo: str
    codigo_institucional: str | None


class ConvocatoriaCreate(_Entrada):
    estudiantes: Annotated[list[Correo], Field(min_length=1, max_length=500), AfterValidator(_sin_repetidos)]


class ConvocatoriaRead(_Salida):
    """Nunca incluye el código de autorización."""

    id: uuid.UUID
    sesion_id: uuid.UUID
    estudiante: EstudianteResumen
    intentos_autorizados: int
    creada_en: datetime
    activa: bool
    revocada_en: datetime | None


class IntentosUpdate(_Entrada):
    intentos_autorizados: int = Field(ge=1, le=100, description="Nuevo total; debe ser mayor que el actual.")
    motivo: Motivo = Field(description="Circunstancia excepcional que justifica el aumento.")


class RegenerarCodigoRequest(_Entrada):
    motivo: Motivo = Field(description="Justificación de seguridad (p. ej., código expuesto).")


class CodigoConvocatoriaRead(BaseModel):
    convocatoria_id: uuid.UUID
    codigo_autorizacion: str


# --- Acceso estudiantil ---------------------------------------------------------------


def _normalizar_codigo(valor: str) -> str:
    # Tolera minúsculas, espacios y guiones al transcribir el código.
    return re.sub(r"[\s-]", "", valor).upper()


class StudentLoginRequest(_Entrada):
    correo: Correo
    # Sin validar formato aquí: un código mal formado responde igual que uno incorrecto (401).
    codigo: Annotated[str, Field(min_length=1, max_length=40), AfterValidator(_normalizar_codigo)]


class StudentTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    sesion_id: uuid.UUID
    convocatoria_id: uuid.UUID


class SesionEstudianteRead(BaseModel):
    """Lo que ve el estudiante de su sesión: sin contenido académico (módulo 4) ni datos de otros."""

    id: uuid.UUID
    caso_nombre: str
    inicio: datetime
    fin: datetime
    limite_ingreso: datetime
    tiempo_max_min: int
    lugar: str | None
    mensaje: str | None
    estado: EstadoSesionEnum


class AccesoEstudiantilRead(BaseModel):
    estudiante_id: uuid.UUID
    nombre_completo: str
    convocatoria_id: uuid.UUID
    intentos_autorizados: int
    puede_iniciar_participacion: bool = Field(
        description="Si ahora mismo se admitiría un ingreso nuevo (EN_CURSO y antes del límite de ingreso). "
        "Estar autenticado no basta: el módulo 4 lo exigirá al crear cada participación."
    )
    sesion: SesionEstudianteRead


__all__ = [
    "AccesoEstudiantilRead",
    "CodigoConvocatoriaRead",
    "ConvocatoriaCreate",
    "ConvocatoriaRead",
    "EspacioCreate",
    "EspacioList",
    "EspacioRead",
    "EspacioStatusUpdate",
    "EspacioUpdate",
    "EstudianteResumen",
    "IntentosUpdate",
    "RegenerarCodigoRequest",
    "SesionCreate",
    "SesionEstudianteRead",
    "SesionList",
    "SesionRead",
    "SesionUpdate",
    "StudentLoginRequest",
    "StudentTokenResponse",
]
