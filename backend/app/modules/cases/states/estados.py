"""Patrón State: ciclo de vida de un caso académico.

Participantes:
- `EstadoCaso` (abstracción): operaciones cuyo comportamiento depende del estado.
- `EstadoBorrador`, `EstadoPublicado`, `EstadoArchivado` (estados concretos).
- `CicloVidaCaso` (contexto): envuelve al caso, delega en su estado actual y
  persiste el cambio en `caso.estado` (la transacción la confirma el servicio).

Transiciones permitidas:  BORRADOR --publicar--> PUBLICADO --archivar--> ARCHIVADO

El estado NO valida el contenido: para publicar recibe una función que ejecuta la
cadena de validadores. Solo `EstadoBorrador` la invoca; los demás estados
rechazan la operación sin validar nada.
"""

from abc import ABC
from collections.abc import Callable
from typing import ClassVar

from app.core.exceptions import ConflictError, UnprocessableEntityError
from app.modules.cases.models import CasoEstudio, EstadoCasoEnum
from app.modules.cases.validators import ResultadoValidacion


class TransicionNoPermitidaError(ConflictError):
    default_detail = "La operación no está permitida en el estado actual del caso."


class CasoNoPublicableError(UnprocessableEntityError):
    def __init__(self, resultado: ResultadoValidacion) -> None:
        super().__init__(
            "El caso no cumple los requisitos de publicación.",
            extra={"valido": False, "errores": [_error_a_dict(e) for e in resultado.errores]},
        )
        self.resultado = resultado


def _error_a_dict(error) -> dict:
    return {
        "codigo": error.codigo,
        "componente": error.componente,
        "componente_id": error.componente_id,
        "campo": error.campo,
        "ruta": error.ruta,
        "mensaje": error.mensaje,
    }


class EstadoCaso(ABC):
    """Por defecto toda operación se rechaza; cada estado habilita solo las suyas."""

    valor: ClassVar[EstadoCasoEnum]
    descripcion: ClassVar[str]

    def asegurar_contenido_editable(self, contexto: "CicloVidaCaso") -> None:
        raise TransicionNoPermitidaError(f"No se puede modificar el contenido de un caso {self.descripcion}.")

    def asegurar_eliminable(self, contexto: "CicloVidaCaso") -> None:
        raise TransicionNoPermitidaError(f"No se puede eliminar un caso {self.descripcion}.")

    def publicar(self, contexto: "CicloVidaCaso", validar: Callable[[], ResultadoValidacion]) -> None:
        raise TransicionNoPermitidaError(f"No se puede publicar un caso {self.descripcion}.")

    def archivar(self, contexto: "CicloVidaCaso") -> None:
        raise TransicionNoPermitidaError(f"No se puede archivar un caso {self.descripcion}.")

    def admite_programacion(self) -> bool:
        """Si el caso puede seleccionarse al programar sesiones (lo consultará el módulo 3)."""
        return False


class EstadoBorrador(EstadoCaso):
    valor = EstadoCasoEnum.BORRADOR
    descripcion = "en borrador"

    def asegurar_contenido_editable(self, contexto: "CicloVidaCaso") -> None:
        return None

    def asegurar_eliminable(self, contexto: "CicloVidaCaso") -> None:
        return None

    def publicar(self, contexto: "CicloVidaCaso", validar: Callable[[], ResultadoValidacion]) -> None:
        resultado = validar()
        if not resultado.valido:
            # El caso permanece en BORRADOR.
            raise CasoNoPublicableError(resultado)
        contexto.cambiar_a(EstadoPublicado())


class EstadoPublicado(EstadoCaso):
    valor = EstadoCasoEnum.PUBLICADO
    descripcion = "publicado"

    def archivar(self, contexto: "CicloVidaCaso") -> None:
        contexto.cambiar_a(EstadoArchivado())

    def admite_programacion(self) -> bool:
        return True


class EstadoArchivado(EstadoCaso):
    """Estado final: no se modifica ni se programa. No existe transición de regreso a PUBLICADO."""

    valor = EstadoCasoEnum.ARCHIVADO
    descripcion = "archivado"


_ESTADOS: dict[EstadoCasoEnum, type[EstadoCaso]] = {
    estado.valor: estado for estado in (EstadoBorrador, EstadoPublicado, EstadoArchivado)
}


class CicloVidaCaso:
    """Contexto del patrón State."""

    def __init__(self, caso: CasoEstudio) -> None:
        self._caso = caso
        self._estado: EstadoCaso = _ESTADOS[caso.estado]()

    @property
    def caso(self) -> CasoEstudio:
        return self._caso

    @property
    def estado(self) -> EstadoCaso:
        return self._estado

    def cambiar_a(self, nuevo: EstadoCaso) -> None:
        self._estado = nuevo
        self._caso.estado = nuevo.valor

    def asegurar_contenido_editable(self) -> None:
        self._estado.asegurar_contenido_editable(self)

    def asegurar_eliminable(self) -> None:
        self._estado.asegurar_eliminable(self)

    def publicar(self, validar: Callable[[], ResultadoValidacion]) -> None:
        self._estado.publicar(self, validar)

    def archivar(self) -> None:
        self._estado.archivar(self)

    def admite_programacion(self) -> bool:
        return self._estado.admite_programacion()
