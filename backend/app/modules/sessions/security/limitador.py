"""Limitación de intentos fallidos de acceso estudiantil (ventana deslizante en memoria).

Cuenta FALLOS por correo y por dirección IP. Superado el límite, la clave queda
bloqueada hasta que los fallos salgan de la ventana, incluso si después se envía
el código correcto (si no, el bloqueo no impediría la adivinación). Un ingreso
correcto limpia los fallos de ese correo.

Limitación conocida: el estado vive en el proceso. Con varios workers de Uvicorn o
varias réplicas, cada uno lleva su propia cuenta; para producción distribuida debe
sustituirse por un almacén compartido (p. ej., Redis) con la misma interfaz.
"""

import math
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from app.core.clock import Reloj, get_reloj
from app.core.config import get_settings
from app.core.exceptions import AppError


class DemasiadosIntentosError(AppError):
    status_code = 429
    default_detail = "Demasiados intentos fallidos. Espere antes de volver a intentarlo."

    def __init__(self, reintentar_en: int) -> None:
        super().__init__()
        self.reintentar_en = reintentar_en
        self.headers = {"Retry-After": str(reintentar_en)}


class LimitadorIntentos:
    def __init__(self, reloj: Reloj, ventana: timedelta, max_por_correo: int, max_por_ip: int) -> None:
        self._reloj = reloj
        self._ventana = ventana
        self._limites = {"correo": max_por_correo, "ip": max_por_ip}
        self._fallos: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def verificar(self, correo: str, ip: str) -> None:
        """Lanza `DemasiadosIntentosError` si el correo o la IP están bloqueados."""
        ahora = self._reloj.ahora()
        with self._lock:
            esperas = [
                self._espera((tipo, valor), ahora)
                for tipo, valor in (("correo", correo), ("ip", ip))
            ]
        espera = max(esperas)
        if espera > 0:
            raise DemasiadosIntentosError(espera)

    def registrar_fallo(self, correo: str, ip: str) -> None:
        ahora = self._reloj.ahora()
        with self._lock:
            for clave in (("correo", correo), ("ip", ip)):
                self._purgar(clave, ahora)
                self._fallos[clave].append(ahora)

    def registrar_exito(self, correo: str) -> None:
        with self._lock:
            self._fallos.pop(("correo", correo), None)

    def reiniciar(self) -> None:
        with self._lock:
            self._fallos.clear()

    def _purgar(self, clave: tuple[str, str], ahora: datetime) -> None:
        cola = self._fallos[clave]
        while cola and cola[0] <= ahora - self._ventana:
            cola.popleft()

    def _espera(self, clave: tuple[str, str], ahora: datetime) -> int:
        self._purgar(clave, ahora)
        cola = self._fallos[clave]
        if len(cola) < self._limites[clave[0]]:
            return 0
        # Se desbloquea cuando sale de la ventana el fallo que hace superar el límite.
        liberacion = cola[-self._limites[clave[0]]] + self._ventana
        return max(1, math.ceil((liberacion - ahora).total_seconds()))


_limitador: LimitadorIntentos | None = None


def get_limitador() -> LimitadorIntentos:
    """Instancia única del proceso (dependencia de FastAPI; las pruebas pueden reemplazarla)."""
    global _limitador
    if _limitador is None:
        settings = get_settings()
        _limitador = LimitadorIntentos(
            reloj=get_reloj(),
            ventana=timedelta(seconds=settings.STUDENT_LOGIN_WINDOW_SECONDS),
            max_por_correo=settings.STUDENT_LOGIN_MAX_FAILURES_PER_EMAIL,
            max_por_ip=settings.STUDENT_LOGIN_MAX_FAILURES_PER_IP,
        )
    return _limitador
