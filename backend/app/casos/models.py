import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.enums import EstadoCaso
from app.core.tipos import FechaCreacion, UUIDPk, enum_pg


class CasoEstudio(Base):
    __tablename__ = "caso_estudio"
    __table_args__ = (
        CheckConstraint("tiempo_estimado_min BETWEEN 1 AND 240", name="ck_caso_tiempo"),
        CheckConstraint("char_length(nombre) >= 5", name="ck_caso_nombre"),
        CheckConstraint(
            "char_length(desarrollo_situacional) >= 20", name="ck_caso_desarrollo"
        ),
        CheckConstraint("char_length(historia) >= 20", name="ck_caso_historia"),
    )

    id: Mapped[UUIDPk]
    autor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("usuario.id"))
    nombre: Mapped[str] = mapped_column(String(200))
    desarrollo_situacional: Mapped[str] = mapped_column(Text)
    historia: Mapped[str] = mapped_column(Text)
    tiempo_estimado_min: Mapped[int] = mapped_column(Integer)
    estado: Mapped[EstadoCaso] = mapped_column(
        enum_pg(EstadoCaso, "estado_caso"), server_default=EstadoCaso.BORRADOR.value
    )
    creado_en: Mapped[FechaCreacion]
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    escenarios: Mapped[list["Escenario"]] = relationship(
        back_populates="caso",
        order_by="Escenario.orden",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    rubrica: Mapped["Rubrica | None"] = relationship(
        back_populates="caso", cascade="all, delete-orphan", passive_deletes=True
    )


class Escenario(Base):
    __tablename__ = "escenario"
    __table_args__ = (
        UniqueConstraint("caso_id", "orden", name="uq_escenario_orden"),
        CheckConstraint("orden >= 1", name="ck_escenario_orden"),
        CheckConstraint("char_length(contexto) >= 20", name="ck_escenario_contexto"),
    )

    id: Mapped[UUIDPk]
    caso_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("caso_estudio.id", ondelete="CASCADE")
    )
    orden: Mapped[int] = mapped_column(Integer)
    titulo: Mapped[str | None] = mapped_column(String(200))
    contexto: Mapped[str] = mapped_column(Text)

    caso: Mapped[CasoEstudio] = relationship(back_populates="escenarios")
    preguntas: Mapped[list["Pregunta"]] = relationship(
        back_populates="escenario",
        order_by="Pregunta.orden",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Pregunta(Base):
    __tablename__ = "pregunta"
    __table_args__ = (
        UniqueConstraint("escenario_id", "orden", name="uq_pregunta_orden"),
        CheckConstraint("orden >= 1", name="ck_pregunta_orden"),
        CheckConstraint("char_length(enunciado) >= 10", name="ck_pregunta_enunciado"),
    )

    id: Mapped[UUIDPk]
    escenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("escenario.id", ondelete="CASCADE")
    )
    # Mapeo pregunta → criterio de la rúbrica (mapeoPreguntas del DSL)
    criterio_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("criterio_rubrica.id", ondelete="SET NULL")
    )
    orden: Mapped[int] = mapped_column(Integer)
    enunciado: Mapped[str] = mapped_column(Text)

    escenario: Mapped[Escenario] = relationship(back_populates="preguntas")
    opciones: Mapped[list["Opcion"]] = relationship(
        back_populates="pregunta", cascade="all, delete-orphan", passive_deletes=True
    )


class Opcion(Base):
    __tablename__ = "opcion"
    __table_args__ = (
        # Permite la FK compuesta desde respuesta (opcion_id, pregunta_id)
        UniqueConstraint("id", "pregunta_id", name="uq_opcion_pregunta"),
        CheckConstraint("char_length(texto) >= 1", name="ck_opcion_texto"),
        CheckConstraint("char_length(justificacion) >= 10", name="ck_opcion_justif"),
    )

    id: Mapped[UUIDPk]
    pregunta_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pregunta.id", ondelete="CASCADE")
    )
    texto: Mapped[str] = mapped_column(Text)
    es_correcta: Mapped[bool] = mapped_column(Boolean, server_default="false")
    justificacion: Mapped[str] = mapped_column(Text)

    pregunta: Mapped[Pregunta] = relationship(back_populates="opciones")


class Rubrica(Base):
    __tablename__ = "rubrica"
    __table_args__ = (CheckConstraint("escala_max > 0", name="ck_rubrica_escala"),)

    id: Mapped[UUIDPk]
    caso_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("caso_estudio.id", ondelete="CASCADE"), unique=True
    )
    escala_max: Mapped[Decimal] = mapped_column(Numeric(4, 2), server_default="5.00")

    caso: Mapped[CasoEstudio] = relationship(back_populates="rubrica")
    criterios: Mapped[list["CriterioRubrica"]] = relationship(
        back_populates="rubrica", cascade="all, delete-orphan", passive_deletes=True
    )


class CriterioRubrica(Base):
    __tablename__ = "criterio_rubrica"
    __table_args__ = (
        CheckConstraint("peso > 0 AND peso <= 100", name="ck_criterio_peso"),
        CheckConstraint("char_length(descripcion) >= 5", name="ck_criterio_descripcion"),
        CheckConstraint("jsonb_typeof(niveles) = 'array'", name="ck_criterio_niveles"),
    )

    id: Mapped[UUIDPk]
    rubrica_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rubrica.id", ondelete="CASCADE")
    )
    descripcion: Mapped[str] = mapped_column(String(255))
    peso: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    # [{nombre, minAciertosPct, valor}, ...]
    niveles: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    rubrica: Mapped[Rubrica] = relationship(back_populates="criterios")
