"""Módulo 4 (ajuste): evaluación solo tras el cierre y respuestas protegidas ante cierres concurrentes.

Revision ID: 0010_evaluacion_tras_cierre
Revises: 0009_participaciones
Create Date: 2026-10-29

0009 ya permitía registrar `nota_final` y `retroalimentacion_general` en participaciones
cerradas. Esta revisión, sin modificar 0009, cierra tres huecos:

- CHECK `ck_participacion_evaluacion_tras_cierre`: un intento EN_PROGRESO no puede tener
  nota ni retroalimentación (la evaluación del módulo 5 llega después del cierre).
- `fn_participacion_inmutable`: además de inicio, número de intento y convocatoria,
  tampoco cambia el `id` de la participación.
- `fn_respuesta_valida`:
  * en UPDATE, una respuesta no puede cambiar de participación ni de pregunta (antes se
    validaba solo la participación NUEVA, y una respuesta de un intento cerrado podía
    "moverse" a uno abierto);
  * la comprobación de "participación EN_PROGRESO" bloquea la fila con FOR SHARE: si otra
    transacción la está cerrando, se espera a que confirme y se reevalúa el estado, así que
    ninguna escritura directa sobre `respuesta` se cuela tras un cierre concurrente.

Autocontenida: no importa código de la aplicación.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_evaluacion_tras_cierre"
down_revision: str | None = "0009_participaciones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INMUTABLE_0010 = """
CREATE OR REPLACE FUNCTION fn_participacion_inmutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Las participaciones no se eliminan.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_participacion_sin_borrado';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.inicio IS DISTINCT FROM OLD.inicio
       OR NEW.numero_intento IS DISTINCT FROM OLD.numero_intento
       OR NEW.convocatoria_id IS DISTINCT FROM OLD.convocatoria_id THEN
        RAISE EXCEPTION 'El identificador, el inicio, el número de intento y la convocatoria no se modifican.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_participacion_datos_inmutables';
    END IF;
    -- Cerrada: estado, fin y motivo quedan fijos; nota_final y retroalimentacion_general no.
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
"""

_RESPUESTA_0010 = """
CREATE OR REPLACE FUNCTION fn_respuesta_valida() RETURNS trigger AS $$
DECLARE
    v_participacion uuid;
BEGIN
    IF TG_OP = 'UPDATE'
       AND (NEW.participacion_id IS DISTINCT FROM OLD.participacion_id
            OR NEW.pregunta_id IS DISTINCT FROM OLD.pregunta_id) THEN
        RAISE EXCEPTION 'Una respuesta no cambia de participación ni de pregunta.'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_respuesta_identidad_inmutable';
    END IF;
    v_participacion := CASE WHEN TG_OP = 'DELETE' THEN OLD.participacion_id ELSE NEW.participacion_id END;
    -- FOR SHARE: espera a un cierre concurrente y reevalúa el estado ya confirmado.
    PERFORM 1 FROM participacion WHERE id = v_participacion AND estado = 'EN_PROGRESO' FOR SHARE;
    IF NOT FOUND THEN
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
"""

# Copias literales de las funciones de 0009, para el downgrade.
_INMUTABLE_0009 = """
CREATE OR REPLACE FUNCTION fn_participacion_inmutable() RETURNS trigger AS $$
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
"""

_RESPUESTA_0009 = """
CREATE OR REPLACE FUNCTION fn_respuesta_valida() RETURNS trigger AS $$
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
"""


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_participacion_evaluacion_tras_cierre"),
        "participacion",
        "estado <> 'EN_PROGRESO' OR (nota_final IS NULL AND retroalimentacion_general IS NULL)",
    )
    op.execute(_INMUTABLE_0010)
    op.execute(_RESPUESTA_0010)


def downgrade() -> None:
    op.execute(_RESPUESTA_0009)
    op.execute(_INMUTABLE_0009)
    op.drop_constraint(op.f("ck_participacion_evaluacion_tras_cierre"), "participacion", type_="check")
