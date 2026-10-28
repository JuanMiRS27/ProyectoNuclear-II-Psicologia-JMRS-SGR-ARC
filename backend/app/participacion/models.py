import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.enums import EstadoParticipacion
from app.core.tipos import UUIDPk, enum_pg


class Participacion(Base):
    __tablename__ = "participacion"
    __table_args__ = (
        UniqueConstraint(
            "convocatoria_id", "numero_intento", name="uq_participacion_intento"
        ),
        # Una participación en progreso no tiene fin; una cerrada (incluida AUSENTE) sí
        CheckConstraint(
            "(estado = 'EN_PROGRESO' AND fin IS NULL)"
            " OR (estado <> 'EN_PROGRESO' AND fin IS NOT NULL)",
            name="ck_particip_cierre",
        ),
        CheckConstraint("fin IS NULL OR fin >= inicio", name="ck_particip_fechas"),
        CheckConstraint("nota_final IS NULL OR nota_final >= 0", name="ck_particip_nota"),
        CheckConstraint("numero_intento >= 1", name="ck_particip_intento"),
    )

    id: Mapped[UUIDPk]
    convocatoria_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("convocatoria.id"))
    numero_intento: Mapped[int] = mapped_column(Integer, server_default="1")
    inicio: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    fin: Mapped[datetime | None] = mapped_column(DateTime)
    nota_final: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    estado: Mapped[EstadoParticipacion] = mapped_column(
        enum_pg(EstadoParticipacion, "estado_participacion"),
        server_default=EstadoParticipacion.EN_PROGRESO.value,
    )
    retroalimentacion_general: Mapped[str | None] = mapped_column(Text)

    respuestas: Mapped[list["Respuesta"]] = relationship(back_populates="participacion")


class Respuesta(Base):
    __tablename__ = "respuesta"
    __table_args__ = (
        UniqueConstraint("participacion_id", "pregunta_id", name="uq_respuesta"),
        Index("ix_respuesta_participacion", "participacion_id"),
        # La opción elegida debe pertenecer a la misma pregunta respondida
        ForeignKeyConstraint(
            ["opcion_id", "pregunta_id"],
            ["opcion.id", "opcion.pregunta_id"],
            name="fk_respuesta_opcion",
        ),
    )

    id: Mapped[UUIDPk]
    participacion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participacion.id"))
    pregunta_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pregunta.id"))
    opcion_id: Mapped[uuid.UUID]
    respondida_en: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    actualizada_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    participacion: Mapped[Participacion] = relationship(back_populates="respuestas")
