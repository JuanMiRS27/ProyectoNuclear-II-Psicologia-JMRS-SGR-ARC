"""Modelo relacional del módulo 4.

Además de lo declarado aquí, las migraciones 0009 y 0010 crean disparadores que SQLAlchemy
no modela:

- `trg_participacion_insercion`: solo con convocatoria activa, sesión EN_CURSO y
  `numero_intento <= intentos_autorizados` (bloquea la convocatoria con FOR SHARE).
- `trg_participacion_inmutable`: `id`, `inicio`, `numero_intento` y `convocatoria_id` nunca
  cambian; en una participación cerrada `estado`, `fin` y `motivo_cierre` tampoco. Solo
  `nota_final` y `retroalimentacion_general` (módulo 5) siguen siendo modificables tras el
  cierre. No se pueden borrar participaciones.
- `trg_respuesta_valida`: solo se inserta, cambia o borra una respuesta de una
  participación EN_PROGRESO (bloqueándola con FOR SHARE, de modo que un cierre concurrente
  se respeta); una respuesta no cambia de participación ni de pregunta, y la pregunta debe
  pertenecer al caso de su sesión.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.sessions.models import Convocatoria


class EstadoParticipacionEnum(str, enum.Enum):
    EN_PROGRESO = "EN_PROGRESO"
    FINALIZADA = "FINALIZADA"
    EXPIRADA = "EXPIRADA"


class MotivoCierreEnum(str, enum.Enum):
    """Causa del cierre (columna añadida al modelo: la necesita el módulo 5 y no es deducible)."""

    VOLUNTARIO = "VOLUNTARIO"  # el estudiante finalizó (FINALIZADA)
    TIEMPO_AGOTADO = "TIEMPO_AGOTADO"  # se cumplió inicio + tiempo_max_min (EXPIRADA)
    CONVOCATORIA_REVOCADA = "CONVOCATORIA_REVOCADA"  # el docente retiró al estudiante (EXPIRADA)
    SESION_FINALIZADA = "SESION_FINALIZADA"  # cierre manual o automático de la sesión (EXPIRADA)


def _enum(cls, nombre: str) -> Enum:
    return Enum(cls, name=nombre, values_callable=lambda c: [m.value for m in c])


class Participacion(Base):
    __tablename__ = "participacion"
    __table_args__ = (
        UniqueConstraint("convocatoria_id", "numero_intento", name="uq_participacion_convocatoria_intento"),
        CheckConstraint("numero_intento >= 1", name="intento_positivo"),
        CheckConstraint("(estado = 'EN_PROGRESO') = (fin IS NULL)", name="fin_segun_estado"),
        CheckConstraint("fin IS NULL OR fin >= inicio", name="fin_posterior_a_inicio"),
        CheckConstraint(
            "(estado = 'EN_PROGRESO' AND motivo_cierre IS NULL)"
            " OR (estado = 'FINALIZADA' AND motivo_cierre = 'VOLUNTARIO')"
            " OR (estado = 'EXPIRADA' AND motivo_cierre IN ('TIEMPO_AGOTADO', 'CONVOCATORIA_REVOCADA', 'SESION_FINALIZADA'))",
            name="motivo_segun_estado",
        ),
        CheckConstraint("nota_final IS NULL OR nota_final >= 0", name="nota_no_negativa"),
        # Migración 0010: la evaluación (módulo 5) solo se registra sobre intentos cerrados.
        CheckConstraint(
            "estado <> 'EN_PROGRESO' OR (nota_final IS NULL AND retroalimentacion_general IS NULL)",
            name="evaluacion_tras_cierre",
        ),
        # Como máximo una participación EN_PROGRESO por convocatoria.
        Index(
            "uq_participacion_activa_por_convocatoria",
            "convocatoria_id",
            unique=True,
            postgresql_where=text("estado = 'EN_PROGRESO'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    convocatoria_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("convocatoria.id", ondelete="RESTRICT"), nullable=False
    )
    numero_intento: Mapped[int] = mapped_column(Integer, nullable=False)
    # Siempre la asigna el servidor (reloj UTC centralizado); nunca el cliente.
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reservada para el módulo 5: permanece NULL en el módulo 4 (no se registra una nota ficticia).
    nota_final: Mapped[Decimal | None] = mapped_column(Numeric(4, 2, asdecimal=True), nullable=True)
    estado: Mapped[EstadoParticipacionEnum] = mapped_column(
        _enum(EstadoParticipacionEnum, "estado_participacion"),
        nullable=False,
        default=EstadoParticipacionEnum.EN_PROGRESO,
        server_default=text("'EN_PROGRESO'"),
    )
    motivo_cierre: Mapped[MotivoCierreEnum | None] = mapped_column(
        _enum(MotivoCierreEnum, "motivo_cierre_participacion"), nullable=True
    )
    # Reservada para el módulo 5.
    retroalimentacion_general: Mapped[str | None] = mapped_column(Text, nullable=True)

    convocatoria: Mapped[Convocatoria] = relationship()
    respuestas: Mapped[list["Respuesta"]] = relationship(back_populates="participacion")

    @property
    def activa(self) -> bool:
        return self.estado == EstadoParticipacionEnum.EN_PROGRESO


class Respuesta(Base):
    __tablename__ = "respuesta"
    __table_args__ = (
        UniqueConstraint("participacion_id", "pregunta_id", name="uq_respuesta_participacion_pregunta"),
        # FK compuesta: la opción debe pertenecer a la pregunta respondida (requiere uq_opcion_id_pregunta).
        ForeignKeyConstraint(
            ["opcion_id", "pregunta_id"],
            ["opcion.id", "opcion.pregunta_id"],
            name="fk_respuesta_opcion_de_la_pregunta",
            ondelete="RESTRICT",
        ),
        CheckConstraint("actualizada_en >= respondida_en", name="actualizada_posterior"),
        Index("ix_respuesta_pregunta_id", "pregunta_id"),
        Index("ix_respuesta_opcion_id_pregunta_id", "opcion_id", "pregunta_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    participacion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("participacion.id", ondelete="RESTRICT"), nullable=False
    )
    pregunta_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pregunta.id", ondelete="RESTRICT"), nullable=False
    )
    opcion_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    respondida_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    participacion: Mapped[Participacion] = relationship(back_populates="respuestas")
