"""Patrón Observer: publicación de eventos académicos.

Los servicios de sesiones y evaluación publican eventos; los observadores
(ObservadorConvocatorias, ObservadorAvisosAcademicos, pendientes) reaccionan
enviando las notificaciones.
"""

import enum
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class TipoEvento(str, enum.Enum):
    SESION_PROGRAMADA = "SESION_PROGRAMADA"
    ESTUDIANTES_MODIFICADOS = "ESTUDIANTES_MODIFICADOS"
    CALIFICACION_REGISTRADA = "CALIFICACION_REGISTRADA"
    RETROALIMENTACION_REGISTRADA = "RETROALIMENTACION_REGISTRADA"


@dataclass(frozen=True)
class EventoAcademico:
    tipo: TipoEvento
    # Id de la sesión (eventos de programación) o de la participación (de evaluación)
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

    def publicar(self, evento: EventoAcademico) -> None:
        for observador in self._observadores.get(evento.tipo, []):
            observador.actualizar(evento)


publicador_eventos = PublicadorEventos()
