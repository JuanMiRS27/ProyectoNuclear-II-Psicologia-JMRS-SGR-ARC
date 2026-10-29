"""Consulta de solo lectura para el módulo 5 (Evaluación y Retroalimentación).

No calcula notas ni escribe nada: entrega a la futura Strategy de evaluación todo lo que
necesita sobre una participación, sin que tenga que conocer la persistencia del módulo 4.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.modules.cases.models import CasoEstudio, Rubrica
from app.modules.participations.models import EstadoParticipacionEnum, MotivoCierreEnum, Participacion
from app.modules.participations.repositories import ParticipacionRepository, RespuestaRepository
from app.modules.sessions.models import Convocatoria, Sesion


@dataclass(frozen=True, slots=True)
class ParticipacionParaEvaluacion:
    participacion_id: uuid.UUID
    convocatoria_id: uuid.UUID
    estudiante_id: uuid.UUID
    sesion_id: uuid.UUID
    numero_intento: int
    estado: EstadoParticipacionEnum
    motivo_cierre: MotivoCierreEnum | None
    # pregunta_id -> opcion_id elegida
    respuestas: dict[uuid.UUID, uuid.UUID]
    preguntas_sin_responder: list[uuid.UUID]
    caso: CasoEstudio  # con escenarios, preguntas (con criterio_id), opciones y rúbrica cargados
    rubrica: Rubrica | None = field(default=None)

    @property
    def completa_voluntariamente(self) -> bool:
        return self.estado == EstadoParticipacionEnum.FINALIZADA


class ConsultaEvaluacionService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._participaciones = ParticipacionRepository(session)
        self._respuestas = RespuestaRepository(session)

    def obtener(self, participacion_id: uuid.UUID) -> ParticipacionParaEvaluacion:
        participacion = self._participaciones.get(participacion_id)
        if participacion is None:
            raise NotFoundError("Participación no encontrada.")
        convocatoria: Convocatoria = participacion.convocatoria
        sesion: Sesion = convocatoria.sesion
        caso = self._participaciones.caso_con_contenido(sesion.caso_id)
        respuestas = {r.pregunta_id: r.opcion_id for r in self._respuestas.de_participacion(participacion.id)}
        preguntas = [p.id for p in caso.preguntas]
        return ParticipacionParaEvaluacion(
            participacion_id=participacion.id,
            convocatoria_id=convocatoria.id,
            estudiante_id=convocatoria.estudiante_id,
            sesion_id=sesion.id,
            numero_intento=participacion.numero_intento,
            estado=participacion.estado,
            motivo_cierre=participacion.motivo_cierre,
            respuestas=respuestas,
            preguntas_sin_responder=[p for p in preguntas if p not in respuestas],
            caso=caso,
            rubrica=caso.rubrica,
        )

    def cerradas_de_sesion(self, sesion_id: uuid.UUID) -> list[uuid.UUID]:
        """Participaciones FINALIZADAS o EXPIRADAS de una sesión (las que el módulo 5 puede evaluar)."""
        stmt = (
            select(Participacion.id)
            .join(Convocatoria, Participacion.convocatoria_id == Convocatoria.id)
            .where(Convocatoria.sesion_id == sesion_id, Participacion.estado != EstadoParticipacionEnum.EN_PROGRESO)
            .order_by(Convocatoria.estudiante_id, Participacion.numero_intento)
        )
        return list(self._session.scalars(stmt))

    def ausencias(self, sesion_id: uuid.UUID) -> list[Convocatoria]:
        """Convocatorias activas SIN ningún intento: la ausencia no se representa con participaciones ficticias."""
        sin_intentos = ~select(Participacion.id).where(Participacion.convocatoria_id == Convocatoria.id).exists()
        stmt = select(Convocatoria).where(
            Convocatoria.sesion_id == sesion_id, Convocatoria.revocada_en.is_(None), sin_intentos
        )
        return list(self._session.scalars(stmt))
