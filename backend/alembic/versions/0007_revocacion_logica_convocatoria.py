"""Revocación lógica de convocatorias: columna convocatoria.revocada_en.

Revision ID: 0007_revocacion_convocatoria
Revises: 0006_cifrado_codigos
Create Date: 2026-10-29

Retirar a un estudiante deja de borrar la fila: se registra el instante de revocación.
Así la convocatoria sigue existiendo para las participaciones del módulo 4 (FK con
RESTRICT) y para el historial. NULL significa "activa"; las filas existentes quedan activas.
Autocontenida: no importa código de la aplicación.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_revocacion_convocatoria"
down_revision: str | None = "0006_cifrado_codigos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("convocatoria", sa.Column("revocada_en", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Las convocatorias revocadas se eliminan para volver a la semántica de 0006 (retiro = borrado):
    # dejarlas sin la marca las reactivaría y les devolvería el acceso.
    op.execute("DELETE FROM convocatoria WHERE revocada_en IS NOT NULL")
    op.drop_column("convocatoria", "revocada_en")
