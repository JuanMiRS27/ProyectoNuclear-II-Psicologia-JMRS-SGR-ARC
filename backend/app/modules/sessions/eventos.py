"""Punto de integración con el módulo 6 (Notificaciones y Eventos).

Los servicios del módulo 3 publican hechos de dominio a través del puerto
`PublicadorEventosSesion`, DESPUÉS de confirmar la transacción. Aquí solo existe
la implementación nula (registra el evento en el log): el módulo 6 aportará la
implementación real (Observer, Adapter de correo, etc.) sin modificar estos servicios.

Los eventos nunca transportan códigos de autorización: el módulo 6 obtendrá el
código de la convocatoria por su id, dentro de su propio flujo de envío.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger("inter_nos.sesiones.eventos")


@dataclass(frozen=True, slots=True)
class EventoSesion:
    tipo: str
    sesion_id: uuid.UUID
    datos: dict[str, str] = field(default_factory=dict)


# Tipos de evento publicados por el módulo 3.
SESION_PROGRAMADA = "SESION_PROGRAMADA"
SESION_MODIFICADA = "SESION_MODIFICADA"
SESION_INICIADA = "SESION_INICIADA"
SESION_FINALIZADA = "SESION_FINALIZADA"
CONVOCATORIA_CREADA = "CONVOCATORIA_CREADA"
CONVOCATORIA_RETIRADA = "CONVOCATORIA_RETIRADA"
CONVOCATORIA_REACTIVADA = "CONVOCATORIA_REACTIVADA"
CODIGO_REGENERADO = "CODIGO_REGENERADO"
INTENTOS_MODIFICADOS = "INTENTOS_MODIFICADOS"


class PublicadorEventosSesion(Protocol):
    def publicar(self, evento: EventoSesion) -> None: ...


class PublicadorNulo:
    """Implementación por defecto: no envía nada; deja rastro en el log (sin datos sensibles)."""

    def publicar(self, evento: EventoSesion) -> None:
        logger.info("evento=%s sesion=%s datos=%s", evento.tipo, evento.sesion_id, evento.datos)


def evento_convocatoria(tipo: str, sesion_id: uuid.UUID, convocatoria_id: uuid.UUID, **extra: str) -> EventoSesion:
    return EventoSesion(tipo, sesion_id, {"convocatoria_id": str(convocatoria_id), **extra})

