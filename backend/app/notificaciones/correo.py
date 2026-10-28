"""Patrón Adapter: interfaz de envío de correo.

ServicioConvocatorias y los observadores dependen solo de ServicioCorreo.
AdaptadorProveedorCorreo (pendiente) traducirá estas llamadas a APIProveedorCorreo.
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ServicioCorreo(ABC):
    @abstractmethod
    def enviar(self, destinatario: str, asunto: str, cuerpo: str) -> None:
        """Envía un correo. Lanza una excepción si el proveedor lo rechaza."""


class ServicioCorreoConsola(ServicioCorreo):
    """Implementación provisional para desarrollo: escribe el correo en el log."""

    def enviar(self, destinatario: str, asunto: str, cuerpo: str) -> None:
        logger.info("Correo a %s | %s\n%s", destinatario, asunto, cuerpo)


def obtener_servicio_correo() -> ServicioCorreo:
    """Dependencia de FastAPI. Se cambiará por AdaptadorProveedorCorreo."""
    return ServicioCorreoConsola()
