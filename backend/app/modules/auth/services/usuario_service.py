import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.modules.auth.models import RolUsuario, Usuario
from app.modules.auth.repositories import UsuarioRepository
from app.modules.auth.schemas import DocenteCreate, EstudianteCreate, UsuarioUpdate

DUPLICATE_EMAIL_MESSAGE = "Ya existe un usuario registrado con ese correo."
DUPLICATE_CODE_MESSAGE = "Ya existe un usuario registrado con ese código institucional."


class UsuarioService:
    """Reglas de negocio para la gestión de cuentas.

    No existe ninguna operación que cambie el rol de un usuario: así se evita la
    escalación de privilegios por API. Los administradores solo se crean con el
    script `app.scripts.create_admin`.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._usuarios = UsuarioRepository(session)

    # --- Creación -----------------------------------------------------------

    def create_docente(self, data: DocenteCreate) -> Usuario:
        return self._create_with_password(
            nombre_completo=data.nombre_completo,
            correo=data.correo,
            clave=data.clave,
            rol=RolUsuario.DOCENTE,
            codigo_institucional=data.codigo_institucional,
        )

    def create_estudiante(self, data: EstudianteCreate) -> Usuario:
        """Alta administrativa de estudiantes (módulo 3). `clave_hash` queda nulo, como permite el modelo."""
        self._ensure_unique(correo=data.correo, codigo_institucional=data.codigo_institucional)
        usuario = Usuario(
            nombre_completo=data.nombre_completo,
            correo=data.correo,
            clave_hash=None,
            rol=RolUsuario.ESTUDIANTE,
            codigo_institucional=data.codigo_institucional,
            puede_crear_casos=False,
            activo=True,
        )
        with self._transaction():
            self._usuarios.add(usuario)
        return usuario

    def create_admin(
        self, *, nombre_completo: str, correo: str, clave: str, codigo_institucional: str | None = None
    ) -> Usuario:
        """Uso exclusivo del script de inicialización; no se expone por la API."""
        return self._create_with_password(
            nombre_completo=nombre_completo,
            correo=correo,
            clave=clave,
            rol=RolUsuario.ADMINISTRADOR,
            codigo_institucional=codigo_institucional,
        )

    def _create_with_password(
        self, *, nombre_completo: str, correo: str, clave: str, rol: RolUsuario, codigo_institucional: str | None
    ) -> Usuario:
        self._ensure_unique(correo=correo, codigo_institucional=codigo_institucional)
        usuario = Usuario(
            nombre_completo=nombre_completo,
            correo=correo,
            clave_hash=security.hash_password(clave),
            rol=rol,
            codigo_institucional=codigo_institucional,
            puede_crear_casos=False,
            activo=True,
        )
        # El flush de add() ocurre dentro de la transacción protegida: si otra petición
        # insertó el mismo correo entre la verificación previa y el INSERT, se responde 409.
        with self._transaction():
            self._usuarios.add(usuario)
        return usuario

    # --- Consulta -----------------------------------------------------------

    def get(self, usuario_id: uuid.UUID) -> Usuario:
        usuario = self._usuarios.get_by_id(usuario_id)
        if usuario is None:
            raise NotFoundError("Usuario no encontrado.")
        return usuario

    def list(
        self, *, rol: RolUsuario | None, activo: bool | None, skip: int, limit: int
    ) -> tuple[list[Usuario], int]:
        return self._usuarios.list(rol=rol, activo=activo, skip=skip, limit=limit)

    # --- Modificación -------------------------------------------------------

    def update(self, actor: Usuario, usuario_id: uuid.UUID, data: UsuarioUpdate) -> Usuario:
        usuario = self.get(usuario_id)
        self._ensure_not_other_admin(actor, usuario, "No puede modificar los datos de otro administrador.")

        changes = data.model_dump(exclude_unset=True)
        if "nombre_completo" in changes and changes["nombre_completo"] is None:
            raise BusinessRuleError("El nombre completo no puede quedar vacío.")
        if "correo" in changes and changes["correo"] is None:
            raise BusinessRuleError("El correo no puede quedar vacío.")

        self._ensure_unique(
            correo=changes.get("correo"),
            codigo_institucional=changes.get("codigo_institucional"),
            exclude_id=usuario.id,
        )
        with self._transaction():
            for field, value in changes.items():
                setattr(usuario, field, value)
        return usuario

    def set_status(self, actor: Usuario, usuario_id: uuid.UUID, activo: bool) -> Usuario:
        usuario = self.get(usuario_id)
        if usuario.activo == activo:
            return usuario

        if not activo and usuario.id == actor.id:
            raise BusinessRuleError("No puede desactivar su propia cuenta.")

        # Si se lanza la regla de negocio, el rollback libera los bloqueos tomados.
        with self._transaction():
            if not activo and usuario.es_administrador:
                # Bloqueo para que dos desactivaciones concurrentes no dejen el sistema sin administradores.
                self._usuarios.lock_active_admins()
                if self._usuarios.count_active_admins() <= 1:
                    raise BusinessRuleError("El sistema debe conservar al menos un administrador activo.")
            usuario.activo = activo
        return usuario

    def set_case_permission(self, usuario_id: uuid.UUID, puede_crear_casos: bool) -> Usuario:
        usuario = self.get(usuario_id)
        if not usuario.es_docente:
            raise BusinessRuleError("El permiso de creación de casos solo puede asignarse a docentes.")

        with self._transaction():
            usuario.puede_crear_casos = puede_crear_casos
        return usuario

    # --- Auxiliares ---------------------------------------------------------

    @staticmethod
    def _ensure_not_other_admin(actor: Usuario, target: Usuario, message: str) -> None:
        if target.es_administrador and target.id != actor.id:
            raise PermissionDeniedError(message)

    def _ensure_unique(
        self, *, correo: str | None, codigo_institucional: str | None, exclude_id: uuid.UUID | None = None
    ) -> None:
        if correo is not None and self._usuarios.exists_by_correo(correo, exclude_id):
            raise ConflictError(DUPLICATE_EMAIL_MESSAGE)
        if codigo_institucional is not None and self._usuarios.exists_by_codigo_institucional(
            codigo_institucional, exclude_id
        ):
            raise ConflictError(DUPLICATE_CODE_MESSAGE)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Ejecuta el bloque y confirma la transacción.

        Cubre tanto los flush explícitos del repositorio como el commit final: ante
        cualquier excepción se hace rollback, y las violaciones de integridad se
        traducen a errores de aplicación sin exponer detalles de la base de datos.
        """
        try:
            yield
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise self._translate_integrity_error(exc) from exc
        except BaseException:
            self._session.rollback()
            raise

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> Exception:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_usuario_correo":
            return ConflictError(DUPLICATE_EMAIL_MESSAGE)
        if constraint == "uq_usuario_codigo_institucional":
            return ConflictError(DUPLICATE_CODE_MESSAGE)
        return BusinessRuleError("Los datos no cumplen las restricciones de integridad.")
