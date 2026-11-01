"""Cierre automático de sesiones vencidas (lógica de negocio; la ejecución periódica vive en app/workers)."""

import logging
import uuid
from datetime import timedelta

from app.core.config import get_settings
from app.modules.sessions import eventos, integracion
from app.modules.sessions.services.base import ServicioSesionesBase
from app.modules.sessions.states import CicloVidaSesion

logger = logging.getLogger("iter_animi.sesiones.cierre")

TAMANO_LOTE = 500


def plazo_cierre() -> timedelta:
    """Regla configurable del sistema: SESSION_AUTO_CLOSE_HOURS (24 h por defecto) tras la hora de fin."""
    return timedelta(hours=get_settings().SESSION_AUTO_CLOSE_HOURS)


class CierreSesionesService(ServicioSesionesBase):
    def cerrar_vencidas(self, plazo: timedelta | None = None, lote: int = TAMANO_LOTE) -> list[uuid.UUID]:
        """Finaliza, mediante el patrón State, las sesiones SIN_INICIAR o EN_CURSO vencidas.

        - Cada sesión se procesa en su propia transacción.
        - `FOR UPDATE SKIP LOCKED`: dos ejecuciones simultáneas no procesan la misma sesión.
        - Tras el bloqueo se vuelve a consultar el estado: si ya está FINALIZADO, el State
          no hace nada (idempotente), así que nunca se finaliza dos veces.
        """
        plazo = plazo_cierre() if plazo is None else plazo
        ahora = self._reloj.ahora()
        cerradas: list[uuid.UUID] = []
        for sesion_id in self._sesiones.ids_vencidas(ahora - plazo, lote):
            with self._transaction():
                sesion = self._sesiones.get_for_update_skip_locked(sesion_id)
                if sesion is None:
                    continue  # la está procesando otra ejecución
                estado_anterior = sesion.estado.value
                if CicloVidaSesion(sesion).cerrar_por_vencimiento(ahora, plazo):
                    integracion.cierre_participaciones().cerrar_por_fin_de_sesion(self._session, sesion.id, ahora)
                    cerradas.append(sesion.id)
                    logger.info("sesion=%s cerrada automaticamente (estado anterior %s)", sesion.id, estado_anterior)
                    self._emitir(
                        eventos.EventoSesion(
                            eventos.SESION_FINALIZADA, sesion.id, {"origen": "cierre_automatico", "anterior": estado_anterior}
                        )
                    )
        return cerradas
