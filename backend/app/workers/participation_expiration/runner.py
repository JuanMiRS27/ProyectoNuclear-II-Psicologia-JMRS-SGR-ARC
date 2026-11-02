"""Ejecución de la expiración de participaciones, separada de la lógica (`ExpiracionParticipacionesService`)."""

import logging
import time
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.bootstrap import configurar_integraciones
from app.core.clock import Reloj, RelojSistema
from app.core.database import SessionLocal
from app.modules.participations.services import ExpiracionParticipacionesService

logger = logging.getLogger("inter_nos.workers.participation_expiration")


def ejecutar_una_vez(session_factory: Callable[[], Session] = SessionLocal, reloj: Reloj | None = None) -> list[uuid.UUID]:
    """Una pasada: expira las participaciones cuyo tiempo individual terminó. No depende de HTTP."""
    configurar_integraciones()
    with session_factory() as session:
        expiradas = ExpiracionParticipacionesService(session, reloj or RelojSistema()).expirar_vencidas()
    logger.info("expiracion de participaciones: %d expirada(s)", len(expiradas))
    return expiradas


def ejecutar_periodicamente(intervalo_segundos: int, reloj: Reloj | None = None) -> None:
    while True:
        try:
            ejecutar_una_vez(reloj=reloj)
        except Exception:  # noqa: BLE001 - se registra y se reintenta en la siguiente pasada
            logger.exception("fallo en la pasada de expiracion de participaciones")
        time.sleep(intervalo_segundos)
