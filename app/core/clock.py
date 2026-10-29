"""Fuente única del tiempo actual.

Toda regla temporal del backend (inicio de sesiones, límite de ingreso, cierre
automático) obtiene la hora de un `Reloj`, nunca de `datetime.now()` directamente
ni del cliente. Siempre devuelve instantes conscientes de zona horaria en UTC, de
modo que el resultado no depende de la zona horaria del servidor.

En las pruebas se sustituye por un reloj fijo (dependencia `get_reloj`).
"""

from datetime import UTC, datetime
from typing import Protocol


class Reloj(Protocol):
    def ahora(self) -> datetime: ...


class RelojSistema:
    def ahora(self) -> datetime:
        return datetime.now(UTC)


_reloj_sistema = RelojSistema()


def get_reloj() -> Reloj:
    """Dependencia de FastAPI; las pruebas la reemplazan con `app.dependency_overrides`."""
    return _reloj_sistema
