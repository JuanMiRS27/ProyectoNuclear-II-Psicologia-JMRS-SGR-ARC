"""Patrón Builder: construcción paso a paso de un caso académico completo.

Participantes:
- `ConstructorCaso` (abstracción): declara los pasos de construcción.
- `ConstructorCasoAcademico` (constructor concreto): mantiene el estado parcial,
  asigna órdenes, resuelve referencias a criterios y produce el agregado.
- `CasoAcademico` (producto): el agregado completo listo para persistirse.
- `ServicioConstruccionCaso` (director, en `director.py`): fija la secuencia de pasos.

El constructor no usa la sesión de base de datos: produce entidades transitorias
que el servicio de aplicación persiste después en una sola transacción.
"""

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.exceptions import UnprocessableEntityError
from app.modules.cases.models import CasoEstudio, CriterioRubrica, Escenario, EstadoCasoEnum, Opcion, Pregunta, Rubrica


class ErrorConstruccionCaso(UnprocessableEntityError):
    default_detail = "No se pudo construir el caso con la estructura proporcionada."


@dataclass(frozen=True, slots=True)
class ReferenciaEscenario:
    indice: int


@dataclass(frozen=True, slots=True)
class ReferenciaPregunta:
    escenario: ReferenciaEscenario
    indice: int


@dataclass(frozen=True, slots=True)
class CasoAcademico:
    """Producto del Builder: la raíz del agregado con todo su contenido enlazado."""

    caso: CasoEstudio

    @property
    def escenarios(self) -> list[Escenario]:
        return list(self.caso.escenarios)

    @property
    def preguntas(self) -> list[Pregunta]:
        return self.caso.preguntas

    @property
    def opciones(self) -> list[Opcion]:
        return [opcion for pregunta in self.preguntas for opcion in pregunta.opciones]

    @property
    def rubrica(self) -> Rubrica | None:
        return self.caso.rubrica


class ConstructorCaso(ABC):
    """Abstracción del Builder: pasos para construir un caso académico."""

    @abstractmethod
    def reiniciar(self, autor_id: uuid.UUID) -> None: ...

    @abstractmethod
    def establecer_datos_generales(
        self,
        nombre: str,
        desarrollo_situacional: str | None,
        historia: str | None,
        tiempo_estimado_min: int | None,
    ) -> None: ...

    @abstractmethod
    def agregar_escenario(self, titulo: str, contexto: str | None) -> ReferenciaEscenario: ...

    @abstractmethod
    def agregar_pregunta(
        self, escenario: ReferenciaEscenario, enunciado: str, clave_criterio: str | None = None
    ) -> ReferenciaPregunta: ...

    @abstractmethod
    def agregar_opcion(
        self, pregunta: ReferenciaPregunta, texto: str, es_correcta: bool, justificacion: str | None
    ) -> None: ...

    @abstractmethod
    def establecer_rubrica(self, escala_max: Decimal) -> None: ...

    @abstractmethod
    def agregar_criterio(
        self, clave: str, descripcion: str, peso: Decimal, niveles: list[dict[str, Any]]
    ) -> None: ...

    @abstractmethod
    def construir(self) -> CasoAcademico: ...


