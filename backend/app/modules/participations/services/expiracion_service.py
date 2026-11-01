"""Expiración automática de participaciones vencidas (la ejecución periódica vive en app/workers)."""

import logging
import uuid

from app.modules.participations.policies import temporizador
from app.modules.participations.services.base import ServicioParticipacionesBase

logger = logging.getLogger("iter_animi.participaciones.expiracion")

TAMANO_LOTE = 500


class ExpiracionParticipacionesService(ServicioParticipacionesBase):
    def expirar_vencidas(self, lote: int = TAMANO_LOTE) -> list[uuid.UUID]:
        """EN_PROGRESO con `inicio + tiempo_max_min <= ahora` pasan a EXPIRADA (TIEMPO_AGOTADO).

        - `fin = fecha_limite`: el instante real en que se agotó el tiempo, no el de la detección.
        - Una transacción por participación, con `FOR UPDATE SKIP LOCKED`: dos workers (o un
          worker y una petición del estudiante) nunca cierran dos veces la misma.
        - Tras el bloqueo se revalida el estado: si ya está cerrada, no se toca (idempotente).
        - Las respuestas no se modifican.
        """
        ahora = self._reloj.ahora()
        expiradas: list[uuid.UUID] = []
        for participacion_id in self._participaciones.ids_vencidas(ahora, lote):
            with self._transaction():
                participacion = self._participaciones.bloquear_para_expirar(participacion_id)
                if participacion is None or not participacion.activa:
                    continue
                tiempo_max = self._participaciones.tiempo_max_de(participacion)
                # Revalidación con la hora posterior al bloqueo (ver Contexto en base.py).
                if temporizador.vencida(participacion, tiempo_max, self._reloj.ahora()):
                    temporizador.expirar_por_tiempo(participacion, tiempo_max)
                    expiradas.append(participacion.id)
                    logger.info("participacion=%s expirada por tiempo", participacion.id)
        return expiradas
