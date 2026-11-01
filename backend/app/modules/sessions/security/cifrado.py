"""Cifrado autenticado en reposo de los códigos de autorización.

- Algoritmo: AES-256-GCM de la biblioteca `cryptography` (PyCA). No se implementa
  criptografía propia: solo se usa la primitiva AEAD estándar.
- Clave: `CONVOCATION_CODE_ENCRYPTION_KEY` (32 bytes), solo en el entorno. Nunca en
  PostgreSQL ni en Git.
- Nonce: 96 bits aleatorios (`os.urandom`) por cada cifrado. Cifrar dos veces el mismo
  código da textos distintos.
- Datos asociados (AAD): el id de la convocatoria. El texto cifrado queda ligado a su
  fila: copiarlo a otra convocatoria hace fallar el descifrado.
- Formato almacenado: ``v1.<base64url(nonce || cifrado || etiqueta)>`` (54 caracteres).
  El prefijo de versión permite rotar claves o algoritmos en el futuro.

La base de datos nunca contiene el código en claro. Lo descifran solo el ingreso
estudiantil (para compararlo), el endpoint restringido del docente responsable y,
en el futuro, el envío de notificaciones del módulo 6.
"""

import base64
import binascii
import os
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

VERSION = "v1"
_TAM_NONCE = 12


class CodigoCifradoInvalidoError(Exception):
    """El valor almacenado fue alterado, pertenece a otra convocatoria o se cifró con otra clave."""


def _cifrador() -> AESGCM:
    return AESGCM(base64.urlsafe_b64decode(get_settings().CONVOCATION_CODE_ENCRYPTION_KEY.encode()))


def cifrar_codigo(codigo: str, convocatoria_id: uuid.UUID) -> str:
    nonce = os.urandom(_TAM_NONCE)
    cifrado = _cifrador().encrypt(nonce, codigo.encode(), convocatoria_id.bytes)
    return f"{VERSION}.{base64.urlsafe_b64encode(nonce + cifrado).decode().rstrip('=')}"


def descifrar_codigo(valor: str, convocatoria_id: uuid.UUID) -> str:
    try:
        version, cuerpo = valor.split(".", 1)
        if version != VERSION:
            raise CodigoCifradoInvalidoError()
        datos = base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4))
        claro = _cifrador().decrypt(datos[:_TAM_NONCE], datos[_TAM_NONCE:], convocatoria_id.bytes)
        return claro.decode()
    except (InvalidTag, ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise CodigoCifradoInvalidoError() from exc
