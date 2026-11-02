"""Autenticación de estudiantes por convocatoria (correo institucional + código de autorización)."""

import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.core import security
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.modules.auth.models import RolUsuario, Usuario
from app.modules.sessions.models import LONGITUD_CODIGO, Convocatoria, Sesion
from app.modules.sessions.schemas import StudentTokenResponse
from app.modules.sessions.security import (
    CodigoCifradoInvalidoError,
    LimitadorIntentos,
    crear_token_estudiante,
    decodificar_token_estudiante,
    descifrar_codigo,
    huella_codigo,
)
from app.modules.sessions.services.base import ServicioSesionesBase
from app.modules.sessions.states import CicloVidaSesion, IngresoNoPermitidoError

logger = logging.getLogger("inter_nos.sesiones.acceso")

# Mismo mensaje para correo inexistente, usuario no estudiante y código incorrecto o de otra persona.
CREDENCIALES_INVALIDAS = "Correo o código de acceso incorrectos."
ACCESO_REVOCADO = "Acceso estudiantil inválido, expirado o revocado."


class PoliticaReingreso(Protocol):
    """Punto de extensión del módulo 4 para recuperar una participación ya iniciada.

    Si el JWT de un estudiante expira durante una participación y la ventana de ingreso
    ya cerró, el login normal lo rechazaría. El módulo 4 implementará esta política
    (p. ej., "tiene una participación abierta y su tiempo individual no ha vencido") para
    emitirle un token nuevo. Ese token NO permite nuevos intentos: iniciar una
    participación sigue exigiendo `AccesoEstudiantil.asegurar_inicio_participacion`.
    """

    def permite_reingreso(self, convocatoria: Convocatoria, ahora: datetime) -> bool: ...


class SinReingreso:
    """Política por defecto del módulo 3 (aún no hay participaciones): nunca hay reingreso fuera de la ventana."""

    def permite_reingreso(self, convocatoria: Convocatoria, ahora: datetime) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class AccesoEstudiantil:
    """Estudiante autenticado para UNA convocatoria. Punto de integración del módulo 4.

    Autenticar (este objeto existe) no equivale a poder iniciar una participación: el
    módulo 4 debe llamar a `asegurar_inicio_participacion(ahora)` antes de crear cada
    `participacion`, y calcular el temporizador con `participacion.inicio` y
    `sesion.tiempo_max_min`.
    """

    estudiante: Usuario
    convocatoria: Convocatoria
    sesion: Sesion

    def asegurar_inicio_participacion(self, ahora: datetime) -> None:
        """Mismas reglas que un ingreso nuevo: EN_CURSO e `inicio <= ahora <= limite_ingreso` (< `fin`)."""
        CicloVidaSesion(self.sesion).autorizar_ingreso(ahora)

    def puede_iniciar_participacion(self, ahora: datetime) -> bool:
        try:
            self.asegurar_inicio_participacion(ahora)
        except PermissionDeniedError:
            return False
        return True


