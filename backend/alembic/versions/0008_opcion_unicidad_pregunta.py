"""Ajuste de la tabla opcion (módulo 2) para el módulo 4: UNIQUE (id, pregunta_id).

Revision ID: 0008_opcion_id_pregunta
Revises: 0007_revocacion_convocatoria
Create Date: 2026-10-29

Justificación: `respuesta` necesita garantizar en PostgreSQL que la opción elegida
pertenece a la pregunta respondida. Eso se logra con la FK compuesta
`respuesta(opcion_id, pregunta_id) -> opcion(id, pregunta_id)`, que exige una
restricción única exactamente sobre esas columnas. Como `id` ya es clave primaria,
la nueva restricción no impone ninguna regla adicional a los datos existentes.
Autocontenida: no importa código de la aplicación.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_opcion_id_pregunta"
down_revision: str | None = "0007_revocacion_convocatoria"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_opcion_id_pregunta", "opcion", ["id", "pregunta_id"])


def downgrade() -> None:
    op.drop_constraint("uq_opcion_id_pregunta", "opcion", type_="unique")
