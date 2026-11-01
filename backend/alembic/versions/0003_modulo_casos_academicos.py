"""Módulo 2: tipo estado_caso y tablas caso_estudio, escenario, pregunta, opcion, rubrica y criterio_rubrica.

Revision ID: 0003_casos
Revises: 0002_version_token
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_casos"
down_revision: str | None = "0002_version_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

estado_caso = postgresql.ENUM("BORRADOR", "PUBLICADO", "ARCHIVADO", name="estado_caso", create_type=False)

_UUID_PK = {"server_default": sa.text("gen_random_uuid()"), "nullable": False}

# Coherencia pregunta -> criterio: el criterio debe pertenecer a la rúbrica del MISMO caso
# que el escenario de la pregunta. Requiere consultar tres tablas, por eso es un disparador.
_TRIGGER_FUNCTION = """
CREATE FUNCTION fn_pregunta_criterio_mismo_caso() RETURNS trigger AS $$
BEGIN
    IF NEW.criterio_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM criterio_rubrica c
        JOIN rubrica r ON r.id = c.rubrica_id
        JOIN escenario e ON e.caso_id = r.caso_id
        WHERE c.id = NEW.criterio_id AND e.id = NEW.escenario_id
    ) THEN
        RAISE EXCEPTION 'El criterio no pertenece a la rúbrica del caso de la pregunta.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_pregunta_criterio_mismo_caso';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER trg_pregunta_criterio_mismo_caso
