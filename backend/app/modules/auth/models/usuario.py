import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RolUsuario(str, enum.Enum):
    ADMINISTRADOR = "ADMINISTRADOR"
    DOCENTE = "DOCENTE"
    ESTUDIANTE = "ESTUDIANTE"


rol_usuario_enum = Enum(
    RolUsuario,
    name="rol_usuario",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Usuario(Base):
    __tablename__ = "usuario"
    __table_args__ = (
        # El correo se almacena normalizado: sin espacios externos y en minúsculas.
        CheckConstraint("correo = lower(btrim(correo))", name="correo_normalizado"),
        CheckConstraint("length(btrim(nombre_completo)) > 0", name="nombre_no_vacio"),
        # Administradores y docentes acceden con contraseña. Los estudiantes
        # accederán mediante convocatoria (módulo 3) y no tienen contraseña.
        CheckConstraint("rol = 'ESTUDIANTE' OR clave_hash IS NOT NULL", name="clave_requerida_por_rol"),
        # El permiso de crear casos solo aplica a docentes.
        CheckConstraint("puede_crear_casos = false OR rol = 'DOCENTE'", name="permiso_casos_solo_docente"),
        CheckConstraint("version_token >= 0", name="version_token_no_negativa"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    nombre_completo: Mapped[str] = mapped_column(String(150), nullable=False)
    correo: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    clave_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rol: Mapped[RolUsuario] = mapped_column(rol_usuario_enum, nullable=False, index=True)
    codigo_institucional: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)
    puede_crear_casos: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Columna técnica (migración 0002): se incrementa al cambiar la contraseña.
    # Los JWT llevan la versión vigente al emitirse; si no coincide, se rechazan.
    version_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    @property
    def es_administrador(self) -> bool:
        return self.rol == RolUsuario.ADMINISTRADOR

    @property
    def es_docente(self) -> bool:
        return self.rol == RolUsuario.DOCENTE