class ConstructorCasoAcademico(ConstructorCaso):
    """Constructor concreto.

    Responsabilidades propias (que no son una simple llamada al ORM):
    - exige la secuencia válida de pasos (datos generales antes del contenido,
      rúbrica antes de sus criterios);
    - numera escenarios, preguntas y opciones según el orden de incorporación;
    - permite referenciar criterios por clave antes de declararlos y los resuelve
      al construir, rechazando claves desconocidas o repetidas;
    - fija siempre el estado inicial BORRADOR, sin importar lo que traiga la entrada.
    """

    def __init__(self) -> None:
        self._autor_id: uuid.UUID | None = None
        self._caso: CasoEstudio | None = None
        self._criterios: dict[str, CriterioRubrica] = {}
        self._pendientes: list[tuple[Pregunta, str]] = []

    def reiniciar(self, autor_id: uuid.UUID) -> None:
        self._autor_id = autor_id
        self._caso = None
        self._criterios = {}
        self._pendientes = []

    def establecer_datos_generales(
        self,
        nombre: str,
        desarrollo_situacional: str | None,
        historia: str | None,
        tiempo_estimado_min: int | None,
    ) -> None:
        if self._autor_id is None:
            raise ErrorConstruccionCaso("Debe reiniciarse el constructor con un autor antes de construir.")
        if self._caso is not None:
            raise ErrorConstruccionCaso("Los datos generales del caso ya fueron establecidos.")
        self._caso = CasoEstudio(
            autor_id=self._autor_id,
            nombre=nombre,
            desarrollo_situacional=desarrollo_situacional,
            historia=historia,
            tiempo_estimado_min=tiempo_estimado_min,
            estado=EstadoCasoEnum.BORRADOR,
        )

    def agregar_escenario(self, titulo: str, contexto: str | None) -> ReferenciaEscenario:
        caso = self._requiere_caso()
        caso.escenarios.append(Escenario(orden=len(caso.escenarios) + 1, titulo=titulo, contexto=contexto))
        return ReferenciaEscenario(len(caso.escenarios) - 1)

    def agregar_pregunta(
        self, escenario: ReferenciaEscenario, enunciado: str, clave_criterio: str | None = None
    ) -> ReferenciaPregunta:
        destino = self._escenario(escenario)
        pregunta = Pregunta(orden=len(destino.preguntas) + 1, enunciado=enunciado)
        destino.preguntas.append(pregunta)
        if clave_criterio is not None:
            self._pendientes.append((pregunta, clave_criterio))
        return ReferenciaPregunta(escenario, len(destino.preguntas) - 1)

    def agregar_opcion(
        self, pregunta: ReferenciaPregunta, texto: str, es_correcta: bool, justificacion: str | None
    ) -> None:
        destino = self._pregunta(pregunta)
        destino.opciones.append(
            Opcion(orden=len(destino.opciones) + 1, texto=texto, es_correcta=es_correcta, justificacion=justificacion)
        )

    def establecer_rubrica(self, escala_max: Decimal) -> None:
        caso = self._requiere_caso()
        if caso.rubrica is not None:
            raise ErrorConstruccionCaso("El caso solo puede tener una rúbrica.")
        caso.rubrica = Rubrica(escala_max=escala_max)

    def agregar_criterio(self, clave: str, descripcion: str, peso: Decimal, niveles: list[dict[str, Any]]) -> None:
        rubrica = self._requiere_caso().rubrica
        if rubrica is None:
            raise ErrorConstruccionCaso("Debe establecerse la rúbrica antes de agregar criterios.")
        if clave in self._criterios:
            raise ErrorConstruccionCaso(f"La clave de criterio '{clave}' está repetida.")
        criterio = CriterioRubrica(descripcion=descripcion, peso=peso, niveles=niveles)
        rubrica.criterios.append(criterio)
        self._criterios[clave] = criterio

    def construir(self) -> CasoAcademico:
        caso = self._requiere_caso()
        for pregunta, clave in self._pendientes:
            criterio = self._criterios.get(clave)
            if criterio is None:
                raise ErrorConstruccionCaso(f"La pregunta '{pregunta.enunciado[:40]}' referencia el criterio '{clave}', que no existe.")
            pregunta.criterio = criterio
        producto = CasoAcademico(caso)
        # El constructor queda limpio: un producto no puede modificarse por accidente con pasos posteriores.
        self.reiniciar(self._autor_id)  # type: ignore[arg-type]
        return producto

    # --- Auxiliares ---------------------------------------------------------

    def _requiere_caso(self) -> CasoEstudio:
        if self._caso is None:
            raise ErrorConstruccionCaso("Deben establecerse los datos generales antes de agregar contenido.")
        return self._caso

    def _escenario(self, referencia: ReferenciaEscenario) -> Escenario:
        escenarios = self._requiere_caso().escenarios
        if not 0 <= referencia.indice < len(escenarios):
            raise ErrorConstruccionCaso("La referencia de escenario no es válida.")
        return escenarios[referencia.indice]

    def _pregunta(self, referencia: ReferenciaPregunta) -> Pregunta:
        preguntas = self._escenario(referencia.escenario).preguntas
        if not 0 <= referencia.indice < len(preguntas):
            raise ErrorConstruccionCaso("La referencia de pregunta no es válida.")
        return preguntas[referencia.indice]