BEFORE INSERT OR UPDATE OF criterio_id, escenario_id ON pregunta
FOR EACH ROW EXECUTE FUNCTION fn_pregunta_criterio_mismo_caso();
"""


def upgrade() -> None:
    estado_caso.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "caso_estudio",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("autor_id", sa.UUID(), nullable=False),
        sa.Column("nombre", sa.String(length=200), nullable=False),
        sa.Column("desarrollo_situacional", sa.Text(), nullable=True),
        sa.Column("historia", sa.Text(), nullable=True),
        sa.Column("tiempo_estimado_min", sa.Integer(), nullable=True),
        sa.Column("estado", estado_caso, server_default=sa.text("'BORRADOR'"), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_caso_estudio")),
        sa.ForeignKeyConstraint(
            ["autor_id"], ["usuario.id"], name=op.f("fk_caso_estudio_autor_id_usuario"), ondelete="RESTRICT"
        ),
        sa.CheckConstraint("length(btrim(nombre)) > 0", name=op.f("ck_caso_estudio_nombre_no_vacio")),
        sa.CheckConstraint(
            "tiempo_estimado_min IS NULL OR tiempo_estimado_min > 0", name=op.f("ck_caso_estudio_tiempo_positivo")
        ),
        sa.CheckConstraint(
            "estado = 'BORRADOR' OR (desarrollo_situacional IS NOT NULL AND historia IS NOT NULL "
            "AND tiempo_estimado_min IS NOT NULL)",
            name=op.f("ck_caso_estudio_publicado_con_datos_generales"),
        ),
    )
    op.create_index("ix_caso_estudio_autor_id_estado", "caso_estudio", ["autor_id", "estado"])

    op.create_table(
        "escenario",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("caso_id", sa.UUID(), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=False),
        sa.Column("titulo", sa.String(length=200), nullable=False),
        sa.Column("contexto", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escenario")),
        sa.ForeignKeyConstraint(
            ["caso_id"], ["caso_estudio.id"], name=op.f("fk_escenario_caso_id_caso_estudio"), ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "caso_id", "orden", name="uq_escenario_caso_orden", deferrable=True, initially="IMMEDIATE"
        ),
        sa.CheckConstraint("orden >= 1", name=op.f("ck_escenario_orden_positivo")),
        sa.CheckConstraint("length(btrim(titulo)) > 0", name=op.f("ck_escenario_titulo_no_vacio")),
    )

    op.create_table(
        "rubrica",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("caso_id", sa.UUID(), nullable=False),
        sa.Column("escala_max", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rubrica")),
        sa.ForeignKeyConstraint(
            ["caso_id"], ["caso_estudio.id"], name=op.f("fk_rubrica_caso_id_caso_estudio"), ondelete="CASCADE"
        ),
        sa.UniqueConstraint("caso_id", name=op.f("uq_rubrica_caso_id")),
        sa.CheckConstraint("escala_max > 0", name=op.f("ck_rubrica_escala_positiva")),
    )

    op.create_table(
        "criterio_rubrica",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("rubrica_id", sa.UUID(), nullable=False),
        sa.Column("descripcion", sa.String(length=255), nullable=False),
        sa.Column("peso", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("niveles", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_criterio_rubrica")),
        sa.ForeignKeyConstraint(
            ["rubrica_id"], ["rubrica.id"], name=op.f("fk_criterio_rubrica_rubrica_id_rubrica"), ondelete="CASCADE"
        ),
        sa.CheckConstraint("peso > 0 AND peso <= 100", name=op.f("ck_criterio_rubrica_peso_en_rango")),
        sa.CheckConstraint("length(btrim(descripcion)) > 0", name=op.f("ck_criterio_rubrica_descripcion_no_vacia")),
        sa.CheckConstraint("jsonb_typeof(niveles) = 'array'", name=op.f("ck_criterio_rubrica_niveles_es_arreglo")),
    )
    op.create_index(op.f("ix_criterio_rubrica_rubrica_id"), "criterio_rubrica", ["rubrica_id"])

    op.create_table(
        "pregunta",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("escenario_id", sa.UUID(), nullable=False),
        sa.Column("criterio_id", sa.UUID(), nullable=True),
        sa.Column("orden", sa.Integer(), nullable=False),
        sa.Column("enunciado", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pregunta")),
        sa.ForeignKeyConstraint(
            ["escenario_id"], ["escenario.id"], name=op.f("fk_pregunta_escenario_id_escenario"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["criterio_id"],
            ["criterio_rubrica.id"],
            name=op.f("fk_pregunta_criterio_id_criterio_rubrica"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "escenario_id", "orden", name="uq_pregunta_escenario_orden", deferrable=True, initially="IMMEDIATE"
        ),
        sa.CheckConstraint("orden >= 1", name=op.f("ck_pregunta_orden_positivo")),
        sa.CheckConstraint("length(btrim(enunciado)) > 0", name=op.f("ck_pregunta_enunciado_no_vacio")),
    )
    op.create_index(op.f("ix_pregunta_criterio_id"), "pregunta", ["criterio_id"])

    op.create_table(
        "opcion",
        sa.Column("id", sa.UUID(), **_UUID_PK),
        sa.Column("pregunta_id", sa.UUID(), nullable=False),
        sa.Column("orden", sa.Integer(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("es_correcta", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("justificacion", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opcion")),
        sa.ForeignKeyConstraint(
            ["pregunta_id"], ["pregunta.id"], name=op.f("fk_opcion_pregunta_id_pregunta"), ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "pregunta_id", "orden", name="uq_opcion_pregunta_orden", deferrable=True, initially="IMMEDIATE"
        ),
        sa.CheckConstraint("orden >= 1", name=op.f("ck_opcion_orden_positivo")),
        sa.CheckConstraint("length(btrim(texto)) > 0", name=op.f("ck_opcion_texto_no_vacio")),
    )

    op.execute(_TRIGGER_FUNCTION)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_pregunta_criterio_mismo_caso ON pregunta")
    op.execute("DROP FUNCTION IF EXISTS fn_pregunta_criterio_mismo_caso()")
    op.drop_table("opcion")
    op.drop_index(op.f("ix_pregunta_criterio_id"), table_name="pregunta")
    op.drop_table("pregunta")
    op.drop_index(op.f("ix_criterio_rubrica_rubrica_id"), table_name="criterio_rubrica")
    op.drop_table("criterio_rubrica")
    op.drop_table("rubrica")
    op.drop_table("escenario")
    op.drop_index("ix_caso_estudio_autor_id_estado", table_name="caso_estudio")
    op.drop_table("caso_estudio")
    estado_caso.drop(op.get_bind(), checkfirst=True)
