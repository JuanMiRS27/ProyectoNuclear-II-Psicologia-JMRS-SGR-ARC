"""Módulo 4: tipos estado_participacion y motivo_cierre_participacion, tablas participacion y respuesta.

Revision ID: 0009_participaciones
Revises: 0008_opcion_id_pregunta
Create Date: 2026-10-29

Todas las FK usan RESTRICT para preservar el historial. Los disparadores refuerzan en
PostgreSQL las reglas que también aplican los servicios:

- inserción de participaciones solo con convocatoria activa, sesión EN_CURSO y
  numero_intento <= intentos_autorizados;
- inmutabilidad de inicio, número de intento y convocatoria; participaciones cerradas
  inmutables (salvo nota_final y retroalimentacion_general, reservadas al módulo 5);
  prohibición de borrar participaciones;
- respuestas solo en participaciones EN_PROGRESO y con preguntas del caso de la sesión.

Autocontenida: no importa código de la aplicación.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_participaciones"
down_revision: str | None = "0008_opcion_id_pregunta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

estado_participacion = postgresql.ENUM(
    "EN_PROGRESO", "FINALIZADA", "EXPIRADA", name="estado_participacion", create_type=False
)
motivo_cierre = postgresql.ENUM(
    "VOLUNTARIO", "TIEMPO_AGOTADO", "CONVOCATORIA_REVOCADA", "SESION_FINALIZADA",
    name="motivo_cierre_participacion", create_type=False,
)

_UUID_PK = {"server_default": sa.text("gen_random_uuid()"), "nullable": False}

_FUNCIONES = [
    """
CREATE FUNCTION fn_participacion_insercion() RETURNS trigger AS $$
DECLARE
    v_intentos integer;
    v_revocada timestamptz;
    v_estado text;
BEGIN
    SELECT c.intentos_autorizados, c.revocada_en, s.estado::text INTO v_intentos, v_revocada, v_estado
    FROM convocatoria c JOIN sesion s ON s.id = c.sesion_id
    WHERE c.id = NEW.convocatoria_id FOR SHARE OF c;
    IF v_revocada IS NOT NULL THEN
        RAISE EXCEPTION 'La convocatoria está revocada.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_participacion_convocatoria_activa';
    END IF;
    IF v_estado IS DISTINCT FROM 'EN_CURSO' THEN
        RAISE EXCEPTION 'La sesión no está en curso.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_participacion_sesion_en_curso';
    END IF;
    IF NEW.numero_intento > v_intentos THEN
        RAISE EXCEPTION 'Se superó el número de intentos autorizados.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_participacion_intentos_autorizados';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""",
    """
CREATE FUNCTION fn_participacion_inmutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Las participaciones no se eliminan.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_participacion_sin_borrado';
    END IF;
    IF NEW.inicio IS DISTINCT FROM OLD.inicio
       OR NEW.numero_intento IS DISTINCT FROM OLD.numero_intento
       OR NEW.convocatoria_id IS DISTINCT FROM OLD.convocatoria_id THEN
        RAISE EXCEPTION 'El inicio, el número de intento y la convocatoria no se modifican.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_participacion_datos_inmutables';
    END IF;
    IF OLD.estado <> 'EN_PROGRESO'
       AND (NEW.estado IS DISTINCT FROM OLD.estado
            OR NEW.fin IS DISTINCT FROM OLD.fin
            OR NEW.motivo_cierre IS DISTINCT FROM OLD.motivo_cierre) THEN
        RAISE EXCEPTION 'Una participación cerrada no se modifica.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_participacion_cerrada_inmutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""",
    """
CREATE FUNCTION fn_respuesta_valida() RETURNS trigger AS $$
DECLARE
    v_participacion uuid;
BEGIN
    v_participacion := CASE WHEN TG_OP = 'DELETE' THEN OLD.participacion_id ELSE NEW.participacion_id END;
    IF NOT EXISTS (SELECT 1 FROM participacion WHERE id = v_participacion AND estado = 'EN_PROGRESO') THEN
        RAISE EXCEPTION 'La participación no está en progreso.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_respuesta_participacion_en_progreso';
    END IF;
    IF TG_OP <> 'DELETE' AND NOT EXISTS (
        SELECT 1
        FROM participacion p
        JOIN convocatoria c ON c.id = p.convocatoria_id
        JOIN sesion s ON s.id = c.sesion_id
        JOIN escenario e ON e.caso_id = s.caso_id
        JOIN pregunta q ON q.escenario_id = e.id
        WHERE p.id = NEW.participacion_id AND q.id = NEW.pregunta_id
    ) THEN
        RAISE EXCEPTION 'La pregunta no pertenece al caso de la sesión.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_respuesta_pregunta_del_caso';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql;
""",
]

