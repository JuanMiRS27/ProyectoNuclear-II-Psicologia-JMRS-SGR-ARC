import smtplib
from email.message import EmailMessage

import pytest

from app.notificaciones.correo import (
    AdaptadorProveedorCorreo,
    APIProveedorCorreo,
    ErrorEnvioCorreo,
)


class APIFalsa(APIProveedorCorreo):
    def __init__(self, falla: bool = False) -> None:
        self.mensajes: list[EmailMessage] = []
        self.falla = falla

    def transmitir(self, mensaje: EmailMessage) -> None:
        if self.falla:
            raise smtplib.SMTPRecipientsRefused({})
        self.mensajes.append(mensaje)


def test_adaptador_traduce_al_formato_del_proveedor():
    api = APIFalsa()
    adaptador = AdaptadorProveedorCorreo(api, "Simulador <no-responder@simulador.edu.co>")

    adaptador.enviar("ana@uni.edu.co", "Convocatoria", "Tu código es ABC123")

    mensaje = api.mensajes[0]
    assert mensaje["To"] == "ana@uni.edu.co"
    assert mensaje["From"] == "Simulador <no-responder@simulador.edu.co>"
    assert mensaje["Subject"] == "Convocatoria"
    assert "ABC123" in mensaje.get_content()


def test_adaptador_convierte_errores_del_proveedor():
    adaptador = AdaptadorProveedorCorreo(APIFalsa(falla=True), "no-responder@simulador.edu.co")
    with pytest.raises(ErrorEnvioCorreo):
        adaptador.enviar("ana@uni.edu.co", "Asunto", "Cuerpo")
