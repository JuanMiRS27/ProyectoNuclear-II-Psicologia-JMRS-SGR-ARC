"""Agregar usuario.version_token para invalidar JWT tras un cambio de contraseña.

Revision ID: 0002_version_token
Revises: 0001_usuario
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_version_token"
down_revision: str | None = "0001_usuario"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # El DEFAULT 0 rellena las filas existentes; los tokens emitidos antes de esta
    # migración no tienen el claim "ver" y dejan de ser válidos (deberán iniciar sesión de nuevo).
    op.add_column("usuario", sa.Column("version_token", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.create_check_constraint(op.f("ck_usuario_version_token_no_negativa"), "usuario", "version_token >= 0")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_usuario_version_token_no_negativa"), "usuario", type_="check")
    op.drop_column("usuario", "version_token")
