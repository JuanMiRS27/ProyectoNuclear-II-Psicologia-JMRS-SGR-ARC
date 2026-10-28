import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.enums import EstadoSesion
from app.core.tipos import FechaCreacion, UUIDPk, enum_pg


class Sesion(Base):
    __tablename__ = "sesion"
    __table_args__ = (
        CheckConstraint("fin > inicio", name="ck_sesion_fechas"),
        CheckConstraint("tiempo_max_min > 0", name="ck_sesion_tiempo"),
        # Para que el planificador encuentre rápido las sesiones vencidas
        Index("ix_sesion_estado_fin", "estado", "fin"),
    )

    id: Mapped[UUIDPk]
    caso_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("caso_estudio.id"))
    espacio_academico_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("espacio_academico.id")
    )
    docente_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuario.id"))
    inicio: Mapped[datetime] = mapped_column(DateTime)
    fin: Mapped[datetime] = mapped_column(DateTime)
    tiempo_max_min: Mapped[int] = mapped_column(Integer)
    lugar: Mapped[str | None] = mapped_column(String(150))
    mensaje: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[EstadoSesion] = mapped_column(
        enum_pg(EstadoSesion, "estado_sesion"),
        server_default=EstadoSesion.SIN_INICIAR.value,
    )
    creada_en: Mapped[FechaCreacion]

    convocatorias: Mapped[list["Convocatoria"]] = relationship(back_populates="sesion")


class Convocatoria(Base):
    """Resuelve la relación N:M entre sesion y usuario (estudiante)."""

    __tablename__ = "convocatoria"
    __table_args__ = (
        UniqueConstraint("sesion_id", "estudiante_id", name="uq_convocatoria"),
        Index("ix_convocatoria_estudiante", "estudiante_id"),
        CheckConstraint("intentos_autorizados >= 1", name="ck_convocatoria_intentos"),
    )

    id: Mapped[UUIDPk]
    sesion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sesion.id"))
    estudiante_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuario.id"))
    codigo_autorizacion: Mapped[str] = mapped_column(String(12))
    intentos_autorizados: Mapped[int] = mapped_column(Integer, server_default="1")
    creada_en: Mapped[FechaCreacion]

    sesion: Mapped[Sesion] = relationship(back_populates="convocatorias")
