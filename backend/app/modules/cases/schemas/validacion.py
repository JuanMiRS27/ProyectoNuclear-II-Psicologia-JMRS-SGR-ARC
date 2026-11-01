import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict

Componente = Literal["caso", "escenario", "pregunta", "opcion", "rubrica", "criterio"]


class ErrorValidacionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    codigo: str
    componente: Componente
    componente_id: uuid.UUID | None
    campo: str
    ruta: str
    mensaje: str


class ResultadoValidacionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    valido: bool
    errores: list[ErrorValidacionRead]
