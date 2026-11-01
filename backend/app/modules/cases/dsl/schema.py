"""CaseDSL 1.0: representación declarativa en JSON de un caso académico.

Este esquema valida solo la ESTRUCTURA del documento (tipos, longitudes,
referencias internas). Que el caso esté completo para publicarse lo decide
la cadena de validadores (`validators/`), que se ejecuta sobre el caso persistido.

La DSL no se almacena: PostgreSQL es la única fuente de verdad. Se importa
(Builder) y se exporta (`dsl/exporter.py`) a partir del modelo relacional.
"""

import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from app.modules.cases.schemas.common import (
    Descripcion255,
    EscalaMax,
    NivelSchema,
    Nombre200,
    Peso,
    TextoOpcional,
    TextoRequerido,
    TiempoMinutos,
    blank_to_none,
)

DSL_VERSION = "1.0"

ClaveCriterio = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
]
TextoPendiente = Annotated[TextoOpcional | None, AfterValidator(blank_to_none)]


class _Dsl(BaseModel):
    # JSON en camelCase (dslVersion, tiempoEstimadoMin...); en Python, snake_case.
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)


class NivelDsl(NivelSchema):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True)


class OpcionDsl(_Dsl):
    id: uuid.UUID | None = Field(default=None, description="Solo informativo en la exportación; se ignora al importar.")
    texto: TextoRequerido
    es_correcta: bool = False
    justificacion: TextoPendiente = None


class PreguntaDsl(_Dsl):
    id: uuid.UUID | None = None
    enunciado: TextoRequerido
    criterio: ClaveCriterio | None = Field(default=None, description="Clave de un criterio de la rúbrica.")
    opciones: list[OpcionDsl] = Field(default_factory=list, max_length=20)


class EscenarioDsl(_Dsl):
    id: uuid.UUID | None = None
    titulo: Nombre200
    contexto: TextoPendiente = None
    preguntas: list[PreguntaDsl] = Field(default_factory=list, max_length=200)


class CriterioDsl(_Dsl):
    id: uuid.UUID | None = None
    clave: ClaveCriterio
    descripcion: Descripcion255
    peso: Peso
    niveles: list[NivelDsl] = Field(default_factory=list, max_length=20)


class RubricaDsl(_Dsl):
    id: uuid.UUID | None = None
    escala_max: EscalaMax
    criterios: list[CriterioDsl] = Field(default_factory=list, max_length=50)


class CasoInfoDsl(_Dsl):
    id: uuid.UUID | None = None
    nombre: Nombre200
    desarrollo_situacional: TextoPendiente = None
    historia: TextoPendiente = None
    tiempo_estimado_min: TiempoMinutos | None = None
    estado: Literal["BORRADOR", "PUBLICADO", "ARCHIVADO"] | None = Field(
        default=None, description="Solo informativo en la exportación; un caso importado siempre es BORRADOR."
    )


class CaseDSLDocument(_Dsl):
    """Documento CaseDSL. El orden de los arreglos es el orden académico."""

    dsl_version: Literal["1.0"]
    caso: CasoInfoDsl
    escenarios: list[EscenarioDsl] = Field(default_factory=list, max_length=100)
    rubrica: RubricaDsl | None = None

    @model_validator(mode="after")
    def _referencias_de_criterios(self) -> "CaseDSLDocument":
        claves = [c.clave for c in self.rubrica.criterios] if self.rubrica else []
        repetidas = {clave for clave in claves if claves.count(clave) > 1}
        if repetidas:
            raise ValueError(f"Claves de criterio repetidas: {sorted(repetidas)}.")

        desconocidas = sorted(
            {
                pregunta.criterio
                for escenario in self.escenarios
                for pregunta in escenario.preguntas
                if pregunta.criterio is not None and pregunta.criterio not in claves
            }
        )
        if desconocidas:
            raise ValueError(f"Las preguntas referencian criterios inexistentes en la rúbrica: {desconocidas}.")
        return self
