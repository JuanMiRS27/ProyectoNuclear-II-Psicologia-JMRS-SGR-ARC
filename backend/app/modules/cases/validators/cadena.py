"""Patrón Chain of Responsibility: validación de un caso antes de publicarlo.

Se usa la variante acumulativa de la cadena: cada eslabón revisa su aspecto
del caso, registra sus inconsistencias en el `ResultadoValidacion` compartido
y SIEMPRE delega en el siguiente. Así el docente recibe todos los problemas
en una sola respuesta y no solo el primero.

La cadena opera sobre el agregado `CasoEstudio` con su contenido cargado y no
modifica nada: es la misma para `POST /validate` (consulta) y para `POST /publish`.
"""

from abc import ABC, abstractmethod
from decimal import Decimal

from pydantic import ValidationError

from app.modules.cases.models import TIEMPO_MAXIMO_MIN, CasoEstudio
from app.modules.cases.schemas.common import NivelSchema
from app.modules.cases.validators.resultado import ResultadoValidacion

MIN_OPCIONES_POR_PREGUNTA = 2
MIN_NIVELES_POR_CRITERIO = 2  # JSON Schema de CaseDSL del proyecto
SUMA_PESOS_REQUERIDA = Decimal("100.00")


def _vacio(texto: str | None) -> bool:
    return texto is None or not texto.strip()


class ValidadorCaso(ABC):
    """Eslabón de la cadena."""

    def __init__(self) -> None:
        self._siguiente: ValidadorCaso | None = None

    def enlazar(self, siguiente: "ValidadorCaso") -> "ValidadorCaso":
        """Fija el siguiente eslabón y lo devuelve, para encadenar: a.enlazar(b).enlazar(c)."""
        self._siguiente = siguiente
        return siguiente

    def validar(self, caso: CasoEstudio, resultado: ResultadoValidacion) -> ResultadoValidacion:
        self._revisar(caso, resultado)
        if self._siguiente is not None:
            self._siguiente.validar(caso, resultado)
        return resultado

    @abstractmethod
    def _revisar(self, caso: CasoEstudio, resultado: ResultadoValidacion) -> None: ...


class ValidadorDatosCaso(ValidadorCaso):
    def _revisar(self, caso: CasoEstudio, resultado: ResultadoValidacion) -> None:
        def error(codigo: str, campo: str, mensaje: str) -> None:
            resultado.agregar(codigo, componente="caso", componente_id=caso.id, campo=campo, ruta=f"caso.{campo}", mensaje=mensaje)

        if _vacio(caso.nombre):
            error("CASO_NOMBRE_VACIO", "nombre", "El caso debe tener un nombre.")
        if _vacio(caso.desarrollo_situacional):
            error("CASO_DESARROLLO_SITUACIONAL_VACIO", "desarrollo_situacional", "Falta el desarrollo situacional del caso.")
        if _vacio(caso.historia):
            error("CASO_HISTORIA_VACIA", "historia", "Falta la historia o contexto general del caso.")

        tiempo = caso.tiempo_estimado_min
        if tiempo is None:
            error("CASO_TIEMPO_REQUERIDO", "tiempo_estimado_min", "Debe indicarse el tiempo estimado de participación.")
        elif not 1 <= tiempo <= TIEMPO_MAXIMO_MIN:
            # La API y PostgreSQL ya lo impiden; la cadena lo comprueba igualmente sobre el agregado.
            error(
                "CASO_TIEMPO_FUERA_DE_RANGO",
                "tiempo_estimado_min",
                f"El tiempo estimado debe estar entre 1 y {TIEMPO_MAXIMO_MIN} minutos.",
            )


