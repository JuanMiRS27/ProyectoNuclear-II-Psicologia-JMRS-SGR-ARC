"""Esquemas del módulo 4.

Los esquemas del cuestionario son ESPECÍFICOS para estudiantes: solo declaran los campos
permitidos y se construyen campo a campo (sin `from_attributes`), de modo que una
serialización accidental no puede filtrar `es_correcta`, `justificacion`, criterios de la
rúbrica ni la representación CaseDSL del docente.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.participations.models import EstadoParticipacionEnum, MotivoCierreEnum


class ProgresoRead(BaseModel):
    total_preguntas: int
    respondidas: int
    pendientes: int
    porcentaje: int = Field(description="Porcentaje entero (redondeo hacia abajo) de preguntas respondidas.")


class ParticipacionRead(BaseModel):
    """Estado de un intento. Todo se calcula en el servidor; el cliente no puede alterarlo."""

    id: uuid.UUID
    convocatoria_id: uuid.UUID
    sesion_id: uuid.UUID
    numero_intento: int
    estado: EstadoParticipacionEnum
    motivo_cierre: MotivoCierreEnum | None
    inicio: datetime
    fin: datetime | None
    fecha_limite: datetime
    tiempo_restante_segundos: int
    intentos_autorizados: int
    intentos_utilizados: int
    progreso: ProgresoRead


class OpcionCuestionario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    orden: int
    texto: str


class PreguntaCuestionario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    orden: int
    enunciado: str
    opcion_seleccionada_id: uuid.UUID | None = Field(description="Selección guardada por el estudiante, si existe.")
    opciones: list[OpcionCuestionario]


class EscenarioCuestionario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    orden: int
    titulo: str
    contexto: str | None
    preguntas: list[PreguntaCuestionario]


class CasoCuestionario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str
    desarrollo_situacional: str | None
    historia: str | None


class CuestionarioRead(BaseModel):
    participacion_id: uuid.UUID
    estado: EstadoParticipacionEnum
    fecha_limite: datetime
    tiempo_restante_segundos: int
    caso: CasoCuestionario
    escenarios: list[EscenarioCuestionario]
    progreso: ProgresoRead


class RespuestaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opcion_id: uuid.UUID


class RespuestaRead(BaseModel):
    """Selección guardada. Nunca indica si es correcta."""

    pregunta_id: uuid.UUID
    opcion_id: uuid.UUID
    respondida_en: datetime
    actualizada_en: datetime


__all__ = [
    "CasoCuestionario",
    "CuestionarioRead",
    "EscenarioCuestionario",
    "OpcionCuestionario",
    "ParticipacionRead",
    "PreguntaCuestionario",
    "ProgresoRead",
    "RespuestaRead",
    "RespuestaRequest",
]
