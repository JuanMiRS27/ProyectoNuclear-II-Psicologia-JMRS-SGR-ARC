"""Modelo relacional del módulo 2.

Las restricciones de la base de datos solo garantizan integridad que nunca debe
romperse, ni siquiera en un borrador (claves, unicidad de órdenes, rangos de
pesos). La completitud que exige la publicación se comprueba en `validators/`.

Además de lo declarado aquí, la migración 0003 crea el disparador
`trg_pregunta_criterio_mismo_caso`, que impide asociar una pregunta a un
criterio de la rúbrica de OTRO caso (SQLAlchemy no modela disparadores).
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.core.database import Base


# Regla del modelo del proyecto: el tiempo estimado de participación va de 1 a 240 minutos.
# Fuente única para la restricción de PostgreSQL, los esquemas Pydantic y el validador.
TIEMPO_MAXIMO_MIN = 240


class EstadoCasoEnum(str, enum.Enum):
    """Valores persistidos del estado. El comportamiento de cada estado vive en `states/`."""

    BORRADOR = "BORRADOR"
    PUBLICADO = "PUBLICADO"
    ARCHIVADO = "ARCHIVADO"


estado_caso_enum = Enum(
    EstadoCasoEnum,
    name="estado_caso",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class NivelesJSONB(TypeDecorator):
    """JSONB que conserva los valores numéricos de los niveles como `Decimal`.

    En JSONB se guardan como números (consultables desde SQL). Los valores
    tienen como máximo dos decimales y cuatro dígitos, de modo que la
    conversión Decimal -> float -> texto es exacta; al leer se reconstruye
    `Decimal(str(valor))`, sin errores de redondeo binario.
    """

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return _convert_numbers(value, lambda d: float(d) if isinstance(d, Decimal) else d)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return _convert_numbers(value, lambda n: Decimal(str(n)) if isinstance(n, float | int) else n)


def _convert_numbers(value: Any, convert: Any) -> Any:
    if isinstance(value, list):
        return [_convert_numbers(item, convert) for item in value]
    if isinstance(value, dict):
        return {key: _convert_numbers(item, convert) for key, item in value.items()}
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    return convert(value)


class CasoEstudio(Base):
    __tablename__ = "caso_estudio"
    __table_args__ = (
        CheckConstraint("length(btrim(nombre)) > 0", name="nombre_no_vacio"),
        CheckConstraint(
            f"tiempo_estimado_min IS NULL OR tiempo_estimado_min BETWEEN 1 AND {TIEMPO_MAXIMO_MIN}",
            name="tiempo_en_rango",
        ),
        # Un caso que salió de BORRADOR conserva siempre sus datos generales completos.
        CheckConstraint(
            "estado = 'BORRADOR' OR (desarrollo_situacional IS NOT NULL AND historia IS NOT NULL "
            "AND tiempo_estimado_min IS NOT NULL)",
            name="publicado_con_datos_generales",
        ),
        Index("ix_caso_estudio_autor_id_estado", "autor_id", "estado"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    autor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    desarrollo_situacional: Mapped[str | None] = mapped_column(Text, nullable=True)
    historia: Mapped[str | None] = mapped_column(Text, nullable=True)
    tiempo_estimado_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estado: Mapped[EstadoCasoEnum] = mapped_column(
        estado_caso_enum,
        nullable=False,
        default=EstadoCasoEnum.BORRADOR,
        server_default=text("'BORRADOR'"),
    )
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    escenarios: Mapped[list["Escenario"]] = relationship(
        back_populates="caso", order_by="Escenario.orden", cascade="all, delete-orphan", passive_deletes=True
    )
    rubrica: Mapped["Rubrica | None"] = relationship(
        back_populates="caso", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def preguntas(self) -> list["Pregunta"]:
        return [pregunta for escenario in self.escenarios for pregunta in escenario.preguntas]


class Escenario(Base):
    __tablename__ = "escenario"
    __table_args__ = (
        # DEFERRABLE: permite reordenar (intercambiar órdenes) dentro de una transacción.
        UniqueConstraint("caso_id", "orden", deferrable=True, initially="IMMEDIATE", name="uq_escenario_caso_orden"),
        CheckConstraint("orden >= 1", name="orden_positivo"),
        CheckConstraint("length(btrim(titulo)) > 0", name="titulo_no_vacio"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    caso_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("caso_estudio.id", ondelete="CASCADE"), nullable=False
    )
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    contexto: Mapped[str | None] = mapped_column(Text, nullable=True)

    caso: Mapped[CasoEstudio] = relationship(back_populates="escenarios")
    preguntas: Mapped[list["Pregunta"]] = relationship(
        back_populates="escenario", order_by="Pregunta.orden", cascade="all, delete-orphan", passive_deletes=True
    )


class Pregunta(Base):
    __tablename__ = "pregunta"
    __table_args__ = (
        UniqueConstraint(
            "escenario_id", "orden", deferrable=True, initially="IMMEDIATE", name="uq_pregunta_escenario_orden"
        ),
        CheckConstraint("orden >= 1", name="orden_positivo"),
        CheckConstraint("length(btrim(enunciado)) > 0", name="enunciado_no_vacio"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    escenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("escenario.id", ondelete="CASCADE"), nullable=False
    )
    # Nulo mientras el borrador no tenga la asociación; la publicación la exige.
    criterio_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("criterio_rubrica.id", ondelete="SET NULL"), nullable=True, index=True
    )
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    enunciado: Mapped[str] = mapped_column(Text, nullable=False)

    escenario: Mapped[Escenario] = relationship(back_populates="preguntas")
    criterio: Mapped["CriterioRubrica | None"] = relationship()
    opciones: Mapped[list["Opcion"]] = relationship(
        back_populates="pregunta", order_by="Opcion.orden", cascade="all, delete-orphan", passive_deletes=True
    )


class Opcion(Base):
    __tablename__ = "opcion"
    __table_args__ = (
        UniqueConstraint("pregunta_id", "orden", deferrable=True, initially="IMMEDIATE", name="uq_opcion_pregunta_orden"),
        # Migración 0008 (módulo 4): permite la FK compuesta respuesta(opcion_id, pregunta_id) -> opcion(id, pregunta_id),
        # que garantiza en la BD que la opción elegida pertenece a la pregunta respondida.
        UniqueConstraint("id", "pregunta_id", name="uq_opcion_id_pregunta"),
        CheckConstraint("orden >= 1", name="orden_positivo"),
        CheckConstraint("length(btrim(texto)) > 0", name="texto_no_vacio"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    pregunta_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pregunta.id", ondelete="CASCADE"), nullable=False
    )
    # Columna añadida al modelo (ver README): fija el orden de presentación de las opciones.
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    es_correcta: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    justificacion: Mapped[str | None] = mapped_column(Text, nullable=True)

    pregunta: Mapped[Pregunta] = relationship(back_populates="opciones")


class Rubrica(Base):
    __tablename__ = "rubrica"
    __table_args__ = (CheckConstraint("escala_max > 0", name="escala_positiva"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    # UNIQUE: una sola rúbrica por caso.
    caso_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("caso_estudio.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    escala_max: Mapped[Decimal] = mapped_column(Numeric(4, 2, asdecimal=True), nullable=False)

    caso: Mapped[CasoEstudio] = relationship(back_populates="rubrica")
    criterios: Mapped[list["CriterioRubrica"]] = relationship(
        back_populates="rubrica",
        order_by="[CriterioRubrica.descripcion, CriterioRubrica.id]",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CriterioRubrica(Base):
    __tablename__ = "criterio_rubrica"
    __table_args__ = (
        CheckConstraint("peso > 0 AND peso <= 100", name="peso_en_rango"),
        CheckConstraint("length(btrim(descripcion)) > 0", name="descripcion_no_vacia"),
        CheckConstraint("jsonb_typeof(niveles) = 'array'", name="niveles_es_arreglo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    rubrica_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rubrica.id", ondelete="CASCADE"), nullable=False, index=True
    )
    descripcion: Mapped[str] = mapped_column(String(255), nullable=False)
    peso: Mapped[Decimal] = mapped_column(Numeric(5, 2, asdecimal=True), nullable=False)
    niveles: Mapped[list[dict[str, Any]]] = mapped_column(
        NivelesJSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    rubrica: Mapped[Rubrica] = relationship(back_populates="criterios")
