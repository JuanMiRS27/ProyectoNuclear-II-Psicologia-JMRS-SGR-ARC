import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ErrorValidacion:
    """Inconsistencia detectada, con lo necesario para señalarla en un frontend.

    - `codigo`: identificador estable y legible por máquina.
    - `componente` / `componente_id`: entidad afectada (None si aún no existe, p. ej. la rúbrica).
    - `campo`: atributo concreto.
    - `ruta`: posición dentro del caso, p. ej. ``escenarios[0].preguntas[2].opciones[1].justificacion``.
    """

    codigo: str
    componente: str
    componente_id: uuid.UUID | None
    campo: str
    ruta: str
    mensaje: str


@dataclass(slots=True)
class ResultadoValidacion:
    errores: list[ErrorValidacion] = field(default_factory=list)

    @property
    def valido(self) -> bool:
        return not self.errores

    def agregar(
        self,
        codigo: str,
        *,
        componente: str,
        componente_id: uuid.UUID | None,
        campo: str,
        ruta: str,
        mensaje: str,
    ) -> None:
        self.errores.append(ErrorValidacion(codigo, componente, componente_id, campo, ruta, mensaje))

    @property
    def codigos(self) -> list[str]:
        return [error.codigo for error in self.errores]
