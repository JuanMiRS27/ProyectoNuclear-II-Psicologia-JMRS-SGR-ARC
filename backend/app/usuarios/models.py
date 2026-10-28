from sqlalchemy import Boolean, CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import RolUsuario
from app.core.tipos import FechaCreacion, UUIDPk, enum_pg


class Usuario(Base):
    __tablename__ = "usuario"
    __table_args__ = (
        CheckConstraint(
            "puede_crear_casos = FALSE OR rol = 'DOCENTE'",
            name="ck_usuario_permiso",
        ),
    )

    id: Mapped[UUIDPk]
    nombre_completo: Mapped[str] = mapped_column(String(150))
    correo: Mapped[str] = mapped_column(String(150), unique=True)
    clave_hash: Mapped[str] = mapped_column(String(255))
    rol: Mapped[RolUsuario] = mapped_column(enum_pg(RolUsuario, "rol_usuario"))
    codigo_institucional: Mapped[str | None] = mapped_column(String(30), unique=True)
    puede_crear_casos: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    creado_en: Mapped[FechaCreacion]


class EspacioAcademico(Base):
    __tablename__ = "espacio_academico"
    __table_args__ = (
        UniqueConstraint("nombre", "periodo", name="uq_espacio_nombre_periodo"),
    )

    id: Mapped[UUIDPk]
    nombre: Mapped[str] = mapped_column(String(150))
    periodo: Mapped[str] = mapped_column(String(10))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    creado_en: Mapped[FechaCreacion]
