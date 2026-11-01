"""Patrón State: ciclo de vida de una sesión (independiente del State de casos del módulo 2).

Participantes:
- `EstadoSesion` (abstracción): operaciones cuyo comportamiento depende del estado.
- `EstadoSinIniciar`, `EstadoEnCurso`, `EstadoFinalizado` (estados concretos).
- `CicloVidaSesion` (contexto): envuelve la sesión, delega en su estado actual y
  persiste el cambio en `sesion.estado` (la transacción la confirma el servicio).

Transiciones:
    SIN_INICIAR --iniciar (docente, inicio <= ahora < fin)--> EN_CURSO
    EN_CURSO   --finalizar (docente)-----------------------> FINALIZADO
    SIN_INICIAR | EN_CURSO --cierre automático (ahora >= fin + plazo)--> FINALIZADO
No existe ninguna transición de salida de FINALIZADO.

Se distinguen tres conceptos:
- Estado administrativo (`sesion.estado`): lo cambian el docente o el cierre automático.
- Ventana de ingreso: nuevos ingresos solo si EN_CURSO y `inicio <= ahora <= limite_ingreso`
  (mitad de la ventana, siempre antes de `fin`). Una sesión que sigue EN_CURSO tras `fin`,
  a la espera del cierre automático (+24 h), NO reabre ni amplía la ventana.
- Duración individual de una participación ya iniciada: la controlará el módulo 4 con
  `participacion.inicio` + `sesion.tiempo_max_min`, no la expiración del token.
"""

from abc import ABC
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import ClassVar

from app.core.exceptions import ConflictError, PermissionDeniedError
from app.modules.sessions.models import EstadoSesionEnum, Sesion

# Datos de la programación que no alteran las condiciones académicas de la actividad.
CAMPOS_INFORMATIVOS = frozenset({"lugar", "mensaje"})


class TransicionSesionNoPermitidaError(ConflictError):
    default_detail = "La operación no está permitida en el estado actual de la sesión."


class IngresoNoPermitidoError(PermissionDeniedError):
    """El estudiante tiene credenciales válidas, pero la sesión no admite su ingreso ahora."""


