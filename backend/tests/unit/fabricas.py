"""Entidades transitorias y dobles de prueba para las pruebas unitarias (sin base de datos)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.modules.auth.models import RolUsuario, Usuario
from app.modules.cases.models import CasoEstudio, CriterioRubrica, Escenario, EstadoCasoEnum, Opcion, Pregunta, Rubrica
from app.modules.participations.models import EstadoParticipacionEnum, Participacion
from app.modules.sessions.models import Convocatoria, EstadoSesionEnum, Sesion
from app.modules.sessions.security import cifrar_codigo

T8 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
T9 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
T10 = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
CLAVE = "ClaveSegura#2026"
CODIGO = "ABCDEFGH2345"


def sesion_bd() -> MagicMock:
    return MagicMock(spec=Session)


def error_integridad(restriccion: str | None) -> IntegrityError:
    """IntegrityError como el de psycopg: expone la restricción en `orig.diag.constraint_name`."""
    return IntegrityError("INSERT", {}, SimpleNamespace(diag=SimpleNamespace(constraint_name=restriccion)))


def usuario(rol: RolUsuario = RolUsuario.DOCENTE, **extra) -> Usuario:
    datos = {
        "id": uuid.uuid4(),
        "nombre_completo": "Ana Pérez",
        "correo": "ana@universidad.edu.co",
        "clave_hash": None,
        "rol": rol,
        "activo": True,
        "puede_crear_casos": False,
        "version_token": 0,
    }
    return Usuario(**(datos | extra))


def _opciones() -> list[Opcion]:
    return [
        Opcion(id=uuid.uuid4(), orden=1, texto="Adecuada", es_correcta=True, justificacion="Considera el contexto."),
        Opcion(id=uuid.uuid4(), orden=2, texto="Inadecuada", es_correcta=False, justificacion="Refuerza el sesgo."),
    ]


def _niveles() -> list[dict]:
    return [
        {"nombre": "Bajo", "valor": Decimal("1.00"), "min_aciertos_pct": Decimal("0.00")},
        {"nombre": "Alto", "valor": Decimal("5.00"), "min_aciertos_pct": Decimal("80.00")},
    ]


def caso_valido(estado: EstadoCasoEnum = EstadoCasoEnum.BORRADOR) -> CasoEstudio:
    """Agregado completo que supera toda la cadena de validación de publicación."""
    c1 = CriterioRubrica(id=uuid.uuid4(), descripcion="Análisis", peso=Decimal("60.00"), niveles=_niveles())
    c2 = CriterioRubrica(id=uuid.uuid4(), descripcion="Decisión", peso=Decimal("40.00"), niveles=_niveles())
    return CasoEstudio(
        id=uuid.uuid4(),
        autor_id=uuid.uuid4(),
        nombre="Conformidad en el aula",
        desarrollo_situacional="Un grupo decide bajo presión.",
        historia="Réplica del paradigma de Asch.",
        tiempo_estimado_min=45,
        estado=estado,
        escenarios=[
            Escenario(
                id=uuid.uuid4(),
                orden=1,
                titulo="Primera votación",
                contexto="Todos responden antes que Laura.",
                preguntas=[
                    Pregunta(id=uuid.uuid4(), orden=1, enunciado="¿Qué se observa?", criterio_id=c1.id, opciones=_opciones()),
                    Pregunta(id=uuid.uuid4(), orden=2, enunciado="¿Qué haría?", criterio_id=c2.id, opciones=_opciones()),
                ],
            )
        ],
        rubrica=Rubrica(id=uuid.uuid4(), escala_max=Decimal("5.00"), criterios=[c1, c2]),
    )


def sesion(estado: EstadoSesionEnum = EstadoSesionEnum.EN_CURSO, **extra) -> Sesion:
    """Ventana 08:00-10:00 (límite de ingreso 09:00), 40 minutos por participación."""
    datos = {
        "id": uuid.uuid4(),
        "caso_id": uuid.uuid4(),
        "espacio_academico_id": uuid.uuid4(),
        "docente_id": uuid.uuid4(),
        "inicio": T8,
        "fin": T10,
        "tiempo_max_min": 40,
        "estado": estado,
    }
    return Sesion(**(datos | extra))


def convocatoria(sesion_: Sesion | None = None, estudiante: Usuario | None = None, codigo: str = CODIGO, **extra) -> Convocatoria:
    sesion_ = sesion_ or sesion()
    estudiante = estudiante or usuario(RolUsuario.ESTUDIANTE)
    conv_id = extra.pop("id", uuid.uuid4())
    return Convocatoria(
        id=conv_id,
        sesion=sesion_,
        sesion_id=sesion_.id,
        estudiante=estudiante,
        estudiante_id=estudiante.id,
        codigo_autorizacion=cifrar_codigo(codigo, conv_id),
        intentos_autorizados=extra.pop("intentos_autorizados", 1),
        **extra,
    )


def participacion(estado: EstadoParticipacionEnum = EstadoParticipacionEnum.EN_PROGRESO, **extra) -> Participacion:
    datos = {"id": uuid.uuid4(), "convocatoria_id": uuid.uuid4(), "numero_intento": 1, "inicio": T8, "estado": estado}
    return Participacion(**(datos | extra))


def hash_clave(clave: str = CLAVE) -> str:
    return security.hash_password(clave)
