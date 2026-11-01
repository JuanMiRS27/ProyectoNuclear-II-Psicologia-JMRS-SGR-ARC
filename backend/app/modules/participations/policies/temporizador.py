"""Reglas de tiempo y cierre de una participación (dominio puro, sin base de datos).

Fuente de verdad del temporizador: `participacion.inicio` (asignado por el servidor) y
`sesion.tiempo_max_min`. No depende del JWT, ni del navegador, ni de la hora `fin` de la
sesión, ni del cierre automático administrativo.

    fecha_limite = inicio + tiempo_max_min

- Con `ahora >= fecha_limite` la participación ya no acepta cambios y debe estar EXPIRADA.
- Al expirar por tiempo, `fin = fecha_limite` (el instante real en que se agotó el tiempo,
  aunque el cierre se detecte después: al consultar, al responder o desde el worker).
- Al cerrarse por una causa administrativa (revocación, fin de sesión) antes de agotar el
  tiempo, `fin = ahora`. Si el tiempo ya se había agotado, prevalece TIEMPO_AGOTADO.
"""

import math
from datetime import datetime, timedelta

from app.modules.participations.models import EstadoParticipacionEnum, MotivoCierreEnum, Participacion


def fecha_limite(participacion: Participacion, tiempo_max_min: int) -> datetime:
    return participacion.inicio + timedelta(minutes=tiempo_max_min)


def tiempo_restante_segundos(participacion: Participacion, tiempo_max_min: int, ahora: datetime) -> int:
    """Segundos restantes (redondeo hacia arriba); 0 si está cerrada o vencida."""
    if not participacion.activa:
        return 0
    return max(0, math.ceil((fecha_limite(participacion, tiempo_max_min) - ahora).total_seconds()))


def vencida(participacion: Participacion, tiempo_max_min: int, ahora: datetime) -> bool:
    return ahora >= fecha_limite(participacion, tiempo_max_min)


def expirar_por_tiempo(participacion: Participacion, tiempo_max_min: int) -> None:
    _cerrar(participacion, EstadoParticipacionEnum.EXPIRADA, MotivoCierreEnum.TIEMPO_AGOTADO, fecha_limite(participacion, tiempo_max_min))


def finalizar_voluntariamente(participacion: Participacion, tiempo_max_min: int, ahora: datetime) -> None:
    """FINALIZADA si aún tenía tiempo; si el tiempo ya venció, queda EXPIRADA (nunca FINALIZADA)."""
    if vencida(participacion, tiempo_max_min, ahora):
        expirar_por_tiempo(participacion, tiempo_max_min)
    else:
        _cerrar(participacion, EstadoParticipacionEnum.FINALIZADA, MotivoCierreEnum.VOLUNTARIO, ahora)


def cerrar_administrativamente(
    participacion: Participacion, motivo: MotivoCierreEnum, tiempo_max_min: int, ahora: datetime
) -> None:
    if vencida(participacion, tiempo_max_min, ahora):
        expirar_por_tiempo(participacion, tiempo_max_min)
    else:
        _cerrar(participacion, EstadoParticipacionEnum.EXPIRADA, motivo, ahora)


def _cerrar(participacion: Participacion, estado: EstadoParticipacionEnum, motivo: MotivoCierreEnum, fin: datetime) -> None:
    if not participacion.activa:
        return  # idempotente: una participación cerrada no cambia
    participacion.estado = estado
    participacion.motivo_cierre = motivo
    participacion.fin = fin
