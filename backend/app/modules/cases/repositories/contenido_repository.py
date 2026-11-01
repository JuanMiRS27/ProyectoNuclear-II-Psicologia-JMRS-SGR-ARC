import uuid
from collections.abc import Sequence
from typing import Protocol

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.modules.cases.models import CriterioRubrica, Escenario, Opcion, Pregunta, Rubrica


class _Ordenable(Protocol):
    orden: int


class ContenidoCasoRepository:
    """Acceso a los componentes de un caso. Cada búsqueda exige el caso padre para validar pertenencia."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- Escenarios -----------------------------------------------------------

    def get_escenario(self, caso_id: uuid.UUID, escenario_id: uuid.UUID) -> Escenario | None:
        stmt = (
            select(Escenario)
            .where(Escenario.id == escenario_id, Escenario.caso_id == caso_id)
            .options(selectinload(Escenario.preguntas).selectinload(Pregunta.opciones))
        )
        return self._session.scalars(stmt).first()

    def list_escenarios(self, caso_id: uuid.UUID) -> list[Escenario]:
        stmt = (
            select(Escenario)
            .where(Escenario.caso_id == caso_id)
            .order_by(Escenario.orden)
            .options(selectinload(Escenario.preguntas).selectinload(Pregunta.opciones))
        )
        return list(self._session.scalars(stmt))

    def next_orden_escenario(self, caso_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(Escenario.orden), 0)).where(Escenario.caso_id == caso_id)
        return int(self._session.scalar(stmt) or 0) + 1

    # --- Preguntas ------------------------------------------------------------

    def get_pregunta(self, caso_id: uuid.UUID, pregunta_id: uuid.UUID) -> Pregunta | None:
        stmt = (
            select(Pregunta)
            .join(Escenario, Pregunta.escenario_id == Escenario.id)
            .where(Pregunta.id == pregunta_id, Escenario.caso_id == caso_id)
            .options(selectinload(Pregunta.opciones))
        )
        return self._session.scalars(stmt).first()

    def list_preguntas(self, escenario_id: uuid.UUID) -> list[Pregunta]:
        stmt = (
            select(Pregunta)
            .where(Pregunta.escenario_id == escenario_id)
            .order_by(Pregunta.orden)
            .options(selectinload(Pregunta.opciones))
        )
        return list(self._session.scalars(stmt))

    def next_orden_pregunta(self, escenario_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(Pregunta.orden), 0)).where(Pregunta.escenario_id == escenario_id)
        return int(self._session.scalar(stmt) or 0) + 1

    # --- Opciones -------------------------------------------------------------

    def get_opcion(self, pregunta_id: uuid.UUID, opcion_id: uuid.UUID) -> Opcion | None:
        stmt = select(Opcion).where(Opcion.id == opcion_id, Opcion.pregunta_id == pregunta_id)
        return self._session.scalars(stmt).first()

    def list_opciones(self, pregunta_id: uuid.UUID) -> list[Opcion]:
        stmt = select(Opcion).where(Opcion.pregunta_id == pregunta_id).order_by(Opcion.orden)
        return list(self._session.scalars(stmt))

    def next_orden_opcion(self, pregunta_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(Opcion.orden), 0)).where(Opcion.pregunta_id == pregunta_id)
        return int(self._session.scalar(stmt) or 0) + 1

    # --- Rúbrica --------------------------------------------------------------

    def get_rubrica(self, caso_id: uuid.UUID) -> Rubrica | None:
        stmt = select(Rubrica).where(Rubrica.caso_id == caso_id).options(selectinload(Rubrica.criterios))
        return self._session.scalars(stmt).first()

    def get_criterio(self, caso_id: uuid.UUID, criterio_id: uuid.UUID) -> CriterioRubrica | None:
        stmt = (
            select(CriterioRubrica)
            .join(Rubrica, CriterioRubrica.rubrica_id == Rubrica.id)
            .where(CriterioRubrica.id == criterio_id, Rubrica.caso_id == caso_id)
        )
        return self._session.scalars(stmt).first()

    # --- Operaciones comunes --------------------------------------------------

    def add(self, entidad: object) -> None:
        self._session.add(entidad)
        self._session.flush()

    def delete(self, entidad: object) -> None:
        self._session.delete(entidad)
        self._session.flush()

    def flush(self) -> None:
        self._session.flush()

    def aplicar_orden(self, elementos: Sequence[_Ordenable]) -> None:
        """Asigna órdenes 1..n según la secuencia dada.

        Las restricciones únicas de orden son DEFERRABLE: se difieren al commit para
        que los intercambios intermedios (p. ej., 1<->2) no violen la unicidad.
        """
        self._session.execute(
            text("SET CONSTRAINTS uq_escenario_caso_orden, uq_pregunta_escenario_orden, uq_opcion_pregunta_orden DEFERRED")
        )
        for posicion, elemento in enumerate(elementos, start=1):
            elemento.orden = posicion
        self._session.flush()
