"""Acceso a datos del módulo 4.

Orden de bloqueo común a todo el sistema (evita interbloqueos):
    sesion  ->  convocatoria  ->  participacion
Los servicios del módulo 3 ya bloquean la sesión primero; los del módulo 4 respetan el
mismo orden. El worker de expiración solo bloquea filas de `participacion`.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app.modules.cases.models import CasoEstudio, Escenario, Opcion, Pregunta, Rubrica
from app.modules.participations.models import EstadoParticipacionEnum, Participacion, Respuesta
from app.modules.sessions.models import Convocatoria, Sesion

_CONTENIDO = (
    selectinload(CasoEstudio.escenarios).selectinload(Escenario.preguntas).selectinload(Pregunta.opciones),
    selectinload(CasoEstudio.rubrica).selectinload(Rubrica.criterios),
)


class ParticipacionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- Bloqueos (en el orden sesión -> convocatoria -> participación) --------------------

    def bloquear_sesion_compartida(self, sesion_id: uuid.UUID) -> Sesion | None:
        """FOR SHARE: compatible con otros estudiantes, pero espera a un cierre de sesión en curso."""
        stmt = select(Sesion).where(Sesion.id == sesion_id).with_for_update(read=True)
        return self._session.scalars(stmt.execution_options(populate_existing=True)).first()

    def bloquear_convocatoria(self, convocatoria_id: uuid.UUID, *, exclusiva: bool) -> Convocatoria | None:
        stmt = select(Convocatoria).where(Convocatoria.id == convocatoria_id).with_for_update(read=not exclusiva)
        return self._session.scalars(stmt.execution_options(populate_existing=True)).first()

    def bloquear_participacion(self, participacion_id: uuid.UUID, convocatoria_id: uuid.UUID) -> Participacion | None:
        stmt = (
            select(Participacion)
            .where(Participacion.id == participacion_id, Participacion.convocatoria_id == convocatoria_id)
            .with_for_update()
        )
        return self._session.scalars(stmt.execution_options(populate_existing=True)).first()

    def bloquear_activa(self, convocatoria_id: uuid.UUID) -> Participacion | None:
        stmt = (
            select(Participacion)
            .where(Participacion.convocatoria_id == convocatoria_id, Participacion.estado == EstadoParticipacionEnum.EN_PROGRESO)
            .with_for_update()
        )
        return self._session.scalars(stmt.execution_options(populate_existing=True)).first()

    def activa(self, convocatoria_id: uuid.UUID) -> Participacion | None:
        stmt = select(Participacion).where(
            Participacion.convocatoria_id == convocatoria_id, Participacion.estado == EstadoParticipacionEnum.EN_PROGRESO
        )
        return self._session.scalars(stmt).first()

    def bloquear_activas_de_sesion(self, sesion_id: uuid.UUID) -> list[Participacion]:
        stmt = (
            select(Participacion)
            .join(Convocatoria, Participacion.convocatoria_id == Convocatoria.id)
            .where(Convocatoria.sesion_id == sesion_id, Participacion.estado == EstadoParticipacionEnum.EN_PROGRESO)
            .order_by(Participacion.id)
            .with_for_update(of=Participacion)
        )
        return list(self._session.scalars(stmt.execution_options(populate_existing=True)))

    def bloquear_para_expirar(self, participacion_id: uuid.UUID) -> Participacion | None:
        """Worker: SKIP LOCKED (si otra transacción la tiene, se omite en esta pasada)."""
        stmt = select(Participacion).where(Participacion.id == participacion_id).with_for_update(skip_locked=True)
        return self._session.scalars(stmt.execution_options(populate_existing=True)).first()

    # --- Consultas ------------------------------------------------------------------------

    def get(self, participacion_id: uuid.UUID) -> Participacion | None:
        return self._session.get(Participacion, participacion_id)

    def de_convocatoria(self, convocatoria_id: uuid.UUID) -> list[Participacion]:
        stmt = select(Participacion).where(Participacion.convocatoria_id == convocatoria_id).order_by(Participacion.numero_intento)
        return list(self._session.scalars(stmt))

    def intentos_utilizados(self, convocatoria_id: uuid.UUID) -> int:
        """Todas las participaciones creadas (EN_PROGRESO, FINALIZADA y EXPIRADA). Nunca se reinicia."""
        stmt = select(func.count()).select_from(Participacion).where(Participacion.convocatoria_id == convocatoria_id)
        return self._session.scalar(stmt) or 0

    def tiempo_max_de(self, participacion: Participacion) -> int:
        stmt = (
            select(Sesion.tiempo_max_min)
            .join(Convocatoria, Convocatoria.sesion_id == Sesion.id)
            .where(Convocatoria.id == participacion.convocatoria_id)
        )
        return int(self._session.scalar(stmt))

    def ids_vencidas(self, ahora: datetime, limite: int) -> list[uuid.UUID]:
        """EN_PROGRESO con inicio + tiempo_max_min <= ahora (usa el índice parcial de activas)."""
        stmt = (
            select(Participacion.id)
            .join(Convocatoria, Participacion.convocatoria_id == Convocatoria.id)
            .join(Sesion, Convocatoria.sesion_id == Sesion.id)
            .where(
                Participacion.estado == EstadoParticipacionEnum.EN_PROGRESO,
                Participacion.inicio + func.make_interval(0, 0, 0, 0, 0, Sesion.tiempo_max_min) <= ahora,
            )
            .order_by(Participacion.inicio)
            .limit(limite)
        )
        return list(self._session.scalars(stmt))

    def caso_con_contenido(self, caso_id: uuid.UUID) -> CasoEstudio:
        stmt = select(CasoEstudio).where(CasoEstudio.id == caso_id).options(*_CONTENIDO)
        return self._session.scalars(stmt).one()

    def add(self, participacion: Participacion) -> None:
        self._session.add(participacion)
        self._session.flush()

    def flush(self) -> None:
        self._session.flush()


class RespuestaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def de_participacion(self, participacion_id: uuid.UUID) -> list[Respuesta]:
        stmt = select(Respuesta).where(Respuesta.participacion_id == participacion_id).order_by(Respuesta.respondida_en, Respuesta.id)
        return list(self._session.scalars(stmt))

    def contar(self, participacion_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Respuesta).where(Respuesta.participacion_id == participacion_id)
        return self._session.scalar(stmt) or 0

    def pregunta_del_caso(self, pregunta_id: uuid.UUID, caso_id: uuid.UUID) -> Pregunta | None:
        stmt = (
            select(Pregunta)
            .join(Escenario, Pregunta.escenario_id == Escenario.id)
            .where(Pregunta.id == pregunta_id, Escenario.caso_id == caso_id)
        )
        return self._session.scalars(stmt).first()

    def opcion_de_pregunta(self, opcion_id: uuid.UUID, pregunta_id: uuid.UUID) -> Opcion | None:
        stmt = select(Opcion).where(Opcion.id == opcion_id, Opcion.pregunta_id == pregunta_id)
        return self._session.scalars(stmt).first()

    def total_preguntas(self, caso_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Pregunta)
            .join(Escenario, Pregunta.escenario_id == Escenario.id)
            .where(Escenario.caso_id == caso_id)
        )
        return self._session.scalar(stmt) or 0

    def guardar(self, participacion_id: uuid.UUID, pregunta_id: uuid.UUID, opcion_id: uuid.UUID, ahora: datetime) -> None:
        """INSERT ... ON CONFLICT: una sola fila por (participación, pregunta), atómico ante reintentos.

        `respondida_en` conserva la primera respuesta; `actualizada_en` solo cambia si la
        opción realmente cambia (un reintento con la misma opción no es una modificación).
        """
        stmt = insert(Respuesta).values(
            participacion_id=participacion_id,
            pregunta_id=pregunta_id,
            opcion_id=opcion_id,
            respondida_en=ahora,
            actualizada_en=ahora,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_respuesta_participacion_pregunta",
            set_={"opcion_id": stmt.excluded.opcion_id, "actualizada_en": stmt.excluded.actualizada_en},
            where=Respuesta.opcion_id.is_distinct_from(stmt.excluded.opcion_id),
        )
        self._session.execute(stmt)

    def eliminar(self, participacion_id: uuid.UUID, pregunta_id: uuid.UUID) -> int:
        stmt = delete(Respuesta).where(Respuesta.participacion_id == participacion_id, Respuesta.pregunta_id == pregunta_id)
        return self._session.execute(stmt).rowcount or 0

    def get(self, participacion_id: uuid.UUID, pregunta_id: uuid.UUID) -> Respuesta | None:
        stmt = select(Respuesta).where(Respuesta.participacion_id == participacion_id, Respuesta.pregunta_id == pregunta_id)
        return self._session.scalars(stmt.execution_options(populate_existing=True)).first()


__all__ = ["ParticipacionRepository", "RespuestaRepository"]
