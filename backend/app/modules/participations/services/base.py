import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Reloj, RelojSistema
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.modules.participations.models import Participacion
from app.modules.participations.policies import temporizador
from app.modules.participations.repositories import ParticipacionRepository, RespuestaRepository
from app.modules.participations.schemas import ParticipacionRead, ProgresoRead
from app.modules.sessions.models import Convocatoria, Sesion
from app.modules.sessions.services import AccesoEstudiantil
from app.modules.sessions.states import CicloVidaSesion

PARTICIPACION_NO_ENCONTRADA = "Participación no encontrada."

_RESTRICCIONES = {
    "uq_participacion_activa_por_convocatoria": (ConflictError, "Ya existe una participación en progreso para esta convocatoria."),
    "uq_participacion_convocatoria_intento": (ConflictError, "Ese número de intento ya fue registrado."),
    "ck_participacion_intentos_autorizados": (ConflictError, "Se agotaron los intentos autorizados."),
    "ck_participacion_convocatoria_activa": (PermissionDeniedError, "La convocatoria fue revocada."),
    "ck_participacion_sesion_en_curso": (ConflictError, "La sesión no está en curso."),
    "ck_participacion_cerrada_inmutable": (ConflictError, "La participación está cerrada y no admite cambios."),
    "ck_respuesta_participacion_en_progreso": (ConflictError, "La participación está cerrada y no admite cambios."),
    "ck_respuesta_pregunta_del_caso": (NotFoundError, "La pregunta no pertenece a este cuestionario."),
    "fk_respuesta_opcion_de_la_pregunta": (BusinessRuleError, "La opción no pertenece a la pregunta."),
}


@dataclass(slots=True)
class Contexto:
    """Filas bloqueadas para una operación, en el orden sesión -> convocatoria -> participación.

    `ahora` se lee DESPUÉS de obtener los bloqueos: si la solicitud esperó a otra transacción,
    el vencimiento se evalúa con la hora en que realmente se ejecuta, no con la de llegada.
    """

    sesion: Sesion
    convocatoria: Convocatoria
    ahora: datetime
    participacion: Participacion | None = None


class ServicioParticipacionesBase:
    def __init__(self, session: Session, reloj: Reloj | None = None) -> None:
        self._session = session
        self._reloj = reloj or RelojSistema()
        self._participaciones = ParticipacionRepository(session)
        self._respuestas = RespuestaRepository(session)

    # --- Bloqueos ----------------------------------------------------------------------------

    def _bloquear(self, acceso: AccesoEstudiantil, participacion_id: uuid.UUID | None = None, *, convocatoria_exclusiva: bool = False) -> Contexto:
        """Bloquea en orden fijo y revalida lo que pudo cambiar desde que se resolvió el token.

        La sesión se bloquea en modo compartido: muchos estudiantes operan a la vez, pero un
        cierre de sesión (que la bloquea en exclusiva) espera a que terminen o se adelanta.
        """
        sesion = self._participaciones.bloquear_sesion_compartida(acceso.sesion.id)
        convocatoria = self._participaciones.bloquear_convocatoria(acceso.convocatoria.id, exclusiva=convocatoria_exclusiva)
        if sesion is None or convocatoria is None:
            raise NotFoundError(PARTICIPACION_NO_ENCONTRADA)
        if not convocatoria.activa:
            raise PermissionDeniedError("La convocatoria fue revocada.")
        if not CicloVidaSesion(sesion).mantiene_accesos():
            raise ConflictError("La sesión ya no está en curso.")
        participacion = None
        if participacion_id is not None:
            # Solo participaciones de la convocatoria del token: las ajenas responden 404.
            participacion = self._participaciones.bloquear_participacion(participacion_id, convocatoria.id)
            if participacion is None:
                raise NotFoundError(PARTICIPACION_NO_ENCONTRADA)
        return Contexto(sesion, convocatoria, self._reloj.ahora(), participacion)

    # --- Vista ---------------------------------------------------------------------------------

    def _progreso(self, participacion: Participacion, caso_id: uuid.UUID) -> ProgresoRead:
        total = self._respuestas.total_preguntas(caso_id)
        respondidas = self._respuestas.contar(participacion.id)
        return ProgresoRead(
            total_preguntas=total,
            respondidas=respondidas,
            pendientes=total - respondidas,
            porcentaje=(respondidas * 100 // total) if total else 0,
        )

    def _leer(self, participacion: Participacion, sesion: Sesion, convocatoria: Convocatoria, ahora: datetime) -> ParticipacionRead:
        return ParticipacionRead(
            id=participacion.id,
            convocatoria_id=convocatoria.id,
            sesion_id=sesion.id,
            numero_intento=participacion.numero_intento,
            estado=participacion.estado,
            motivo_cierre=participacion.motivo_cierre,
            inicio=participacion.inicio,
            fin=participacion.fin,
            fecha_limite=temporizador.fecha_limite(participacion, sesion.tiempo_max_min),
            tiempo_restante_segundos=temporizador.tiempo_restante_segundos(participacion, sesion.tiempo_max_min, ahora),
            intentos_autorizados=convocatoria.intentos_autorizados,
            intentos_utilizados=self._participaciones.intentos_utilizados(convocatoria.id),
            progreso=self._progreso(participacion, sesion.caso_id),
        )

    # --- Transacciones ---------------------------------------------------------------------------

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            yield
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise _traducir(exc) from exc
        except BaseException:
            self._session.rollback()
            raise


def _traducir(exc: IntegrityError) -> Exception:
    nombre = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if nombre in _RESTRICCIONES:
        tipo, mensaje = _RESTRICCIONES[nombre]
        return tipo(mensaje)
    return BusinessRuleError("Los datos no cumplen las restricciones de integridad.")
