"""Patrón Adapter: envío de correo.

- ServicioCorreo (Target): la interfaz que usa el resto del sistema.
- APIProveedorCorreo (Adaptee): el cliente del proveedor externo (SMTP), con
  su propia interfaz basada en mensajes MIME.
- AdaptadorProveedorCorreo (Adapter): traduce enviar(destinatario, asunto,
  cuerpo) al formato que espera APIProveedorCorreo.

ServicioConvocatorias y los observadores dependen solo de ServicioCorreo, así
cambiar de proveedor no toca el resto del código.
"""

import logging
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

from app.core.config import obtener_configuracion

logger = logging.getLogger(__name__)


class ErrorEnvioCorreo(Exception):
    """El proveedor no pudo entregar el mensaje."""


class ServicioCorreo(ABC):
    @abstractmethod
    def enviar(self, destinatario: str, asunto: str, cuerpo: str) -> None:
        """Envía un correo. Lanza ErrorEnvioCorreo si el proveedor lo rechaza."""


class APIProveedorCorreo:
    """Cliente SMTP del proveedor. Su interfaz no coincide con ServicioCorreo."""

    def __init__(
        self,
        host: str,
        puerto: int,
        usuario: str | None = None,
        clave: str | None = None,
        usar_tls: bool = False,
    ) -> None:
        self._host = host
        self._puerto = puerto
        self._usuario = usuario
        self._clave = clave
        self._usar_tls = usar_tls

    def transmitir(self, mensaje: EmailMessage) -> None:
        with smtplib.SMTP(self._host, self._puerto, timeout=10) as smtp:
            if self._usar_tls:
                smtp.starttls()
            if self._usuario and self._clave:
                smtp.login(self._usuario, self._clave)
            smtp.send_message(mensaje)


class AdaptadorProveedorCorreo(ServicioCorreo):
    def __init__(self, api: APIProveedorCorreo, remitente: str) -> None:
        self._api = api
        self._remitente = remitente

    def enviar(self, destinatario: str, asunto: str, cuerpo: str) -> None:
        mensaje = EmailMessage()
        mensaje["From"] = self._remitente
        mensaje["To"] = destinatario
        mensaje["Subject"] = asunto
        mensaje.set_content(cuerpo)
        try:
            self._api.transmitir(mensaje)
        except (smtplib.SMTPException, OSError) as error:
            raise ErrorEnvioCorreo(f"No se pudo enviar el correo a {destinatario}") from error


class ServicioCorreoConsola(ServicioCorreo):
    """Alternativa sin servidor de correo: escribe el mensaje en el log."""

    def enviar(self, destinatario: str, asunto: str, cuerpo: str) -> None:
        logger.info("Correo a %s | %s\n%s", destinatario, asunto, cuerpo)


def obtener_servicio_correo() -> ServicioCorreo:
    config = obtener_configuracion()
    if config.correo_modo == "smtp":
        api = APIProveedorCorreo(
            host=config.smtp_host,
            puerto=config.smtp_puerto,
            usuario=config.smtp_usuario,
            clave=config.smtp_clave,
            usar_tls=config.smtp_tls,
        )
        return AdaptadorProveedorCorreo(api, config.correo_remitente)
    return ServicioCorreoConsola()
