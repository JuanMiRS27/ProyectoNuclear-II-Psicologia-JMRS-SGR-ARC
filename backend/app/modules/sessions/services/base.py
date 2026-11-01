import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Reloj, RelojSistema
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.auth.models import Usuario
from app.modules.sessions.eventos import EventoSesion, PublicadorEventosSesion, PublicadorNulo
from app.modules.sessions.models import Sesion
from app.modules.sessions.repositories import (
    ConvocatoriaRepository,
    EspacioRepository,
    EstudianteRepository,
    SesionRepository,
)

SESION_NO_ENCONTRADA = "Sesión no encontrada."

_RESTRICCIONES = {
    "uq_espacio_academico_nombre_periodo": (ConflictError, "Ya existe un espacio académico con ese nombre y periodo."),
    "uq_convocatoria_sesion_estudiante": (ConflictError, "El estudiante ya está convocado a esta sesión."),
    "ck_sesion_caso_publicado": (ConflictError, "Solo se pueden programar sesiones con casos publicados."),
    "ck_sesion_tiempo_max_hasta_estimado": (BusinessRuleError, "El tiempo máximo supera el tiempo estimado del caso."),
    "ck_sesion_responsable_docente": (BusinessRuleError, "El responsable de la sesión debe ser un docente."),
    "ck_sesion_inicio_antes_de_fin": (BusinessRuleError, "La hora de inicio debe ser anterior a la de finalización."),
    "ck_convocatoria_estudiante_rol": (BusinessRuleError, "Solo se pueden convocar usuarios con rol ESTUDIANTE."),
}


class ServicioSesionesBase:
    """Comportamiento común del módulo 3.

    - El tiempo actual procede siempre del `Reloj` inyectado (nunca del cliente).
    - Una sesión de otro docente responde 404, igual que una inexistente.
    - Las escrituras bloquean la fila de la sesión y se confirman o revierten enteras.
    - Los eventos para el módulo 6 se publican solo después del commit.
    """

    def __init__(
        self, session: Session, reloj: Reloj | None = None, eventos: PublicadorEventosSesion | None = None
    ) -> None:
        self._session = session
        self._reloj = reloj or RelojSistema()
        self._eventos = eventos or PublicadorNulo()
        self._espacios = EspacioRepository(session)
        self._sesiones = SesionRepository(session)
        self._convocatorias = ConvocatoriaRepository(session)
        self._estudiantes = EstudianteRepository(session)
        self._pendientes: list[EventoSesion] = []

    def _sesion_lectura(self, sesion_id: uuid.UUID, docente: Usuario) -> Sesion:
        sesion = self._sesiones.get_owned(sesion_id, docente.id)
        if sesion is None:
            raise NotFoundError(SESION_NO_ENCONTRADA)
        return sesion

    def _sesion_bloqueada(self, sesion_id: uuid.UUID, docente: Usuario) -> Sesion:
        sesion = self._sesiones.get_owned(sesion_id, docente.id, for_update=True)
        if sesion is None:
            raise NotFoundError(SESION_NO_ENCONTRADA)
        return sesion

    def _emitir(self, evento: EventoSesion) -> None:
        self._pendientes.append(evento)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            yield
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            self._pendientes.clear()
            raise _traducir(exc) from exc
        except BaseException:
            self._session.rollback()
            self._pendientes.clear()
            raise
        pendientes, self._pendientes = self._pendientes, []
        for evento in pendientes:
            self._eventos.publicar(evento)


def _traducir(exc: IntegrityError) -> Exception:
    nombre = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if nombre in _RESTRICCIONES:
        tipo, mensaje = _RESTRICCIONES[nombre]
        return tipo(mensaje)
    return BusinessRuleError("Los datos no cumplen las restricciones de integridad.")
