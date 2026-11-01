"""Módulo 3: tipo estado_sesion y tablas espacio_academico, sesion y convocatoria.

Revision ID: 0005_sesiones
Revises: 0004_tiempo_max_240
Create Date: 2026-10-29

Migración autocontenida: los valores (240 minutos, alfabeto y longitud del código)
son literales de esta revisión y no se importan del código de la aplicación.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_sesiones"
down_revision: str | None = "0004_tiempo_max_240"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIEMPO_MAXIMO_MIN = 240
FORMATO_CODIGO = "^[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{12}$"

estado_sesion = postgresql.ENUM("SIN_INICIAR", "EN_CURSO", "FINALIZADO", name="estado_sesion", create_type=False)

_UUID_PK = {"server_default": sa.text("gen_random_uuid()"), "nullable": False}

_FN_SESION = """
CREATE FUNCTION fn_sesion_programable() RETURNS trigger AS $$
DECLARE
    v_estado text;
    v_tiempo integer;
    v_rol text;
BEGIN
    -- FOR SHARE: espera a que termine un cambio de estado concurrente del caso (p. ej., archivarlo).
    SELECT estado::text, tiempo_estimado_min INTO v_estado, v_tiempo
    FROM caso_estudio WHERE id = NEW.caso_id FOR SHARE;
    IF v_estado IS DISTINCT FROM 'PUBLICADO' THEN
        RAISE EXCEPTION 'Solo se pueden programar sesiones con casos publicados.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_sesion_caso_publicado';
    END IF;
    IF NEW.tiempo_max_min > v_tiempo THEN
        RAISE EXCEPTION 'El tiempo máximo supera el tiempo estimado del caso.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_sesion_tiempo_max_hasta_estimado';
    END IF;
    SELECT rol::text INTO v_rol FROM usuario WHERE id = NEW.docente_id;
    IF v_rol IS DISTINCT FROM 'DOCENTE' THEN
        RAISE EXCEPTION 'El responsable de la sesión debe ser un docente.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_sesion_responsable_docente';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRG_SESION = """
CREATE TRIGGER trg_sesion_programable
BEFORE INSERT OR UPDATE OF caso_id, tiempo_max_min, docente_id ON sesion
FOR EACH ROW EXECUTE FUNCTION fn_sesion_programable();
"""

_FN_CONVOCATORIA = """
CREATE FUNCTION fn_convocatoria_estudiante() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM usuario WHERE id = NEW.estudiante_id AND rol = 'ESTUDIANTE') THEN
        RAISE EXCEPTION 'Solo se pueden convocar usuarios con rol ESTUDIANTE.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_convocatoria_estudiante_rol';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRG_CONVOCATORIA = """
