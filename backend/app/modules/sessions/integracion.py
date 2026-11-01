"""Puntos de integración del módulo 3 con el módulo 4 (participaciones), sin dependencia circular.

El módulo 3 no importa el módulo 4. Declara aquí dos puertos con una implementación por
defecto que no hace nada; el módulo 4 aporta las reales, que se conectan desde la raíz de
composición (`app/bootstrap.py`) al crear la aplicación o al arrancar un worker.

- `CierreParticipaciones`: se invoca DENTRO de la misma transacción que revoca una
  convocatoria o finaliza una sesión (no con eventos posteriores al commit), para que el
  cierre de las participaciones activas sea atómico con esa transición.
- Fábrica de `PoliticaReingreso`: decide si un estudiante puede reautenticarse fuera de la
  ventana de ingreso para continuar un intento ya iniciado.

Es inyección de dependencias con un único destinatario por puerto, no Observer.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session

from app.modules.sessions.services.acceso_service import PoliticaReingreso, SinReingreso


class CierreParticipaciones(Protocol):
    def cerrar_por_revocacion(self, db: Session, convocatoria_id: uuid.UUID, ahora: datetime) -> None: ...

    def cerrar_por_fin_de_sesion(self, db: Session, sesion_id: uuid.UUID, ahora: datetime) -> None: ...


class _SinParticipaciones:
    def cerrar_por_revocacion(self, db: Session, convocatoria_id: uuid.UUID, ahora: datetime) -> None:
        return None

    def cerrar_por_fin_de_sesion(self, db: Session, sesion_id: uuid.UUID, ahora: datetime) -> None:
        return None


FabricaReingreso = Callable[[Session], PoliticaReingreso]

_cierre: CierreParticipaciones = _SinParticipaciones()
_fabrica_reingreso: FabricaReingreso = lambda db: SinReingreso()  # noqa: E731


def configurar(*, cierre: CierreParticipaciones | None = None, fabrica_reingreso: FabricaReingreso | None = None) -> None:
    global _cierre, _fabrica_reingreso
    if cierre is not None:
        _cierre = cierre
    if fabrica_reingreso is not None:
        _fabrica_reingreso = fabrica_reingreso


def cierre_participaciones() -> CierreParticipaciones:
    return _cierre


def politica_reingreso(db: Session) -> PoliticaReingreso:
    return _fabrica_reingreso(db)
