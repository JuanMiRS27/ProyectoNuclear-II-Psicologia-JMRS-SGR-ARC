"""Cifrar en reposo los códigos de autorización de convocatoria (AES-256-GCM).

Revision ID: 0006_cifrado_codigos
Revises: 0005_sesiones
Create Date: 2026-10-29

`convocatoria.codigo_autorizacion` pasa de VARCHAR(12) con el código en claro a
VARCHAR(100) con ``v1.<base64url(nonce || cifrado || etiqueta)>``. Los datos asociados
(AAD) son el id de la convocatoria.

- Se elimina `uq_convocatoria_codigo_autorizacion`: el texto cifrado es aleatorio y no
  admite unicidad. La unicidad necesaria (entre las convocatorias de un mismo
  estudiante) la garantiza la aplicación al generar el código.
- `ck_convocatoria_codigo_formato` se sustituye por `ck_convocatoria_codigo_cifrado`.
- Las filas existentes se cifran (upgrade) o se descifran (downgrade) en la misma
  transacción. Para ello se necesita la clave: la aporta `env.py` desde la
  configuración (CONVOCATION_CODE_ENCRYPTION_KEY). Si hay filas y no hay clave, la
  migración se detiene sin modificar nada.

Autocontenida: no importa código de la aplicación. Reproduce aquí el formato v1.
"""

import base64
import os
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

revision: str = "0006_cifrado_codigos"
down_revision: str | None = "0005_sesiones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORMATO_CLARO = "^[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{12}$"
FORMATO_CIFRADO = "^v[0-9]+\\.[A-Za-z0-9_-]+$"


def _aes() -> AESGCM:
    clave = op.get_context().config.attributes.get("convocation_code_key") or os.environ.get(
        "CONVOCATION_CODE_ENCRYPTION_KEY"
    )
    if not clave:
        raise RuntimeError(
            "Hay convocatorias existentes y no se configuró CONVOCATION_CODE_ENCRYPTION_KEY; "
            "la migración 0006 no modificó ningún dato."
        )
    return AESGCM(base64.urlsafe_b64decode(clave.encode()))


def _cifrar(aes: AESGCM, codigo: str, convocatoria_id: uuid.UUID) -> str:
    nonce = os.urandom(12)
    datos = nonce + aes.encrypt(nonce, codigo.encode(), convocatoria_id.bytes)
    return "v1." + base64.urlsafe_b64encode(datos).decode().rstrip("=")


def _descifrar(aes: AESGCM, valor: str, convocatoria_id: uuid.UUID) -> str:
    cuerpo = valor.split(".", 1)[1]
    datos = base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4))
    return aes.decrypt(datos[:12], datos[12:], convocatoria_id.bytes).decode()


def _filas() -> list[tuple[uuid.UUID, str]]:
    return [(fila.id, fila.codigo_autorizacion) for fila in op.get_bind().execute(sa.text("SELECT id, codigo_autorizacion FROM convocatoria"))]


def _actualizar(valores: list[tuple[uuid.UUID, str]]) -> None:
    bind = op.get_bind()
    for convocatoria_id, valor in valores:
        bind.execute(sa.text("UPDATE convocatoria SET codigo_autorizacion = :v WHERE id = :id"), {"v": valor, "id": convocatoria_id})


def upgrade() -> None:
    filas = _filas()
    aes = _aes() if filas else None
    op.drop_constraint("uq_convocatoria_codigo_autorizacion", "convocatoria", type_="unique")
    op.drop_constraint(op.f("ck_convocatoria_codigo_formato"), "convocatoria", type_="check")
    op.alter_column("convocatoria", "codigo_autorizacion", type_=sa.String(length=100), existing_nullable=False)
    if filas:
        _actualizar([(cid, _cifrar(aes, codigo, uuid.UUID(str(cid)))) for cid, codigo in filas])
    op.create_check_constraint(
        op.f("ck_convocatoria_codigo_cifrado"), "convocatoria", f"codigo_autorizacion ~ '{FORMATO_CIFRADO}'"
    )


def downgrade() -> None:
    filas = _filas()
    aes = _aes() if filas else None
    op.drop_constraint(op.f("ck_convocatoria_codigo_cifrado"), "convocatoria", type_="check")
    if filas:
        _actualizar([(cid, _descifrar(aes, valor, uuid.UUID(str(cid)))) for cid, valor in filas])
    op.alter_column("convocatoria", "codigo_autorizacion", type_=sa.String(length=12), existing_nullable=False)
    op.create_unique_constraint("uq_convocatoria_codigo_autorizacion", "convocatoria", ["codigo_autorizacion"])
    op.create_check_constraint(
        op.f("ck_convocatoria_codigo_formato"), "convocatoria", f"codigo_autorizacion ~ '{FORMATO_CLARO}'"
    )
