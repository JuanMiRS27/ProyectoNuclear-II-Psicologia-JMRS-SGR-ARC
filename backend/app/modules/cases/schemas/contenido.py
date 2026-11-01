"""Esquemas de creación, actualización y respuesta de los componentes de un caso."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from app.modules.cases.models import EstadoCasoEnum
from app.modules.cases.schemas.common import (
    Descripcion255,
    EscalaMax,
    NivelSchema,
    Nombre200,
    Orden,
    Peso,
    TextoOpcional,
    TextoRequerido,
    TiempoMinutos,
    blank_to_none,
)

TextoPendiente = Annotated[TextoOpcional | None, AfterValidator(blank_to_none)]


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Salida(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Caso ---------------------------------------------------------------------


class CasoCreate(_Entrada):
    nombre: Nombre200
    desarrollo_situacional: TextoPendiente = None
    historia: TextoPendiente = None
    tiempo_estimado_min: TiempoMinutos | None = None


class CasoUpdate(_Entrada):
    """Actualización parcial de la información general. `null` borra un campo opcional."""

    nombre: Nombre200 | None = None
    desarrollo_situacional: TextoPendiente = None
    historia: TextoPendiente = None
    tiempo_estimado_min: TiempoMinutos | None = None


class CasoResumen(_Salida):
    id: uuid.UUID
    autor_id: uuid.UUID
    nombre: str
    tiempo_estimado_min: int | None
    estado: EstadoCasoEnum
    creado_en: datetime
    actualizado_en: datetime


class CasoList(BaseModel):
    items: list[CasoResumen]
    total: int
    skip: int
    limit: int


# --- Opción -------------------------------------------------------------------


class OpcionCreate(_Entrada):
    texto: TextoRequerido
    es_correcta: bool = False
    justificacion: TextoPendiente = None


class OpcionUpdate(_Entrada):
    texto: TextoRequerido | None = None
    es_correcta: bool | None = None
    justificacion: TextoPendiente = None


class OpcionRead(_Salida):
    """Vista de autor: incluye la respuesta correcta y la justificación.

    No debe reutilizarse para estudiantes (módulo 4 definirá su propia vista).
    """

    id: uuid.UUID
    pregunta_id: uuid.UUID
    orden: int
    texto: str
    es_correcta: bool
    justificacion: str | None


# --- Pregunta -----------------------------------------------------------------


class PreguntaCreate(_Entrada):
    enunciado: TextoRequerido
    criterio_id: uuid.UUID | None = None
    orden: Orden | None = Field(default=None, description="Si se omite, se agrega al final.")


class PreguntaUpdate(_Entrada):
    """`criterio_id: null` deja la asociación pendiente (solo en borrador)."""

    enunciado: TextoRequerido | None = None
    criterio_id: uuid.UUID | None = None


class PreguntaRead(_Salida):
    id: uuid.UUID
    escenario_id: uuid.UUID
    criterio_id: uuid.UUID | None
    orden: int
    enunciado: str
    opciones: list[OpcionRead]


# --- Escenario ----------------------------------------------------------------


class EscenarioCreate(_Entrada):
    titulo: Nombre200
    contexto: TextoPendiente = None
    orden: Orden | None = Field(default=None, description="Si se omite, se agrega al final.")


class EscenarioUpdate(_Entrada):
    titulo: Nombre200 | None = None
    contexto: TextoPendiente = None


class EscenarioRead(_Salida):
    id: uuid.UUID
    caso_id: uuid.UUID
    orden: int
    titulo: str
    contexto: str | None
    preguntas: list[PreguntaRead]


class ReordenarRequest(_Entrada):
    """Nuevo orden completo: todos los identificadores actuales, cada uno una sola vez."""

    ids: list[uuid.UUID] = Field(min_length=1)

    @field_validator("ids")
    @classmethod
    def _sin_repetidos(cls, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(ids)) != len(ids):
            raise ValueError("La lista contiene identificadores repetidos.")
        return ids


# --- Rúbrica ------------------------------------------------------------------


class CriterioCreate(_Entrada):
    descripcion: Descripcion255
    peso: Peso
    niveles: list[NivelSchema] = Field(default_factory=list, max_length=20)


class CriterioUpdate(_Entrada):
    descripcion: Descripcion255 | None = None
    peso: Peso | None = None
    niveles: list[NivelSchema] | None = Field(default=None, max_length=20)


class CriterioRead(_Salida):
    id: uuid.UUID
    rubrica_id: uuid.UUID
    descripcion: str
    peso: Decimal
    niveles: list[NivelSchema]


class RubricaCreate(_Entrada):
    escala_max: EscalaMax
    criterios: list[CriterioCreate] = Field(default_factory=list, max_length=50)


class RubricaUpdate(_Entrada):
    escala_max: EscalaMax


class RubricaRead(_Salida):
    id: uuid.UUID
    caso_id: uuid.UUID
    escala_max: Decimal
    criterios: list[CriterioRead]


class CasoDetalle(CasoResumen):
    desarrollo_situacional: str | None
    historia: str | None
    escenarios: list[EscenarioRead]
    rubrica: RubricaRead | None
