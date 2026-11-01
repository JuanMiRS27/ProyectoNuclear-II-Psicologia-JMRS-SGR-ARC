"""Edición individual de los componentes de un borrador (escenarios, preguntas, opciones)."""

import uuid

from app.core.exceptions import BusinessRuleError, NotFoundError
from app.modules.auth.models import Usuario
from app.modules.cases.models import Escenario, Opcion, Pregunta
from app.modules.cases.schemas import (
    EscenarioCreate,
    EscenarioUpdate,
    OpcionCreate,
    OpcionUpdate,
    PreguntaCreate,
    PreguntaUpdate,
)
from app.modules.cases.services.base import ServicioCasoBase


class EscenarioService(ServicioCasoBase):
    def listar(self, autor: Usuario, caso_id: uuid.UUID) -> list[Escenario]:
        self._caso_lectura(caso_id, autor)
        return self._contenido.list_escenarios(caso_id)

    def obtener(self, autor: Usuario, caso_id: uuid.UUID, escenario_id: uuid.UUID) -> Escenario:
        self._caso_lectura(caso_id, autor)
        return self._escenario(caso_id, escenario_id)

    def crear(self, autor: Usuario, caso_id: uuid.UUID, data: EscenarioCreate) -> Escenario:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            orden = data.orden or self._contenido.next_orden_escenario(caso_id)
            escenario = Escenario(caso_id=caso_id, orden=orden, titulo=data.titulo, contexto=data.contexto)
            self._contenido.add(escenario)
        return self._escenario(caso_id, escenario.id)

    def actualizar(self, autor: Usuario, caso_id: uuid.UUID, escenario_id: uuid.UUID, data: EscenarioUpdate) -> Escenario:
        cambios = data.model_dump(exclude_unset=True)
        if "titulo" in cambios and cambios["titulo"] is None:
            raise BusinessRuleError("El título del escenario no puede quedar vacío.")
        with self._transaction():
            self._caso_editable(caso_id, autor)
            escenario = self._escenario(caso_id, escenario_id)
            for campo, valor in cambios.items():
                setattr(escenario, campo, valor)
        return self._escenario(caso_id, escenario_id)

    def eliminar(self, autor: Usuario, caso_id: uuid.UUID, escenario_id: uuid.UUID) -> None:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._contenido.delete(self._escenario(caso_id, escenario_id))
            self._compactar(self._contenido.list_escenarios(caso_id))

    def reordenar(self, autor: Usuario, caso_id: uuid.UUID, ids: list[uuid.UUID]) -> list[Escenario]:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._reordenar(self._contenido.list_escenarios(caso_id), ids)
        return self._contenido.list_escenarios(caso_id)

    def _escenario(self, caso_id: uuid.UUID, escenario_id: uuid.UUID) -> Escenario:
        escenario = self._contenido.get_escenario(caso_id, escenario_id)
        if escenario is None:
            raise NotFoundError("Escenario no encontrado en este caso.")
        return escenario


