import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.auth.models import RolUsuario, Usuario


class UsuarioRepository:
    """Único punto de acceso a la tabla `usuario`. No contiene reglas de negocio."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, usuario_id: uuid.UUID) -> Usuario | None:
        return self._session.get(Usuario, usuario_id)

    def get_by_id_for_update(self, usuario_id: uuid.UUID) -> Usuario | None:
        """Obtiene el usuario bloqueando su fila hasta el fin de la transacción."""
        stmt = select(Usuario).where(Usuario.id == usuario_id).with_for_update()
        return self._session.scalars(stmt).first()

    def get_by_correo(self, correo: str) -> Usuario | None:
        return self._session.scalars(select(Usuario).where(Usuario.correo == correo)).first()

    def exists_by_correo(self, correo: str, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(Usuario.id).where(Usuario.correo == correo)
        if exclude_id is not None:
            stmt = stmt.where(Usuario.id != exclude_id)
        return self._session.scalars(stmt.limit(1)).first() is not None

    def exists_by_codigo_institucional(self, codigo: str, exclude_id: uuid.UUID | None = None) -> bool:
        stmt = select(Usuario.id).where(Usuario.codigo_institucional == codigo)
        if exclude_id is not None:
            stmt = stmt.where(Usuario.id != exclude_id)
        return self._session.scalars(stmt.limit(1)).first() is not None

    def list(
        self, *, rol: RolUsuario | None = None, activo: bool | None = None, skip: int = 0, limit: int = 50
    ) -> tuple[list[Usuario], int]:
        filters = []
        if rol is not None:
            filters.append(Usuario.rol == rol)
        if activo is not None:
            filters.append(Usuario.activo == activo)

        total = self._session.scalar(select(func.count()).select_from(Usuario).where(*filters)) or 0
        stmt = select(Usuario).where(*filters).order_by(Usuario.creado_en, Usuario.id).offset(skip).limit(limit)
        return list(self._session.scalars(stmt)), total

    def count_active_admins(self) -> int:
        stmt = select(func.count()).select_from(Usuario).where(
            Usuario.rol == RolUsuario.ADMINISTRADOR, Usuario.activo.is_(True)
        )
        return self._session.scalar(stmt) or 0

    def lock_active_admins(self) -> None:
        """Bloquea las filas de administradores activos para serializar cambios que afecten su número.

        El orden fijo por id evita interbloqueos entre transacciones concurrentes.
        """
        stmt = (
            select(Usuario.id)
            .where(Usuario.rol == RolUsuario.ADMINISTRADOR, Usuario.activo.is_(True))
            .order_by(Usuario.id)
            .with_for_update()
        )
        self._session.execute(stmt).all()

    def add(self, usuario: Usuario) -> Usuario:
        self._session.add(usuario)
        self._session.flush()
        return usuario
