"""Crear tipo rol_usuario y tabla usuario (módulo 1).

Revision ID: 0001_usuario
Revises:
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_usuario"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

rol_usuario = postgresql.ENUM("ADMINISTRADOR", "DOCENTE", "ESTUDIANTE", name="rol_usuario", create_type=False)


def upgrade() -> None:
    # gen_random_uuid() es nativa desde PostgreSQL 13 (versión mínima soportada).
    rol_usuario.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "usuario",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("nombre_completo", sa.String(length=150), nullable=False),
        sa.Column("correo", sa.String(length=150), nullable=False),
        sa.Column("clave_hash", sa.String(length=255), nullable=True),
        sa.Column("rol", rol_usuario, nullable=False),
        sa.Column("codigo_institucional", sa.String(length=30), nullable=True),
        sa.Column("puede_crear_casos", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usuario")),
        sa.UniqueConstraint("correo", name=op.f("uq_usuario_correo")),
        sa.UniqueConstraint("codigo_institucional", name=op.f("uq_usuario_codigo_institucional")),
        sa.CheckConstraint("correo = lower(btrim(correo))", name=op.f("ck_usuario_correo_normalizado")),
        sa.CheckConstraint("length(btrim(nombre_completo)) > 0", name=op.f("ck_usuario_nombre_no_vacio")),
        sa.CheckConstraint(
            "rol = 'ESTUDIANTE' OR clave_hash IS NOT NULL", name=op.f("ck_usuario_clave_requerida_por_rol")
        ),
        sa.CheckConstraint(
            "puede_crear_casos = false OR rol = 'DOCENTE'", name=op.f("ck_usuario_permiso_casos_solo_docente")
        ),
    )
    op.create_index(op.f("ix_usuario_rol"), "usuario", ["rol"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_usuario_rol"), table_name="usuario")
    op.drop_table("usuario")
    rol_usuario.drop(op.get_bind(), checkfirst=True)
