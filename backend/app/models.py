"""Registro central de modelos: Alembic importa este módulo para ver las 13 tablas."""

from app.casos.models import (
    CasoEstudio,
    CriterioRubrica,
    Escenario,
    Opcion,
    Pregunta,
    Rubrica,
)
from app.core.db import Base
from app.notificaciones.models import Notificacion
from app.participacion.models import Participacion, Respuesta
from app.sesiones.models import Convocatoria, Sesion
from app.usuarios.models import EspacioAcademico, Usuario

__all__ = [
    "Base",
    "CasoEstudio",
    "Convocatoria",
    "CriterioRubrica",
    "Escenario",
    "EspacioAcademico",
    "Notificacion",
    "Opcion",
    "Participacion",
    "Pregunta",
    "Respuesta",
    "Rubrica",
    "Sesion",
    "Usuario",
]
