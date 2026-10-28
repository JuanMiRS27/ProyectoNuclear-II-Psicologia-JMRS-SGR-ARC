"""Patrón Observer: publicación de eventos académicos.

Los servicios de sesiones y evaluación publican eventos; los observadores
(ObservadorConvocatorias, ObservadorAvisosAcademicos) reaccionan enviando las
notificaciones. Los eventos se publican DESPUÉS del commit, porque los
observadores leen los datos con su propia sesión de BD.

Qué lleva cada evento:
- SESION_PROGRAMADA: entidad_id = id de la sesión. Se avisa a todas sus convocatorias.
- ESTUDIANTES_MODIFICADOS: entidad_id = id de la sesión,
  datos = {"convocatoria_ids": [...]} con las convocatorias a avisar.
- CALIFICACION_REGISTRADA: entidad_id = id de la participación.
- RETROALIMENTACION_REGISTRADA: entidad_id = id de la participación.
"""

import enum
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class TipoEvento(str, enum.Enum):
    SESION_PROGRAMADA = "SESION_PROGRAMADA"
    ESTUDIANTES_MODIFICADOS = "ESTUDIANTES_MODIFICADOS"
    CALIFICACION_REGISTRADA = "CALIFICACION_REGISTRADA"
    RETROALIMENTACION_REGISTRADA = "RETROALIMENTACION_REGISTRADA"


@dataclass(frozen=True)
class EventoAcademico:
    tipo: TipoEvento
    entidad_id: uuid.UUID
    datos: dict = field(default_factory=dict)


class ObservadorEvento(ABC):
    @abstractmethod
    def actualizar(self, evento: EventoAcademico) -> None: ...


class PublicadorEventos:
    def __init__(self) -> None:
        self._observadores: dict[TipoEvento, list[ObservadorEvento]] = {}

    def suscribir(self, tipo: TipoEvento, observador: ObservadorEvento) -> None:
        self._observadores.setdefault(tipo, []).append(observador)

    def limpiar(self) -> None:
        self._observadores.clear()

    def publicar(self, evento: EventoAcademico) -> None:
        # Un observador que falla no impide que los demás reciban el evento
        for observador in self._observadores.get(evento.tipo, []):
            try:
                observador.actualizar(evento)
            except Exception:
                logger.exception(
                    "El observador %s falló con el evento %s",
                    type(observador).__name__,
                    evento.tipo.value,
                )


publicador_eventos = PublicadorEventos()
