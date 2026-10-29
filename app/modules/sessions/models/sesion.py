"""Modelo relacional del módulo 3.

Además de lo declarado aquí, la migración 0005 crea dos disparadores que
SQLAlchemy no modela:

- `trg_sesion_programable`: al insertar o cambiar el caso, el docente o el tiempo
  de una sesión, exige que el caso esté PUBLICADO, que `tiempo_max_min` no supere
  su `tiempo_estimado_min` y que el responsable sea un DOCENTE. Bloquea la fila del
  caso (FOR SHARE) para que un archivado concurrente no se cuele.
- `trg_convocatoria_estudiante`: exige que el convocado sea un usuario ESTUDIANTE.

`convocatoria.codigo_autorizacion` guarda el código CIFRADO (AES-256-GCM, migración
0006), no el código en claro de 12 caracteres.

Las convocatorias no se eliminan: retirar a un estudiante fija `revocada_en`
(migración 0007), de modo que la fila sigue disponible para las participaciones del
módulo 4 y para el historial académico.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.auth.models import Usuario
from app.modules.cases.models import TIEMPO_MAXIMO_MIN, CasoEstudio

# Longitud y alfabeto del código de autorización EN CLARO (el que recibe el estudiante).
# Base32 de Crockford: sin I, L, O ni U para evitar confusiones al transcribirlo.
LONGITUD_CODIGO = 12
ALFABETO_CODIGO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# Longitud máxima del valor almacenado: "v1." + 54 caracteres Base64 (AES-GCM), con margen para versiones futuras.
LONGITUD_CODIGO_CIFRADO = 100


class EstadoSesionEnum(str, enum.Enum):
    """Valores persistidos. El comportamiento de cada estado vive en `states/`."""

    SIN_INICIAR = "SIN_INICIAR"
    EN_CURSO = "EN_CURSO"
    FINALIZADO = "FINALIZADO"


estado_sesion_enum = Enum(
    EstadoSesionEnum,
    name="estado_sesion",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class EspacioAcademico(Base):
    __tablename__ = "espacio_academico"
    __table_args__ = (
        CheckConstraint("length(btrim(nombre)) > 0", name="nombre_no_vacio"),
        CheckConstraint("length(btrim(periodo)) > 0", name="periodo_no_vacio"),
        # Unicidad sin distinguir mayúsculas: "Psicología Social"/"psicología social" en 2026-1 es el mismo espacio.
        Index("uq_espacio_academico_nombre_periodo", func.lower(text("nombre")), func.lower(text("periodo")), unique=True),
        Index("ix_espacio_academico_activo", "activo"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    periodo: Mapped[str] = mapped_column(String(10), nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Sesion(Base):
    __tablename__ = "sesion"
    __table_args__ = (
        CheckConstraint("inicio < fin", name="inicio_antes_de_fin"),
        CheckConstraint(f"tiempo_max_min BETWEEN 1 AND {TIEMPO_MAXIMO_MIN}", name="tiempo_max_en_rango"),
        # Cierre automático: sesiones no finalizadas cuya hora de fin ya pasó.
        Index("ix_sesion_estado_fin", "estado", "fin"),
        Index("ix_sesion_docente_id_inicio", "docente_id", "inicio"),
        Index("ix_sesion_caso_id", "caso_id"),
        Index("ix_sesion_espacio_academico_id", "espacio_academico_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    # RESTRICT en todas las referencias: ni un caso, ni un espacio, ni un docente con
    # sesiones puede borrarse, y borrar nada arrastra sesiones en cascada.
    caso_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("caso_estudio.id", ondelete="RESTRICT"), nullable=False
    )
    espacio_academico_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("espacio_academico.id", ondelete="RESTRICT"), nullable=False
    )
    docente_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False
    )
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fin: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tiempo_max_min: Mapped[int] = mapped_column(Integer, nullable=False)
    lugar: Mapped[str | None] = mapped_column(String(150), nullable=True)
    mensaje: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[EstadoSesionEnum] = mapped_column(
        estado_sesion_enum,
        nullable=False,
        default=EstadoSesionEnum.SIN_INICIAR,
        server_default=text("'SIN_INICIAR'"),
    )
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    caso: Mapped[CasoEstudio] = relationship()
    espacio_academico: Mapped[EspacioAcademico] = relationship()
    docente: Mapped[Usuario] = relationship()
    convocatorias: Mapped[list["Convocatoria"]] = relationship(back_populates="sesion", order_by="Convocatoria.creada_en")

    @property
    def limite_ingreso(self) -> datetime:
        """Último instante (incluido) en que un estudiante puede ingresar: la mitad de la ventana."""
        return self.inicio + (self.fin - self.inicio) / 2


class Convocatoria(Base):
    __tablename__ = "convocatoria"
    __table_args__ = (
        UniqueConstraint("sesion_id", "estudiante_id", name="uq_convocatoria_sesion_estudiante"),
        CheckConstraint("intentos_autorizados >= 1", name="intentos_positivos"),
        # Solo se admite el formato cifrado: un código en claro no puede guardarse por error.
        CheckConstraint("codigo_autorizacion ~ '^v[0-9]+\.[A-Za-z0-9_-]+$'", name="codigo_cifrado"),
        Index("ix_convocatoria_estudiante_id", "estudiante_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    # RESTRICT: las convocatorias (y sus futuras participaciones) no se borran en cascada.
    sesion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sesion.id", ondelete="RESTRICT"), nullable=False
    )
    estudiante_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False
    )
    codigo_autorizacion: Mapped[str] = mapped_column(String(LONGITUD_CODIGO_CIFRADO), nullable=False)
    intentos_autorizados: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Revocación lógica: NULL = activa. Una convocatoria revocada no autoriza ingresos ni tokens.
    revocada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sesion: Mapped[Sesion] = relationship(back_populates="convocatorias")
    estudiante: Mapped[Usuario] = relationship()

    @property
    def activa(self) -> bool:
        return self.revocada_en is None

    def __repr__(self) -> str:
        # Nunca incluye el código de autorización.
        return f"<Convocatoria id={self.id} sesion={self.sesion_id} estudiante={self.estudiante_id}>"