class EstadoSesion(ABC):
    """Por defecto toda operación se rechaza; cada estado habilita solo las suyas."""

    valor: ClassVar[EstadoSesionEnum]
    descripcion: ClassVar[str]

    def asegurar_programacion_editable(self, contexto: "CicloVidaSesion", campos: Iterable[str]) -> None:
        raise TransicionSesionNoPermitidaError(f"No se puede modificar una sesión {self.descripcion}.")

    def asegurar_convocatorias_editables(self, contexto: "CicloVidaSesion") -> None:
        """Retirar, cambiar intentos o regenerar códigos de convocatorias existentes."""
        raise TransicionSesionNoPermitidaError(f"No se pueden modificar las convocatorias de una sesión {self.descripcion}.")

    def asegurar_nuevas_convocatorias(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        raise TransicionSesionNoPermitidaError(f"No se pueden convocar estudiantes a una sesión {self.descripcion}.")

    def iniciar(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        raise TransicionSesionNoPermitidaError(f"No se puede iniciar una sesión {self.descripcion}.")

    def finalizar(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        raise TransicionSesionNoPermitidaError(f"No se puede finalizar una sesión {self.descripcion}.")

    def cerrar_por_vencimiento(self, contexto: "CicloVidaSesion", ahora: datetime, plazo: timedelta) -> bool:
        """Cierre automático. Devuelve True si cerró la sesión. Por defecto no hace nada (idempotente)."""
        return False

    def autorizar_ingreso(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        """Nuevo ingreso de un estudiante (inicio de sesión estudiantil)."""
        raise IngresoNoPermitidoError(f"La sesión está {self.descripcion}; no admite ingresos.")

    def mantiene_accesos(self) -> bool:
        """Si los tokens estudiantiles ya emitidos siguen autorizando operaciones."""
        return False


class _EstadoAbierto(EstadoSesion):
    """Comportamiento común a los estados no finalizados."""

    def asegurar_convocatorias_editables(self, contexto: "CicloVidaSesion") -> None:
        return None

    def asegurar_nuevas_convocatorias(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        # Convocar a alguien que ya no podría ingresar no tiene utilidad funcional.
        if ahora > contexto.sesion.limite_ingreso:
            raise TransicionSesionNoPermitidaError(
                "La ventana de ingreso de la sesión terminó; ya no se pueden convocar estudiantes."
            )

    def cerrar_por_vencimiento(self, contexto: "CicloVidaSesion", ahora: datetime, plazo: timedelta) -> bool:
        if ahora < contexto.sesion.fin + plazo:
            return False
        contexto.cambiar_a(EstadoFinalizado())
        return True


class EstadoSinIniciar(_EstadoAbierto):
    valor = EstadoSesionEnum.SIN_INICIAR
    descripcion = "sin iniciar"

    def asegurar_programacion_editable(self, contexto: "CicloVidaSesion", campos: Iterable[str]) -> None:
        return None  # toda la programación es editable antes del inicio

    def iniciar(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        sesion = contexto.sesion
        if ahora < sesion.inicio:
            raise TransicionSesionNoPermitidaError("La sesión no puede iniciarse antes de su hora de inicio programada.")
        if ahora >= sesion.fin:
            raise TransicionSesionNoPermitidaError("La ventana programada de la sesión ya terminó; no puede iniciarse.")
        contexto.cambiar_a(EstadoEnCurso())

    def autorizar_ingreso(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        raise IngresoNoPermitidoError("La sesión aún no ha sido iniciada por el docente.")


class EstadoEnCurso(_EstadoAbierto):
    valor = EstadoSesionEnum.EN_CURSO
    descripcion = "en curso"

    def asegurar_programacion_editable(self, contexto: "CicloVidaSesion", campos: Iterable[str]) -> None:
        bloqueados = sorted(set(campos) - CAMPOS_INFORMATIVOS)
        if bloqueados:
            raise TransicionSesionNoPermitidaError(
                f"Con la sesión en curso solo pueden modificarse {sorted(CAMPOS_INFORMATIVOS)}; no {bloqueados}."
            )

    def finalizar(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        contexto.cambiar_a(EstadoFinalizado())

    def autorizar_ingreso(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        sesion = contexto.sesion
        if ahora < sesion.inicio:
            raise IngresoNoPermitidoError("La sesión aún no ha comenzado.")
        if ahora >= sesion.fin:
            # Aunque el cierre automático todavía no la haya finalizado.
            raise IngresoNoPermitidoError("La ventana programada de la sesión terminó; no admite nuevos ingresos.")
        # El instante exacto del límite está permitido.
        if ahora > sesion.limite_ingreso:
            raise IngresoNoPermitidoError("El plazo de ingreso a la sesión terminó (mitad de la ventana programada).")

    def mantiene_accesos(self) -> bool:
        return True


class EstadoFinalizado(EstadoSesion):
    """Estado final: sin ingresos, sin convocatorias, sin cambios y sin reapertura."""

    valor = EstadoSesionEnum.FINALIZADO
    descripcion = "finalizada"

    def autorizar_ingreso(self, contexto: "CicloVidaSesion", ahora: datetime) -> None:
        raise IngresoNoPermitidoError("La sesión ha finalizado.")


_ESTADOS: dict[EstadoSesionEnum, type[EstadoSesion]] = {
    estado.valor: estado for estado in (EstadoSinIniciar, EstadoEnCurso, EstadoFinalizado)
}


class CicloVidaSesion:
    """Contexto del patrón State."""

    def __init__(self, sesion: Sesion) -> None:
        self._sesion = sesion
        self._estado: EstadoSesion = _ESTADOS[sesion.estado]()

    @property
    def sesion(self) -> Sesion:
        return self._sesion

    @property
    def estado(self) -> EstadoSesion:
        return self._estado

    def cambiar_a(self, nuevo: EstadoSesion) -> None:
        self._estado = nuevo
        self._sesion.estado = nuevo.valor

    def asegurar_programacion_editable(self, campos: Iterable[str]) -> None:
        self._estado.asegurar_programacion_editable(self, campos)

    def asegurar_convocatorias_editables(self) -> None:
        self._estado.asegurar_convocatorias_editables(self)

    def asegurar_nuevas_convocatorias(self, ahora: datetime) -> None:
        self._estado.asegurar_nuevas_convocatorias(self, ahora)

    def iniciar(self, ahora: datetime) -> None:
        self._estado.iniciar(self, ahora)

    def finalizar(self, ahora: datetime) -> None:
        self._estado.finalizar(self, ahora)

    def cerrar_por_vencimiento(self, ahora: datetime, plazo: timedelta) -> bool:
        return self._estado.cerrar_por_vencimiento(self, ahora, plazo)

    def autorizar_ingreso(self, ahora: datetime) -> None:
        self._estado.autorizar_ingreso(self, ahora)

    def mantiene_accesos(self) -> bool:
        return self._estado.mantiene_accesos()
