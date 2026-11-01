import uuid

from app.modules.cases.builders.constructor import CasoAcademico, ConstructorCaso
from app.modules.cases.dsl.schema import CaseDSLDocument


class ServicioConstruccionCaso:
    """Director del Builder: define la secuencia para construir un caso desde CaseDSL.

    Recibe la abstracción `ConstructorCaso`, de modo que otra implementación
    (p. ej., una que construya una vista previa) puede reutilizar la misma secuencia.
    """

    def __init__(self, constructor: ConstructorCaso) -> None:
        self._constructor = constructor

    def construir_desde_dsl(self, documento: CaseDSLDocument, autor_id: uuid.UUID) -> CasoAcademico:
        c = self._constructor
        c.reiniciar(autor_id)

        info = documento.caso
        # El estado del documento se ignora: todo caso construido empieza en BORRADOR.
        c.establecer_datos_generales(info.nombre, info.desarrollo_situacional, info.historia, info.tiempo_estimado_min)

        if documento.rubrica is not None:
            c.establecer_rubrica(documento.rubrica.escala_max)
            for criterio in documento.rubrica.criterios:
                niveles = [nivel.model_dump(mode="python", by_alias=False) for nivel in criterio.niveles]
                c.agregar_criterio(criterio.clave, criterio.descripcion, criterio.peso, niveles)

        for escenario in documento.escenarios:
            ref_escenario = c.agregar_escenario(escenario.titulo, escenario.contexto)
            for pregunta in escenario.preguntas:
                ref_pregunta = c.agregar_pregunta(ref_escenario, pregunta.enunciado, pregunta.criterio)
                for opcion in pregunta.opciones:
                    c.agregar_opcion(ref_pregunta, opcion.texto, opcion.es_correcta, opcion.justificacion)

        return c.construir()