class ValidadorEscenariosYPreguntas(ValidadorCaso):
    def _revisar(self, caso: CasoEstudio, resultado: ResultadoValidacion) -> None:
        if not caso.escenarios:
            resultado.agregar(
                "CASO_SIN_ESCENARIOS",
                componente="caso",
                componente_id=caso.id,
                campo="escenarios",
                ruta="escenarios",
                mensaje="El caso debe tener al menos un escenario.",
            )
            return

        _revisar_orden(
            [e.orden for e in caso.escenarios],
            resultado,
            codigo="ESCENARIO_ORDEN_INVALIDO",
            componente="caso",
            componente_id=caso.id,
            ruta="escenarios",
            mensaje="Los escenarios deben estar numerados de forma consecutiva desde 1.",
        )

        for i, escenario in enumerate(caso.escenarios):
            ruta = f"escenarios[{i}]"

            def error(codigo: str, campo: str, mensaje: str) -> None:
                resultado.agregar(codigo, componente="escenario", componente_id=escenario.id, campo=campo, ruta=f"{ruta}.{campo}", mensaje=mensaje)

            if _vacio(escenario.titulo):
                error("ESCENARIO_TITULO_VACIO", "titulo", f"El escenario {i + 1} no tiene título.")
            if _vacio(escenario.contexto):
                error("ESCENARIO_CONTEXTO_VACIO", "contexto", f"El escenario {i + 1} no tiene contexto.")
            if not escenario.preguntas:
                error("ESCENARIO_SIN_PREGUNTAS", "preguntas", f"El escenario {i + 1} debe tener al menos una pregunta.")
                continue

            _revisar_orden(
                [p.orden for p in escenario.preguntas],
                resultado,
                codigo="PREGUNTA_ORDEN_INVALIDO",
                componente="escenario",
                componente_id=escenario.id,
                ruta=f"{ruta}.preguntas",
                mensaje=f"Las preguntas del escenario {i + 1} deben estar numeradas de forma consecutiva desde 1.",
            )
            for j, pregunta in enumerate(escenario.preguntas):
                if _vacio(pregunta.enunciado):
                    resultado.agregar(
                        "PREGUNTA_ENUNCIADO_VACIO",
                        componente="pregunta",
                        componente_id=pregunta.id,
                        campo="enunciado",
                        ruta=f"{ruta}.preguntas[{j}].enunciado",
                        mensaje=f"La pregunta {j + 1} del escenario {i + 1} no tiene enunciado.",
                    )


class ValidadorOpciones(ValidadorCaso):
    def _revisar(self, caso: CasoEstudio, resultado: ResultadoValidacion) -> None:
        for i, escenario in enumerate(caso.escenarios):
            for j, pregunta in enumerate(escenario.preguntas):
                ruta = f"escenarios[{i}].preguntas[{j}]"
                nombre = f"La pregunta {j + 1} del escenario {i + 1}"

                def error_pregunta(codigo: str, mensaje: str) -> None:
                    resultado.agregar(codigo, componente="pregunta", componente_id=pregunta.id, campo="opciones", ruta=f"{ruta}.opciones", mensaje=mensaje)

                opciones = pregunta.opciones
                if len(opciones) < MIN_OPCIONES_POR_PREGUNTA:
                    error_pregunta(
                        "PREGUNTA_OPCIONES_INSUFICIENTES",
                        f"{nombre} debe tener al menos {MIN_OPCIONES_POR_PREGUNTA} opciones (tiene {len(opciones)}).",
                    )
                # Se admiten varias opciones correctas (incluso todas): el estudiante elige una sola
                # opción por pregunta y el módulo de Evaluación decidirá cómo puntuarla.
                if opciones and not any(o.es_correcta for o in opciones):
                    error_pregunta("PREGUNTA_SIN_OPCION_CORRECTA", f"{nombre} no tiene ninguna opción correcta.")

                for k, opcion in enumerate(opciones):
                    ruta_opcion = f"{ruta}.opciones[{k}]"

                    def error(codigo: str, campo: str, mensaje: str) -> None:
                        resultado.agregar(codigo, componente="opcion", componente_id=opcion.id, campo=campo, ruta=f"{ruta_opcion}.{campo}", mensaje=mensaje)

                    if _vacio(opcion.texto):
                        error("OPCION_TEXTO_VACIO", "texto", f"La opción {k + 1} de {nombre.lower()} no tiene texto.")
                    if _vacio(opcion.justificacion):
                        error("OPCION_SIN_JUSTIFICACION", "justificacion", f"La opción {k + 1} de {nombre.lower()} no tiene justificación.")


