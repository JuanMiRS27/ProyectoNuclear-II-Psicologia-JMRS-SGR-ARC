"""Generación de CaseDSL a partir del modelo relacional (única fuente de verdad)."""

from app.modules.cases.dsl.schema import (
    DSL_VERSION,
    CaseDSLDocument,
    CasoInfoDsl,
    CriterioDsl,
    EscenarioDsl,
    NivelDsl,
    OpcionDsl,
    PreguntaDsl,
    RubricaDsl,
)
from app.modules.cases.models import CasoEstudio, Pregunta


class InconsistenciaDslError(RuntimeError):
    """El modelo relacional contiene una asociación que la DSL no puede representar fielmente."""


def exportar_caso(caso: CasoEstudio) -> CaseDSLDocument:
    """Convierte el agregado persistido en un documento CaseDSL 1.0.

    La base de datos no guarda claves de criterio: se generan como C1..Cn según
    el orden de los criterios en la rúbrica, y cada pregunta referencia la clave
    del criterio cuyo id coincide con su `criterio_id`. Un criterio sin preguntas
    se exporta igualmente; una pregunta sin criterio exporta `criterio: null`.
    """
    claves = {criterio.id: f"C{indice}" for indice, criterio in enumerate(_criterios(caso), start=1)}

    def clave_de(pregunta: Pregunta) -> str | None:
        if pregunta.criterio_id is None:
            return None
        if pregunta.criterio_id not in claves:
            # El disparador de PostgreSQL lo impide; si ocurriera, no se exporta una DSL engañosa.
            raise InconsistenciaDslError(
                f"La pregunta {pregunta.id} referencia el criterio {pregunta.criterio_id}, ajeno a la rúbrica del caso."
            )
        return claves[pregunta.criterio_id]

    rubrica = None
    if caso.rubrica is not None:
        rubrica = RubricaDsl(
            id=caso.rubrica.id,
            escala_max=caso.rubrica.escala_max,
            criterios=[
                CriterioDsl(
                    id=criterio.id,
                    clave=claves[criterio.id],
                    descripcion=criterio.descripcion,
                    peso=criterio.peso,
                    niveles=[NivelDsl.model_validate(nivel) for nivel in criterio.niveles],
                )
                for criterio in caso.rubrica.criterios
            ],
        )

    return CaseDSLDocument(
        dsl_version=DSL_VERSION,
        caso=CasoInfoDsl(
            id=caso.id,
            nombre=caso.nombre,
            desarrollo_situacional=caso.desarrollo_situacional,
            historia=caso.historia,
            tiempo_estimado_min=caso.tiempo_estimado_min,
            estado=caso.estado.value,
        ),
        escenarios=[
            EscenarioDsl(
                id=escenario.id,
                titulo=escenario.titulo,
                contexto=escenario.contexto,
                preguntas=[
                    PreguntaDsl(
                        id=pregunta.id,
                        enunciado=pregunta.enunciado,
                        criterio=clave_de(pregunta),
                        opciones=[
                            OpcionDsl(
                                id=opcion.id,
                                texto=opcion.texto,
                                es_correcta=opcion.es_correcta,
                                justificacion=opcion.justificacion,
                            )
                            for opcion in pregunta.opciones
                        ],
                    )
                    for pregunta in escenario.preguntas
                ],
            )
            for escenario in caso.escenarios
        ],
        rubrica=rubrica,
    )


def _criterios(caso: CasoEstudio):
    return caso.rubrica.criterios if caso.rubrica is not None else []
