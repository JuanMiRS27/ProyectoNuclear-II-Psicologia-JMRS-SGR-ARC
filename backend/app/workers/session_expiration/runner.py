"""Ejecución del cierre automático, separada de la lógica de negocio (`CierreSesionesService`)."""

import logging
import time
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from app.bootstrap import configurar_integraciones
from app.core.clock import Reloj, RelojSistema
from app.core.database import SessionLocal
from app.modules.sessions.services import CierreSesionesService

logger = logging.getLogger("iter_animi.workers.session_expiration")


def ejecutar_una_vez(
    session_factory: Callable[[], Session] = SessionLocal,
    reloj: Reloj | None = None,
    plazo: timedelta | None = None,
) -> list[uuid.UUID]:
    """Una pasada del cierre automático. No depende de peticiones HTTP."""
    configurar_integraciones()  # cerrar sesiones también cierra sus participaciones activas (módulo 4)
    with session_factory() as session:
        cerradas = CierreSesionesService(session, reloj or RelojSistema()).cerrar_vencidas(plazo)
    logger.info("cierre automatico: %d sesion(es) finalizada(s)", len(cerradas))
    return cerradas


def ejecutar_periodicamente(intervalo_segundos: int, reloj: Reloj | None = None) -> None:
    """Bucle simple para entornos sin planificador externo. Un error en una pasada no detiene el bucle."""
    while True:
        try:
            ejecutar_una_vez(reloj=reloj)
        except Exception:  # noqa: BLE001 - se registra y se reintenta en la siguiente pasada
            logger.exception("fallo en la pasada de cierre automatico")
        time.sleep(intervalo_segundos)