class ValidadorRubrica(ValidadorCaso):
    def _revisar(self, caso: CasoEstudio, resultado: ResultadoValidacion) -> None:
        rubrica = caso.rubrica
        if rubrica is None:
            resultado.agregar(
                "CASO_SIN_RUBRICA",
                componente="caso",
                componente_id=caso.id,
                campo="rubrica",
                ruta="rubrica",
                mensaje="El caso debe tener una rúbrica antes de publicarse.",
            )
            self._revisar_asociaciones(caso, set(), resultado)
            return

        def error_rubrica(codigo: str, campo: str, mensaje: str) -> None:
            resultado.agregar(codigo, componente="rubrica", componente_id=rubrica.id, campo=campo, ruta=f"rubrica.{campo}", mensaje=mensaje)

        if rubrica.escala_max is None or rubrica.escala_max <= 0:
            error_rubrica("RUBRICA_ESCALA_INVALIDA", "escala_max", "La escala máxima de la rúbrica debe ser mayor que 0.")

        if not rubrica.criterios:
            error_rubrica("RUBRICA_SIN_CRITERIOS", "criterios", "La rúbrica debe tener al menos un criterio.")
        else:
            suma = sum((c.peso for c in rubrica.criterios), Decimal("0"))
            if suma != SUMA_PESOS_REQUERIDA:
                error_rubrica(
                    "RUBRICA_PESOS_NO_SUMAN_100",
                    "criterios",
                    f"Los pesos de los criterios deben sumar 100 (suman {suma.quantize(Decimal('0.01'))}).",
                )
            for i, criterio in enumerate(rubrica.criterios):
                self._revisar_criterio(i, criterio, resultado)

        self._revisar_asociaciones(caso, {c.id for c in rubrica.criterios}, resultado)

    @staticmethod
    def _revisar_criterio(i: int, criterio, resultado: ResultadoValidacion) -> None:
        """Validación estructural del criterio.

        Se exige lo que fija el JSON Schema de CaseDSL: al menos dos niveles bien formados
        (`nombre` y `valor` obligatorios). Las reglas de calificación (porcentajes distintos,
        nivel base, valores dentro de la escala, monotonía) se definirán con el módulo 5.
        """
        ruta = f"rubrica.criterios[{i}]"

        def error(codigo: str, campo: str, mensaje: str) -> None:
            resultado.agregar(codigo, componente="criterio", componente_id=criterio.id, campo=campo, ruta=f"{ruta}.{campo}", mensaje=mensaje)

        if _vacio(criterio.descripcion):
            error("CRITERIO_DESCRIPCION_VACIA", "descripcion", f"El criterio {i + 1} no tiene descripción.")
        if criterio.peso is None or not (Decimal("0") < criterio.peso <= Decimal("100")):
            error("CRITERIO_PESO_INVALIDO", "peso", f"El peso del criterio {i + 1} debe ser mayor que 0 y como máximo 100.")

        try:
            niveles = [NivelSchema.model_validate(nivel) for nivel in criterio.niveles or []]
        except (ValidationError, TypeError):
            error("CRITERIO_NIVELES_FORMATO_INVALIDO", "niveles", f"Los niveles del criterio {i + 1} tienen un formato inválido.")
            return

        if len(niveles) < MIN_NIVELES_POR_CRITERIO:
            error(
                "CRITERIO_NIVELES_INSUFICIENTES",
                "niveles",
                f"El criterio {i + 1} debe definir al menos {MIN_NIVELES_POR_CRITERIO} niveles de desempeño "
                f"(tiene {len(niveles)}).",
            )
            return

        nombres = [n.nombre.casefold() for n in niveles]
        if len(set(nombres)) != len(nombres):
            # Estructural: el nombre identifica la categoría de desempeño; repetido sería ambiguo.
            error("NIVEL_NOMBRE_DUPLICADO", "niveles", f"El criterio {i + 1} repite nombres de nivel.")

    @staticmethod
    def _revisar_asociaciones(caso: CasoEstudio, ids_criterios: set, resultado: ResultadoValidacion) -> None:
        """Cada pregunta debe tener un criterio, y ese criterio debe pertenecer a la rúbrica de este caso.

        No se exige que todos los criterios tengan preguntas asociadas.
        """
        for i, escenario in enumerate(caso.escenarios):
            for j, pregunta in enumerate(escenario.preguntas):
                ruta = f"escenarios[{i}].preguntas[{j}].criterio_id"
                if pregunta.criterio_id is None:
                    resultado.agregar(
                        "PREGUNTA_SIN_CRITERIO",
                        componente="pregunta",
                        componente_id=pregunta.id,
                        campo="criterio_id",
                        ruta=ruta,
                        mensaje=f"La pregunta {j + 1} del escenario {i + 1} no está asociada a un criterio de la rúbrica.",
                    )
                elif pregunta.criterio_id not in ids_criterios:
                    resultado.agregar(
                        "PREGUNTA_CRITERIO_AJENO",
                        componente="pregunta",
                        componente_id=pregunta.id,
                        campo="criterio_id",
                        ruta=ruta,
                        mensaje=f"La pregunta {j + 1} del escenario {i + 1} referencia un criterio que no pertenece a la rúbrica del caso.",
                    )


def _revisar_orden(
    ordenes: list[int],
    resultado: ResultadoValidacion,
    *,
    codigo: str,
    componente: str,
    componente_id,
    ruta: str,
    mensaje: str,
) -> None:
    if sorted(ordenes) != list(range(1, len(ordenes) + 1)):
        resultado.agregar(codigo, componente=componente, componente_id=componente_id, campo="orden", ruta=ruta, mensaje=mensaje)


def construir_cadena_publicacion() -> ValidadorCaso:
    """Ensambla la cadena: datos -> escenarios y preguntas -> opciones -> rúbrica."""
    cabeza = ValidadorDatosCaso()
    cabeza.enlazar(ValidadorEscenariosYPreguntas()).enlazar(ValidadorOpciones()).enlazar(ValidadorRubrica())
    return cabeza


def validar_caso(caso: CasoEstudio) -> ResultadoValidacion:
    return construir_cadena_publicacion().validar(caso, ResultadoValidacion())
