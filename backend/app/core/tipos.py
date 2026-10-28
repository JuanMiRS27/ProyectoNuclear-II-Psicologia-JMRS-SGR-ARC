"""Columnas reutilizables por todos los modelos."""

import enum
import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import DateTime, Enum, func, text
from sqlalchemy.orm import mapped_column

UUIDPk = Annotated[
    uuid.UUID,
    mapped_column(primary_key=True, server_default=text("gen_random_uuid()")),
]

FechaCreacion = Annotated[
    datetime,
    mapped_column(DateTime, nullable=False, server_default=func.now()),
]


def enum_pg(clase: type[enum.Enum], nombre: str) -> Enum:
    """Enum nativo de PostgreSQL que guarda el valor (no el nombre) del miembro."""
    return Enum(
        clase,
        name=nombre,
        values_callable=lambda miembros: [m.value for m in miembros],
    )
