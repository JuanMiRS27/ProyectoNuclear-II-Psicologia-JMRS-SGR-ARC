"""Limitar caso_estudio.tiempo_estimado_min al rango del modelo del proyecto (1-240 minutos).

Sustituye ck_caso_estudio_tiempo_positivo (> 0) por ck_caso_estudio_tiempo_en_rango (BETWEEN 1 AND 240).
Si existen casos con más de 240 minutos, la migración se detiene sin modificar datos:
deben corregirse manualmente antes de aplicarla.

Revision ID: 0004_tiempo_max_240
Revises: 0003_casos
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tiempo_max_240"
down_revision: str | None = "0003_casos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIEMPO_MAXIMO_MIN = 240  # valor fijo de esta revisión (las migraciones no importan código de la aplicación)


def upgrade() -> None:
    excedidos = op.get_bind().execute(
        sa.text("SELECT count(*) FROM caso_estudio WHERE tiempo_estimado_min > :maximo"), {"maximo": TIEMPO_MAXIMO_MIN}
    ).scalar_one()
    if excedidos:
        raise RuntimeError(
            f"{excedidos} caso(s) tienen tiempo_estimado_min > {TIEMPO_MAXIMO_MIN}. "
            "Corríjalos antes de aplicar la migración 0004; no se modifican datos automáticamente."
        )
    op.drop_constraint(op.f("ck_caso_estudio_tiempo_positivo"), "caso_estudio", type_="check")
    op.create_check_constraint(
        op.f("ck_caso_estudio_tiempo_en_rango"),
        "caso_estudio",
        f"tiempo_estimado_min IS NULL OR tiempo_estimado_min BETWEEN 1 AND {TIEMPO_MAXIMO_MIN}",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_caso_estudio_tiempo_en_rango"), "caso_estudio", type_="check")
    op.create_check_constraint(
        op.f("ck_caso_estudio_tiempo_positivo"), "caso_estudio", "tiempo_estimado_min IS NULL OR tiempo_estimado_min > 0"
    )
