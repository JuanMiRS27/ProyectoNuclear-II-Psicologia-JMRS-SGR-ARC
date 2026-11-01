from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, BusinessRuleError, PermissionDeniedError
from app.modules.auth.models import RolUsuario, Usuario
from app.modules.auth.repositories import UsuarioRepository
from app.modules.auth.schemas import TokenResponse
from app.modules.auth.schemas.usuario import normalize_email

# Roles que inician sesión con correo y contraseña. El acceso estudiantil
# (correo + código de convocatoria) se incorporará con el módulo 3.
PASSWORD_LOGIN_ROLES = frozenset({RolUsuario.ADMINISTRADOR, RolUsuario.DOCENTE})

INVALID_CREDENTIALS_MESSAGE = "Correo o contraseña incorrectos."
INVALID_TOKEN_MESSAGE = "Token inválido o expirado."


class AuthService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._usuarios = UsuarioRepository(session)

    def login(self, correo: str, clave: str) -> TokenResponse:
        usuario = self._usuarios.get_by_correo(normalize_email(correo))

        # verify_password se ejecuta siempre (con un hash ficticio si no hay usuario)
        # para no revelar por tiempo de respuesta si el correo existe.
        password_ok = security.verify_password(clave, usuario.clave_hash if usuario else None)
        if usuario is None or not password_ok or usuario.rol not in PASSWORD_LOGIN_ROLES:
            raise AuthenticationError(INVALID_CREDENTIALS_MESSAGE)

        # Solo se informa la inactividad a quien demostró conocer la contraseña.
        if not usuario.activo:
            raise PermissionDeniedError("La cuenta está desactivada.")

        if usuario.clave_hash and security.password_needs_rehash(usuario.clave_hash):
            usuario.clave_hash = security.hash_password(clave)
            self._session.commit()

        settings = get_settings()
        token = security.create_access_token(usuario.id, usuario.rol.value, usuario.version_token)
        return TokenResponse(access_token=token, expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)

    def get_user_from_token(self, token: str) -> Usuario:
        """Resuelve el usuario del token consultando su estado ACTUAL en la base de datos.

        Así, desactivar una cuenta o cambiar su contraseña invalida de inmediato
        los tokens emitidos antes, aunque no hayan expirado.
        """
        try:
            claims = security.decode_access_token(token)
        except security.InvalidTokenError as exc:
            raise AuthenticationError(INVALID_TOKEN_MESSAGE) from exc

        usuario = self._usuarios.get_by_id(claims.user_id)
        if usuario is None or not usuario.activo or usuario.version_token != claims.token_version:
            raise AuthenticationError(INVALID_TOKEN_MESSAGE)
        return usuario

    def change_password(self, usuario: Usuario, clave_actual: str, clave_nueva: str) -> None:
        if not security.verify_password(clave_actual, usuario.clave_hash):
            raise BusinessRuleError("La contraseña actual es incorrecta.")
        if clave_actual == clave_nueva:
            raise BusinessRuleError("La nueva contraseña debe ser diferente de la actual.")

        usuario.clave_hash = security.hash_password(clave_nueva)
        # Incremento atómico en SQL: todos los tokens emitidos antes dejan de ser válidos,
        # incluido el usado en esta petición. El cliente debe iniciar sesión de nuevo.
        usuario.version_token = Usuario.version_token + 1
        self._session.commit()
