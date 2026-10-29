"""Tipos reutilizables de los esquemas del módulo 2."""

from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.modules.cases.models.caso import TIEMPO_MAXIMO_MIN

TWO_PLACES = Decimal("0.01")


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES)


# Los decimales se reciben como número o texto y se devuelven como texto ("40.00")
# para no perder precisión en el cliente.
Peso = Annotated[Decimal, Field(gt=0, le=100, max_digits=5, decimal_places=2), AfterValidator(_quantize)]
EscalaMax = Annotated[Decimal, Field(gt=0, max_digits=4, decimal_places=2), AfterValidator(_quantize)]
PorcentajeAciertos = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2), AfterValidator(_quantize)]
ValorNivel = Annotated[Decimal, Field(ge=0, max_digits=4, decimal_places=2), AfterValidator(_quantize)]

Nombre200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Descripcion255 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
NombreNivel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
TextoRequerido = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
# Textos que un borrador puede dejar pendientes; la cadena vacía se guarda como NULL.
TextoOpcional = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20_000)]
TiempoMinutos = Annotated[int, Field(ge=1, le=TIEMPO_MAXIMO_MIN)]
Orden = Annotated[int, Field(ge=1, le=10_000)]


def blank_to_none(value: str | None) -> str | None:
    return value or None


class NivelSchema(BaseModel):
    """Nivel de desempeño de un criterio (CaseDSL).

    Campos obligatorios según el JSON Schema del proyecto: `nombre` y `valor`.
    `min_aciertos_pct` (`minAciertosPct` en la DSL) es OPCIONAL: si se indica, es un
    porcentaje entre 0 y 100 (lo dice su propio nombre). Su interpretación para
    calificar —sobre qué preguntas se calcula y qué ocurre si falta— queda pendiente
    para el módulo 5 (Evaluación); este módulo solo lo almacena y valida su forma.
    """

    model_config = ConfigDict(extra="forbid")

    nombre: NombreNivel
    valor: ValorNivel
    min_aciertos_pct: PorcentajeAciertos | None = None