class PreguntaService(ServicioCasoBase):
    def listar(self, autor: Usuario, caso_id: uuid.UUID, escenario_id: uuid.UUID) -> list[Pregunta]:
        self._caso_lectura(caso_id, autor)
        self._escenario(caso_id, escenario_id)
        return self._contenido.list_preguntas(escenario_id)

    def obtener(self, autor: Usuario, caso_id: uuid.UUID, pregunta_id: uuid.UUID) -> Pregunta:
        self._caso_lectura(caso_id, autor)
        return self._pregunta(caso_id, pregunta_id)

    def crear(self, autor: Usuario, caso_id: uuid.UUID, escenario_id: uuid.UUID, data: PreguntaCreate) -> Pregunta:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._escenario(caso_id, escenario_id)
            self._verificar_criterio(caso_id, data.criterio_id)
            orden = data.orden or self._contenido.next_orden_pregunta(escenario_id)
            pregunta = Pregunta(
                escenario_id=escenario_id, orden=orden, enunciado=data.enunciado, criterio_id=data.criterio_id
            )
            self._contenido.add(pregunta)
        return self._pregunta(caso_id, pregunta.id)

    def actualizar(self, autor: Usuario, caso_id: uuid.UUID, pregunta_id: uuid.UUID, data: PreguntaUpdate) -> Pregunta:
        cambios = data.model_dump(exclude_unset=True)
        if "enunciado" in cambios and cambios["enunciado"] is None:
            raise BusinessRuleError("El enunciado de la pregunta no puede quedar vacío.")
        with self._transaction():
            self._caso_editable(caso_id, autor)
            pregunta = self._pregunta(caso_id, pregunta_id)
            if "criterio_id" in cambios:
                self._verificar_criterio(caso_id, cambios["criterio_id"])
            for campo, valor in cambios.items():
                setattr(pregunta, campo, valor)
        return self._pregunta(caso_id, pregunta_id)

    def eliminar(self, autor: Usuario, caso_id: uuid.UUID, pregunta_id: uuid.UUID) -> None:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            pregunta = self._pregunta(caso_id, pregunta_id)
            escenario_id = pregunta.escenario_id
            self._contenido.delete(pregunta)
            self._compactar(self._contenido.list_preguntas(escenario_id))

    def reordenar(self, autor: Usuario, caso_id: uuid.UUID, escenario_id: uuid.UUID, ids: list[uuid.UUID]) -> list[Pregunta]:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._escenario(caso_id, escenario_id)
            self._reordenar(self._contenido.list_preguntas(escenario_id), ids)
        return self._contenido.list_preguntas(escenario_id)

    def _verificar_criterio(self, caso_id: uuid.UUID, criterio_id: uuid.UUID | None) -> None:
        # El disparador de la base de datos lo garantiza; aquí se da un mensaje claro antes de intentarlo.
        if criterio_id is not None and self._contenido.get_criterio(caso_id, criterio_id) is None:
            raise BusinessRuleError("El criterio no pertenece a la rúbrica de este caso.")

    def _escenario(self, caso_id: uuid.UUID, escenario_id: uuid.UUID) -> Escenario:
        escenario = self._contenido.get_escenario(caso_id, escenario_id)
        if escenario is None:
            raise NotFoundError("Escenario no encontrado en este caso.")
        return escenario

    def _pregunta(self, caso_id: uuid.UUID, pregunta_id: uuid.UUID) -> Pregunta:
        pregunta = self._contenido.get_pregunta(caso_id, pregunta_id)
        if pregunta is None:
            raise NotFoundError("Pregunta no encontrada en este caso.")
        return pregunta


class OpcionService(ServicioCasoBase):
    def listar(self, autor: Usuario, caso_id: uuid.UUID, pregunta_id: uuid.UUID) -> list[Opcion]:
        self._caso_lectura(caso_id, autor)
        self._pregunta(caso_id, pregunta_id)
        return self._contenido.list_opciones(pregunta_id)

    def crear(self, autor: Usuario, caso_id: uuid.UUID, pregunta_id: uuid.UUID, data: OpcionCreate) -> Opcion:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._pregunta(caso_id, pregunta_id)
            opcion = Opcion(pregunta_id=pregunta_id, orden=self._contenido.next_orden_opcion(pregunta_id), **data.model_dump())
            self._contenido.add(opcion)
        return opcion

    def actualizar(
        self, autor: Usuario, caso_id: uuid.UUID, pregunta_id: uuid.UUID, opcion_id: uuid.UUID, data: OpcionUpdate
    ) -> Opcion:
        cambios = data.model_dump(exclude_unset=True)
        for campo in ("texto", "es_correcta"):
            if campo in cambios and cambios[campo] is None:
                raise BusinessRuleError(f"El campo '{campo}' de la opción no puede quedar vacío.")
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._pregunta(caso_id, pregunta_id)
            opcion = self._opcion(pregunta_id, opcion_id)
            for campo, valor in cambios.items():
                setattr(opcion, campo, valor)
        return opcion

    def eliminar(self, autor: Usuario, caso_id: uuid.UUID, pregunta_id: uuid.UUID, opcion_id: uuid.UUID) -> None:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._pregunta(caso_id, pregunta_id)
            self._contenido.delete(self._opcion(pregunta_id, opcion_id))
            self._compactar(self._contenido.list_opciones(pregunta_id))

    def reordenar(self, autor: Usuario, caso_id: uuid.UUID, pregunta_id: uuid.UUID, ids: list[uuid.UUID]) -> list[Opcion]:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._pregunta(caso_id, pregunta_id)
            self._reordenar(self._contenido.list_opciones(pregunta_id), ids)
        return self._contenido.list_opciones(pregunta_id)

    def _pregunta(self, caso_id: uuid.UUID, pregunta_id: uuid.UUID) -> Pregunta:
        pregunta = self._contenido.get_pregunta(caso_id, pregunta_id)
        if pregunta is None:
            raise NotFoundError("Pregunta no encontrada en este caso.")
        return pregunta

    def _opcion(self, pregunta_id: uuid.UUID, opcion_id: uuid.UUID) -> Opcion:
        opcion = self._contenido.get_opcion(pregunta_id, opcion_id)
        if opcion is None:
            raise NotFoundError("Opción no encontrada en esta pregunta.")
        return opcion
