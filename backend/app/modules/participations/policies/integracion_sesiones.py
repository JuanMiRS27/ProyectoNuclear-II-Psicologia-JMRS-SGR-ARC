"""Implementaciones del módulo 4 para los puertos del módulo 3 (`app.modules.sessions.integracion`)."""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.modules.participations.models import MotivoCierreEnum
from app.modules.participations.policies import temporizador
from app.modules.participations.repositories import ParticipacionRepository
from app.modules.sessions.models import Convocatoria


class CierreParticipacionesSQL:
    """Cierra las participaciones activas dentro de la transacción del módulo 3.

    El módulo 3 ya bloqueó la sesión (FOR UPDATE); aquí se bloquean después las
    participaciones, respetando el orden sesión -> convocatoria -> participación.
    Las respuestas no se tocan y nada se borra.
    """

    def cerrar_por_revocacion(self, db: Session, convocatoria_id: uuid.UUID, ahora: datetime) -> None:
        repo = ParticipacionRepository(db)
        activa = repo.bloquear_activa(convocatoria_id)
        if activa is not None:
            temporizador.cerrar_administrativamente(
                activa, MotivoCierreEnum.CONVOCATORIA_REVOCADA, repo.tiempo_max_de(activa), ahora
            )
            repo.flush()

    def cerrar_por_fin_de_sesion(self, db: Session, sesion_id: uuid.UUID, ahora: datetime) -> None:
        repo = ParticipacionRepository(db)
        activas = repo.bloquear_activas_de_sesion(sesion_id)
        if not activas:
            return
        tiempo_max = repo.tiempo_max_de(activas[0])  # el mismo para toda la sesión
        for participacion in activas:
            temporizador.cerrar_administrativamente(participacion, MotivoCierreEnum.SESION_FINALIZADA, tiempo_max, ahora)
        repo.flush()


class ReingresoConParticipacionActiva:
    """Política de reingreso del módulo 4 (implementa `PoliticaReingreso` del módulo 3).

    El módulo 3 solo la consulta cuando las credenciales son válidas, la convocatoria está
    activa, la cuenta activa y la sesión EN_CURSO, pero la ventana de ingreso ya cerró.
    Permite el reingreso si esa MISMA convocatoria tiene un intento EN_PROGRESO cuyo tiempo
    individual no ha vencido (incluso después de `sesion.fin`). El token así emitido sirve
    para continuar ese intento; iniciar uno nuevo sigue exigiendo la ventana de ingreso.
    """

    def __init__(self, db: Session) -> None:
        self._repo = ParticipacionRepository(db)

    def permite_reingreso(self, convocatoria: Convocatoria, ahora: datetime) -> bool:
        activa = self._repo.activa(convocatoria.id)
        return activa is not None and not temporizador.vencida(activa, convocatoria.sesion.tiempo_max_min, ahora)