_DISPARADORES = [
    "CREATE TRIGGER trg_participacion_insercion BEFORE INSERT ON participacion "
    "FOR EACH ROW EXECUTE FUNCTION fn_participacion_insercion();",
    "CREATE TRIGGER trg_participacion_inmutable BEFORE UPDATE OR DELETE ON participacion "
    "FOR EACH ROW EXECUTE FUNCTION fn_participacion_inmutable();",
    "CREATE TRIGGER trg_respuesta_valida BEFORE INSERT OR UPDATE OR DELETE ON respuesta "
    "FOR EACH ROW EXECUTE FUNCTION fn_respuesta_valida();",
]


def upgrade() -> None:
    estado_participacion.create(op.get_bind(), checkfirst=True)
    motivo_cierre.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "participacion",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("convocatoria_id", sa.UUID(), nullable=False),
        sa.Column("numero_intento", sa.Integer(), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fin", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nota_final", sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column("estado", estado_participacion, server_default=sa.text("'EN_PROGRESO'"), nullable=False),
        sa.Column("motivo_cierre", motivo_cierre, nullable=True),
        sa.Column("retroalimentacion_general", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participacion")),
        sa.ForeignKeyConstraint(
            ["convocatoria_id"], ["convocatoria.id"], name=op.f("fk_participacion_convocatoria_id_convocatoria"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("convocatoria_id", "numero_intento", name="uq_participacion_convocatoria_intento"),
        sa.CheckConstraint("numero_intento >= 1", name=op.f("ck_participacion_intento_positivo")),
        sa.CheckConstraint("(estado = 'EN_PROGRESO') = (fin IS NULL)", name=op.f("ck_participacion_fin_segun_estado")),
        sa.CheckConstraint("fin IS NULL OR fin >= inicio", name=op.f("ck_participacion_fin_posterior_a_inicio")),
        sa.CheckConstraint(
            "(estado = 'EN_PROGRESO' AND motivo_cierre IS NULL)"
            " OR (estado = 'FINALIZADA' AND motivo_cierre = 'VOLUNTARIO')"
            " OR (estado = 'EXPIRADA' AND motivo_cierre IN ('TIEMPO_AGOTADO', 'CONVOCATORIA_REVOCADA', 'SESION_FINALIZADA'))",
            name=op.f("ck_participacion_motivo_segun_estado"),
        ),
        sa.CheckConstraint("nota_final IS NULL OR nota_final >= 0", name=op.f("ck_participacion_nota_no_negativa")),
    )
    op.create_index(
        "uq_participacion_activa_por_convocatoria",
        "participacion",
        ["convocatoria_id"],
        unique=True,
        postgresql_where=sa.text("estado = 'EN_PROGRESO'"),
    )

    op.create_table(
        "respuesta",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("participacion_id", sa.UUID(), nullable=False),
        sa.Column("pregunta_id", sa.UUID(), nullable=False),
        sa.Column("opcion_id", sa.UUID(), nullable=False),
        sa.Column("respondida_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizada_en", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_respuesta")),
        sa.ForeignKeyConstraint(
            ["participacion_id"], ["participacion.id"], name=op.f("fk_respuesta_participacion_id_participacion"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pregunta_id"], ["pregunta.id"], name=op.f("fk_respuesta_pregunta_id_pregunta"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["opcion_id", "pregunta_id"], ["opcion.id", "opcion.pregunta_id"],
            name="fk_respuesta_opcion_de_la_pregunta", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("participacion_id", "pregunta_id", name="uq_respuesta_participacion_pregunta"),
        sa.CheckConstraint("actualizada_en >= respondida_en", name=op.f("ck_respuesta_actualizada_posterior")),
    )
    op.create_index("ix_respuesta_pregunta_id", "respuesta", ["pregunta_id"])
    op.create_index("ix_respuesta_opcion_id_pregunta_id", "respuesta", ["opcion_id", "pregunta_id"])

    for sentencia in _FUNCIONES + _DISPARADORES:
        op.execute(sentencia)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_respuesta_valida ON respuesta")
    op.execute("DROP TRIGGER IF EXISTS trg_participacion_inmutable ON participacion")
    op.execute("DROP TRIGGER IF EXISTS trg_participacion_insercion ON participacion")
    for funcion in ("fn_respuesta_valida", "fn_participacion_inmutable", "fn_participacion_insercion"):
        op.execute(f"DROP FUNCTION IF EXISTS {funcion}()")
    op.drop_index("ix_respuesta_opcion_id_pregunta_id", table_name="respuesta")
    op.drop_index("ix_respuesta_pregunta_id", table_name="respuesta")
    op.drop_table("respuesta")
    op.drop_index("uq_participacion_activa_por_convocatoria", table_name="participacion")
    op.drop_table("participacion")
    motivo_cierre.drop(op.get_bind(), checkfirst=True)
    estado_participacion.drop(op.get_bind(), checkfirst=True)