class AccesoEstudiantilService(ServicioSesionesBase):
    def __init__(self, *args, reingreso: PoliticaReingreso | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._reingreso = reingreso or SinReingreso()

    def ingresar(self, correo: str, codigo: str, ip: str, limitador: LimitadorIntentos) -> StudentTokenResponse:
        limitador.verificar(correo, ip)

        convocatoria = self._buscar_por_codigo(correo, codigo) if len(codigo) == LONGITUD_CODIGO else None
        if convocatoria is None:
            limitador.registrar_fallo(correo, ip)
            raise AuthenticationError(CREDENCIALES_INVALIDAS)
        limitador.registrar_exito(correo)

        # A partir de aquí las credenciales son correctas: se puede informar el motivo concreto.
        estudiante, sesion = convocatoria.estudiante, convocatoria.sesion
        if not convocatoria.activa:
            raise PermissionDeniedError("La convocatoria fue revocada por el docente.")
        if not estudiante.activo:
            raise PermissionDeniedError("La cuenta del estudiante está desactivada.")
        self._autorizar_ingreso_o_reingreso(convocatoria)

        # Expiración de seguridad, independiente de `tiempo_max_min` (el temporizador académico es del módulo 4).
        duracion = timedelta(minutes=get_settings().STUDENT_TOKEN_EXPIRE_MINUTES)
        token = crear_token_estudiante(
            estudiante_id=estudiante.id,
            convocatoria_id=convocatoria.id,
            sesion_id=sesion.id,
            version_usuario=estudiante.version_token,
            huella_codigo=huella_codigo(convocatoria.codigo_autorizacion),
            duracion=duracion,
        )
        return StudentTokenResponse(
            access_token=token,
            expires_in=int(duracion.total_seconds()),
            sesion_id=sesion.id,
            convocatoria_id=convocatoria.id,
        )

    def _autorizar_ingreso_o_reingreso(self, convocatoria: Convocatoria) -> None:
        """Ingreso nuevo dentro de la ventana o, fuera de ella, reingreso si la política del módulo 4 lo permite."""
        ahora = self._reloj.ahora()
        ciclo = CicloVidaSesion(convocatoria.sesion)
        try:
            ciclo.autorizar_ingreso(ahora)
        except IngresoNoPermitidoError:
            # Solo con la sesión EN_CURSO: una sesión sin iniciar o finalizada nunca admite reingresos.
            if not (ciclo.mantiene_accesos() and self._reingreso.permite_reingreso(convocatoria, ahora)):
                raise

    def _buscar_por_codigo(self, correo: str, codigo: str) -> Convocatoria | None:
        """Descifra los códigos de las convocatorias del estudiante y compara en tiempo constante.

        Recorre todas (no se detiene en la primera coincidencia). Un código que no se puede
        descifrar (alterado u otra clave) nunca coincide.
        """
        encontrada = None
        for convocatoria in self._convocatorias.de_estudiante_por_correo(correo):
            if convocatoria.estudiante.rol != RolUsuario.ESTUDIANTE:
                continue
            try:
                claro = descifrar_codigo(convocatoria.codigo_autorizacion, convocatoria.id)
            except CodigoCifradoInvalidoError:
                logger.error("codigo cifrado invalido convocatoria=%s", convocatoria.id)
                continue
            if hmac.compare_digest(claro.encode(), codigo.encode()):  # bytes: el código recibido puede no ser ASCII
                encontrada = convocatoria
        return encontrada

    def resolver(self, token: str, sesion_id: uuid.UUID | None = None) -> AccesoEstudiantil:
        """Valida un token estudiantil contra el estado ACTUAL de la base de datos.

        Deja de autorizar si: la convocatoria fue retirada, el código se regeneró, la cuenta
        se desactivó o cambió su `version_token`, o la sesión ya no está EN_CURSO.
        Mientras la sesión siga EN_CURSO, el token sigue autenticando incluso después de
        `fin` (si la participación iniciada puede continuar lo decidirá el módulo 4); lo que
        nunca permite es un ingreso nuevo: ver `AccesoEstudiantil.asegurar_inicio_participacion`.
        Un token de una convocatoria no sirve para otra sesión (403).
        """
        try:
            claims = decodificar_token_estudiante(token)
        except security.InvalidTokenError as exc:
            raise AuthenticationError(ACCESO_REVOCADO) from exc

        convocatoria = self._convocatorias.get_by_id(claims.convocatoria_id)
        if (
            convocatoria is None
            or not convocatoria.activa
            or convocatoria.estudiante_id != claims.estudiante_id
            or convocatoria.sesion_id != claims.sesion_id
            or not hmac.compare_digest(huella_codigo(convocatoria.codigo_autorizacion), claims.huella_codigo)
        ):
            raise AuthenticationError(ACCESO_REVOCADO)

        estudiante, sesion = convocatoria.estudiante, convocatoria.sesion
        if (
            not estudiante.activo
            or estudiante.rol != RolUsuario.ESTUDIANTE
            or estudiante.version_token != claims.version_usuario
            or not CicloVidaSesion(sesion).mantiene_accesos()
        ):
            raise AuthenticationError(ACCESO_REVOCADO)

        if sesion_id is not None and sesion_id != sesion.id:
            raise PermissionDeniedError("El acceso concedido no corresponde a esta sesión.")
        return AccesoEstudiantil(estudiante, convocatoria, sesion)
