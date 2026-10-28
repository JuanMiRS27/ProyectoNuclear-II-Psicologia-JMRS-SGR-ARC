"""Esquema inicial: las 13 tablas del modelo relacional

Revision ID: 0001_inicial
Revises:
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_inicial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = (
    "tipo_notificacion",
    "estado_participacion",
    "estado_sesion",
    "estado_caso",
    "rol_usuario",
)


def upgrade() -> None:
    op.create_table('espacio_academico',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('nombre', sa.String(length=150), nullable=False),
    sa.Column('periodo', sa.String(length=10), nullable=False),
    sa.Column('activo', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('creado_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_espacio_academico')),
    sa.UniqueConstraint('nombre', 'periodo', name='uq_espacio_nombre_periodo')
    )
    op.create_table('usuario',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('nombre_completo', sa.String(length=150), nullable=False),
    sa.Column('correo', sa.String(length=150), nullable=False),
    sa.Column('clave_hash', sa.String(length=255), nullable=False),
    sa.Column('rol', sa.Enum('ADMINISTRADOR', 'DOCENTE', 'ESTUDIANTE', name='rol_usuario'), nullable=False),
    sa.Column('codigo_institucional', sa.String(length=30), nullable=True),
    sa.Column('puede_crear_casos', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('activo', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('creado_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("puede_crear_casos = FALSE OR rol = 'DOCENTE'", name='ck_usuario_permiso'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_usuario')),
    sa.UniqueConstraint('codigo_institucional', name=op.f('uq_usuario_codigo_institucional')),
    sa.UniqueConstraint('correo', name=op.f('uq_usuario_correo'))
    )
    op.create_table('caso_estudio',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('autor_id', sa.Uuid(), nullable=False),
    sa.Column('nombre', sa.String(length=200), nullable=False),
    sa.Column('desarrollo_situacional', sa.Text(), nullable=False),
    sa.Column('historia', sa.Text(), nullable=False),
    sa.Column('tiempo_estimado_min', sa.Integer(), nullable=False),
    sa.Column('estado', sa.Enum('BORRADOR', 'PUBLICADO', 'ARCHIVADO', name='estado_caso'), server_default='BORRADOR', nullable=False),
    sa.Column('creado_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('actualizado_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('char_length(desarrollo_situacional) >= 20', name='ck_caso_desarrollo'),
    sa.CheckConstraint('char_length(historia) >= 20', name='ck_caso_historia'),
    sa.CheckConstraint('char_length(nombre) >= 5', name='ck_caso_nombre'),
    sa.CheckConstraint('tiempo_estimado_min BETWEEN 1 AND 240', name='ck_caso_tiempo'),
    sa.ForeignKeyConstraint(['autor_id'], ['usuario.id'], name=op.f('fk_caso_estudio_autor_id_usuario')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_caso_estudio'))
    )
    op.create_table('escenario',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('caso_id', sa.Uuid(), nullable=False),
    sa.Column('orden', sa.Integer(), nullable=False),
    sa.Column('titulo', sa.String(length=200), nullable=True),
    sa.Column('contexto', sa.Text(), nullable=False),
    sa.CheckConstraint('char_length(contexto) >= 20', name='ck_escenario_contexto'),
    sa.CheckConstraint('orden >= 1', name='ck_escenario_orden'),
    sa.ForeignKeyConstraint(['caso_id'], ['caso_estudio.id'], name=op.f('fk_escenario_caso_id_caso_estudio'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_escenario')),
    sa.UniqueConstraint('caso_id', 'orden', name='uq_escenario_orden')
    )
    op.create_table('rubrica',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('caso_id', sa.Uuid(), nullable=False),
    sa.Column('escala_max', sa.Numeric(precision=4, scale=2), server_default='5.00', nullable=False),
    sa.CheckConstraint('escala_max > 0', name='ck_rubrica_escala'),
    sa.ForeignKeyConstraint(['caso_id'], ['caso_estudio.id'], name=op.f('fk_rubrica_caso_id_caso_estudio'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rubrica')),
    sa.UniqueConstraint('caso_id', name=op.f('uq_rubrica_caso_id'))
    )
    op.create_table('sesion',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('caso_id', sa.Uuid(), nullable=False),
    sa.Column('espacio_academico_id', sa.Uuid(), nullable=False),
    sa.Column('docente_id', sa.Uuid(), nullable=False),
    sa.Column('inicio', sa.DateTime(), nullable=False),
    sa.Column('fin', sa.DateTime(), nullable=False),
    sa.Column('tiempo_max_min', sa.Integer(), nullable=False),
    sa.Column('lugar', sa.String(length=150), nullable=True),
    sa.Column('mensaje', sa.Text(), nullable=True),
    sa.Column('estado', sa.Enum('SIN_INICIAR', 'EN_CURSO', 'FINALIZADO', name='estado_sesion'), server_default='SIN_INICIAR', nullable=False),
    sa.Column('creada_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('fin > inicio', name='ck_sesion_fechas'),
    sa.CheckConstraint('tiempo_max_min > 0', name='ck_sesion_tiempo'),
    sa.ForeignKeyConstraint(['caso_id'], ['caso_estudio.id'], name=op.f('fk_sesion_caso_id_caso_estudio')),
    sa.ForeignKeyConstraint(['docente_id'], ['usuario.id'], name=op.f('fk_sesion_docente_id_usuario')),
    sa.ForeignKeyConstraint(['espacio_academico_id'], ['espacio_academico.id'], name=op.f('fk_sesion_espacio_academico_id_espacio_academico')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sesion'))
    )
    op.create_index('ix_sesion_estado_fin', 'sesion', ['estado', 'fin'], unique=False)
    op.create_table('convocatoria',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('sesion_id', sa.Uuid(), nullable=False),
    sa.Column('estudiante_id', sa.Uuid(), nullable=False),
    sa.Column('codigo_autorizacion', sa.String(length=12), nullable=False),
    sa.Column('intentos_autorizados', sa.Integer(), server_default='1', nullable=False),
    sa.Column('creada_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('intentos_autorizados >= 1', name='ck_convocatoria_intentos'),
    sa.ForeignKeyConstraint(['estudiante_id'], ['usuario.id'], name=op.f('fk_convocatoria_estudiante_id_usuario')),
    sa.ForeignKeyConstraint(['sesion_id'], ['sesion.id'], name=op.f('fk_convocatoria_sesion_id_sesion')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_convocatoria')),
    sa.UniqueConstraint('sesion_id', 'estudiante_id', name='uq_convocatoria')
    )
    op.create_index('ix_convocatoria_estudiante', 'convocatoria', ['estudiante_id'], unique=False)
    op.create_table('criterio_rubrica',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('rubrica_id', sa.Uuid(), nullable=False),
    sa.Column('descripcion', sa.String(length=255), nullable=False),
    sa.Column('peso', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('niveles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.CheckConstraint("jsonb_typeof(niveles) = 'array'", name='ck_criterio_niveles'),
    sa.CheckConstraint('char_length(descripcion) >= 5', name='ck_criterio_descripcion'),
    sa.CheckConstraint('peso > 0 AND peso <= 100', name='ck_criterio_peso'),
    sa.ForeignKeyConstraint(['rubrica_id'], ['rubrica.id'], name=op.f('fk_criterio_rubrica_rubrica_id_rubrica'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_criterio_rubrica'))
    )
    op.create_table('participacion',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('convocatoria_id', sa.Uuid(), nullable=False),
    sa.Column('numero_intento', sa.Integer(), server_default='1', nullable=False),
    sa.Column('inicio', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('fin', sa.DateTime(), nullable=True),
    sa.Column('nota_final', sa.Numeric(precision=4, scale=2), nullable=True),
    sa.Column('estado', sa.Enum('EN_PROGRESO', 'FINALIZADA', 'EXPIRADA', 'AUSENTE', name='estado_participacion'), server_default='EN_PROGRESO', nullable=False),
    sa.Column('retroalimentacion_general', sa.Text(), nullable=True),
    sa.CheckConstraint("(estado = 'EN_PROGRESO' AND fin IS NULL) OR (estado <> 'EN_PROGRESO' AND fin IS NOT NULL)", name='ck_particip_cierre'),
    sa.CheckConstraint('fin IS NULL OR fin >= inicio', name='ck_particip_fechas'),
    sa.CheckConstraint('nota_final IS NULL OR nota_final >= 0', name='ck_particip_nota'),
    sa.CheckConstraint('numero_intento >= 1', name='ck_particip_intento'),
    sa.ForeignKeyConstraint(['convocatoria_id'], ['convocatoria.id'], name=op.f('fk_participacion_convocatoria_id_convocatoria')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_participacion')),
    sa.UniqueConstraint('convocatoria_id', 'numero_intento', name='uq_participacion_intento')
    )
    op.create_table('pregunta',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('escenario_id', sa.Uuid(), nullable=False),
    sa.Column('criterio_id', sa.Uuid(), nullable=True),
    sa.Column('orden', sa.Integer(), nullable=False),
    sa.Column('enunciado', sa.Text(), nullable=False),
    sa.CheckConstraint('char_length(enunciado) >= 10', name='ck_pregunta_enunciado'),
    sa.CheckConstraint('orden >= 1', name='ck_pregunta_orden'),
    sa.ForeignKeyConstraint(['criterio_id'], ['criterio_rubrica.id'], name=op.f('fk_pregunta_criterio_id_criterio_rubrica'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['escenario_id'], ['escenario.id'], name=op.f('fk_pregunta_escenario_id_escenario'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pregunta')),
    sa.UniqueConstraint('escenario_id', 'orden', name='uq_pregunta_orden')
    )
    op.create_table('notificacion',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('tipo', sa.Enum('CONVOCATORIA', 'CAMBIO_ESTUDIANTES', 'CALIFICACION', 'RETROALIMENTACION', name='tipo_notificacion'), nullable=False),
    sa.Column('convocatoria_id', sa.Uuid(), nullable=True),
    sa.Column('participacion_id', sa.Uuid(), nullable=True),
    sa.Column('asunto', sa.String(length=200), nullable=False),
    sa.Column('cuerpo', sa.Text(), nullable=False),
    sa.Column('enviada_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("(tipo IN ('CONVOCATORIA', 'CAMBIO_ESTUDIANTES') AND convocatoria_id IS NOT NULL) OR (tipo IN ('CALIFICACION', 'RETROALIMENTACION') AND participacion_id IS NOT NULL)", name='ck_notif_tipo'),
    sa.CheckConstraint('(convocatoria_id IS NOT NULL) <> (participacion_id IS NOT NULL)', name='ck_notif_origen'),
    sa.ForeignKeyConstraint(['convocatoria_id'], ['convocatoria.id'], name=op.f('fk_notificacion_convocatoria_id_convocatoria')),
    sa.ForeignKeyConstraint(['participacion_id'], ['participacion.id'], name=op.f('fk_notificacion_participacion_id_participacion')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notificacion'))
    )
    op.create_table('opcion',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('pregunta_id', sa.Uuid(), nullable=False),
    sa.Column('texto', sa.Text(), nullable=False),
    sa.Column('es_correcta', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('justificacion', sa.Text(), nullable=False),
    sa.CheckConstraint('char_length(justificacion) >= 10', name='ck_opcion_justif'),
    sa.CheckConstraint('char_length(texto) >= 1', name='ck_opcion_texto'),
    sa.ForeignKeyConstraint(['pregunta_id'], ['pregunta.id'], name=op.f('fk_opcion_pregunta_id_pregunta'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_opcion')),
    sa.UniqueConstraint('id', 'pregunta_id', name='uq_opcion_pregunta')
    )
    op.create_table('respuesta',
    sa.Column('id', sa.Uuid(), server_default=sa.text('(gen_random_uuid())'), nullable=False),
    sa.Column('participacion_id', sa.Uuid(), nullable=False),
    sa.Column('pregunta_id', sa.Uuid(), nullable=False),
    sa.Column('opcion_id', sa.Uuid(), nullable=False),
    sa.Column('respondida_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('actualizada_en', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['opcion_id', 'pregunta_id'], ['opcion.id', 'opcion.pregunta_id'], name='fk_respuesta_opcion'),
    sa.ForeignKeyConstraint(['participacion_id'], ['participacion.id'], name=op.f('fk_respuesta_participacion_id_participacion')),
    sa.ForeignKeyConstraint(['pregunta_id'], ['pregunta.id'], name=op.f('fk_respuesta_pregunta_id_pregunta')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_respuesta')),
    sa.UniqueConstraint('participacion_id', 'pregunta_id', name='uq_respuesta')
    )
    op.create_index('ix_respuesta_participacion', 'respuesta', ['participacion_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_respuesta_participacion', table_name='respuesta')
    op.drop_table('respuesta')
    op.drop_table('opcion')
    op.drop_table('notificacion')
    op.drop_table('pregunta')
    op.drop_table('participacion')
    op.drop_table('criterio_rubrica')
    op.drop_index('ix_convocatoria_estudiante', table_name='convocatoria')
    op.drop_table('convocatoria')
    op.drop_index('ix_sesion_estado_fin', table_name='sesion')
    op.drop_table('sesion')
    op.drop_table('rubrica')
    op.drop_table('escenario')
    op.drop_table('caso_estudio')
    op.drop_table('usuario')
    op.drop_table('espacio_academico')
    for nombre in ENUMS:
        sa.Enum(name=nombre).drop(op.get_bind(), checkfirst=True)
