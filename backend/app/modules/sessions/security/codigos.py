"""Códigos de autorización de convocatoria.

- Se generan con `secrets` (CSPRNG del sistema operativo), nunca con `random`.
- 12 símbolos de Base32 de Crockford (32 símbolos) = 60 bits de entropía:
  2^60 ≈ 1,15 · 10^18 combinaciones. Con la limitación de intentos (5 fallos por
  correo cada 15 min), adivinar un código es inviable.
- Se almacenan CIFRADOS con AES-256-GCM (ver `cifrado.py`); en la base de datos nunca
  aparece el código en claro.
"""

import hashlib
import hmac
import secrets

from app.core.config import get_settings
from app.modules.sessions.models import ALFABETO_CODIGO, LONGITUD_CODIGO


def generar_codigo() -> str:
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(LONGITUD_CODIGO))


def huella_codigo(codigo_almacenado: str) -> str:
    """HMAC del valor almacenado (el código CIFRADO) con la clave del servidor.

    Se incluye en el token estudiantil. Al regenerar el código cambia el texto cifrado,
    la huella deja de coincidir y los tokens emitidos antes quedan revocados. No
    requiere descifrar en cada petición ni revela nada del código.
    """
    clave = get_settings().JWT_SECRET_KEY.encode()
    return hmac.new(clave, codigo_almacenado.encode(), hashlib.sha256).hexdigest()[:32]