CREATE TRIGGER trg_convocatoria_estudiante
BEFORE INSERT OR UPDATE OF estudiante_id ON convocatoria
FOR EACH ROW EXECUTE FUNCTION fn_convocatoria_estudiante();
"""


def upgrade() -> None:
    estado_sesion.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "espacio_academico",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("nombre", sa.String(length=150), nullable=False),
        sa.Column("periodo", sa.String(length=10), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_espacio_academico")),
        sa.CheckConstraint("length(btrim(nombre)) > 0", name=op.f("ck_espacio_academico_nombre_no_vacio")),
        sa.CheckConstraint("length(btrim(periodo)) > 0", name=op.f("ck_espacio_academico_periodo_no_vacio")),
    )
    op.create_index(
        "uq_espacio_academico_nombre_periodo",
        "espacio_academico",
        [sa.text("lower(nombre)"), sa.text("lower(periodo)")],
        unique=True,
    )
    op.create_index("ix_espacio_academico_activo", "espacio_academico", ["activo"])

    op.create_table(
        "sesion",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("caso_id", sa.UUID(), nullable=False),
        sa.Column("espacio_academico_id", sa.UUID(), nullable=False),
        sa.Column("docente_id", sa.UUID(), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fin", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tiempo_max_min", sa.Integer(), nullable=False),
        sa.Column("lugar", sa.String(length=150), nullable=True),
        sa.Column("mensaje", sa.Text(), nullable=True),
        sa.Column("estado", estado_sesion, server_default=sa.text("'SIN_INICIAR'"), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sesion")),
        sa.ForeignKeyConstraint(["caso_id"], ["caso_estudio.id"], name=op.f("fk_sesion_caso_id_caso_estudio"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["espacio_academico_id"],
            ["espacio_academico.id"],
            name=op.f("fk_sesion_espacio_academico_id_espacio_academico"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["docente_id"], ["usuario.id"], name=op.f("fk_sesion_docente_id_usuario"), ondelete="RESTRICT"),
        sa.CheckConstraint("inicio < fin", name=op.f("ck_sesion_inicio_antes_de_fin")),
        sa.CheckConstraint(
            f"tiempo_max_min BETWEEN 1 AND {TIEMPO_MAXIMO_MIN}", name=op.f("ck_sesion_tiempo_max_en_rango")
        ),
    )
    op.create_index("ix_sesion_estado_fin", "sesion", ["estado", "fin"])
    op.create_index("ix_sesion_docente_id_inicio", "sesion", ["docente_id", "inicio"])
    op.create_index("ix_sesion_caso_id", "sesion", ["caso_id"])
    op.create_index("ix_sesion_espacio_academico_id", "sesion", ["espacio_academico_id"])

    op.create_table(
        "convocatoria",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("sesion_id", sa.UUID(), nullable=False),
        sa.Column("estudiante_id", sa.UUID(), nullable=False),
        sa.Column("codigo_autorizacion", sa.String(length=12), nullable=False),
        sa.Column("intentos_autorizados", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_convocatoria")),
        sa.ForeignKeyConstraint(["sesion_id"], ["sesion.id"], name=op.f("fk_convocatoria_sesion_id_sesion"), ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["estudiante_id"], ["usuario.id"], name=op.f("fk_convocatoria_estudiante_id_usuario"), ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("sesion_id", "estudiante_id", name="uq_convocatoria_sesion_estudiante"),
        sa.UniqueConstraint("codigo_autorizacion", name="uq_convocatoria_codigo_autorizacion"),
        sa.CheckConstraint("intentos_autorizados >= 1", name=op.f("ck_convocatoria_intentos_positivos")),
        sa.CheckConstraint(f"codigo_autorizacion ~ '{FORMATO_CODIGO}'", name=op.f("ck_convocatoria_codigo_formato")),
    )
    op.create_index("ix_convocatoria_estudiante_id", "convocatoria", ["estudiante_id"])

    op.execute(_FN_SESION)
    op.execute(_TRG_SESION)
    op.execute(_FN_CONVOCATORIA)
    op.execute(_TRG_CONVOCATORIA)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_convocatoria_estudiante ON convocatoria")
    op.execute("DROP FUNCTION IF EXISTS fn_convocatoria_estudiante()")
    op.execute("DROP TRIGGER IF EXISTS trg_sesion_programable ON sesion")
    op.execute("DROP FUNCTION IF EXISTS fn_sesion_programable()")
    op.drop_index("ix_convocatoria_estudiante_id", table_name="convocatoria")
    op.drop_table("convocatoria")
    for indice in ("ix_sesion_espacio_academico_id", "ix_sesion_caso_id", "ix_sesion_docente_id_inicio", "ix_sesion_estado_fin"):
        op.drop_index(indice, table_name="sesion")
    op.drop_table("sesion")
    op.drop_index("ix_espacio_academico_activo", table_name="espacio_academico")
    op.drop_index("uq_espacio_academico_nombre_periodo", table_name="espacio_academico")
    op.drop_table("espacio_academico")
    estado_sesion.drop(op.get_bind(), checkfirst=True)
