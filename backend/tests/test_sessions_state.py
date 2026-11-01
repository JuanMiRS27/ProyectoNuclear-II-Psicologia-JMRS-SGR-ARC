"""Patrón State de sesiones, cierre automático, concurrencia, base de datos y migración 0005."""

import ast
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.modules.sessions.models import EstadoSesionEnum, Sesion
from app.modules.sessions.repositories import SesionRepository
from app.modules.sessions.services import CierreSesionesService, SesionService
from app.modules.sessions.states import (
    CicloVidaSesion,
    EstadoEnCurso,
    EstadoFinalizado,
    EstadoSesion,
    EstadoSinIniciar,
    IngresoNoPermitidoError,
    TransicionSesionNoPermitidaError,
)
from app.workers.session_expiration import ejecutar_una_vez
from tests.conftest import PROJECT_ROOT, RelojFijo, alembic_config
from tests.sessions_helpers import SESSIONS_URL, estado_bd, iniciar, programar

T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)  # inicio de la ventana de prueba
PLAZO = timedelta(hours=24)


def _sesion(estado: EstadoSesionEnum = EstadoSesionEnum.SIN_INICIAR) -> Sesion:
    return Sesion(id=uuid.uuid4(), inicio=T0, fin=T0 + timedelta(hours=2), tiempo_max_min=40, estado=estado)


# --- Clases de estado (sin base de datos) ------------------------------------------------


def test_context_selects_concrete_state():
    assert isinstance(CicloVidaSesion(_sesion()).estado, EstadoSinIniciar)
    assert isinstance(CicloVidaSesion(_sesion(EstadoSesionEnum.EN_CURSO)).estado, EstadoEnCurso)
    assert isinstance(CicloVidaSesion(_sesion(EstadoSesionEnum.FINALIZADO)).estado, EstadoFinalizado)
    assert all(issubclass(e, EstadoSesion) for e in (EstadoSinIniciar, EstadoEnCurso, EstadoFinalizado))


def test_start_respects_time_window():
    ciclo = CicloVidaSesion(_sesion())
    with pytest.raises(TransicionSesionNoPermitidaError, match="antes de su hora"):
        ciclo.iniciar(T0 - timedelta(seconds=1))
    with pytest.raises(TransicionSesionNoPermitidaError, match="ya terminó"):
        CicloVidaSesion(_sesion()).iniciar(T0 + timedelta(hours=2))
    ciclo.iniciar(T0)
    assert ciclo.sesion.estado == EstadoSesionEnum.EN_CURSO and isinstance(ciclo.estado, EstadoEnCurso)


def test_finish_only_from_in_progress_and_no_reopening():
    with pytest.raises(TransicionSesionNoPermitidaError):
        CicloVidaSesion(_sesion()).finalizar(T0)
    ciclo = CicloVidaSesion(_sesion(EstadoSesionEnum.EN_CURSO))
    ciclo.finalizar(T0)
    assert ciclo.sesion.estado == EstadoSesionEnum.FINALIZADO
    for operacion in (lambda: ciclo.iniciar(T0), lambda: ciclo.finalizar(T0), ciclo.asegurar_convocatorias_editables):
        with pytest.raises(TransicionSesionNoPermitidaError):
            operacion()
    with pytest.raises(TransicionSesionNoPermitidaError):
        ciclo.asegurar_programacion_editable(["mensaje"])


def test_entry_rules_per_state():
    limite = T0 + timedelta(hours=1)
    with pytest.raises(IngresoNoPermitidoError, match="no ha sido iniciada"):
        CicloVidaSesion(_sesion()).autorizar_ingreso(T0)
    en_curso = CicloVidaSesion(_sesion(EstadoSesionEnum.EN_CURSO))
    en_curso.autorizar_ingreso(limite)  # el límite exacto está permitido
    with pytest.raises(IngresoNoPermitidoError, match="plazo"):
        en_curso.autorizar_ingreso(limite + timedelta(microseconds=1))
    with pytest.raises(IngresoNoPermitidoError, match="finalizado"):
        CicloVidaSesion(_sesion(EstadoSesionEnum.FINALIZADO)).autorizar_ingreso(T0)
    assert [CicloVidaSesion(_sesion(e)).mantiene_accesos() for e in EstadoSesionEnum] == [False, True, False]


def test_programming_permissions_per_state():
    CicloVidaSesion(_sesion()).asegurar_programacion_editable(["inicio", "caso_id", "tiempo_max_min"])
    en_curso = CicloVidaSesion(_sesion(EstadoSesionEnum.EN_CURSO))
    en_curso.asegurar_programacion_editable(["lugar", "mensaje"])
    with pytest.raises(TransicionSesionNoPermitidaError, match="inicio"):
        en_curso.asegurar_programacion_editable(["lugar", "inicio"])


@pytest.mark.parametrize("estado", [EstadoSesionEnum.SIN_INICIAR, EstadoSesionEnum.EN_CURSO])
def test_automatic_close_after_grace_period(estado):
    fin = T0 + timedelta(hours=2)
    ciclo = CicloVidaSesion(_sesion(estado))
    assert ciclo.cerrar_por_vencimiento(fin + PLAZO - timedelta(seconds=1), PLAZO) is False
    assert ciclo.sesion.estado == estado
    assert ciclo.cerrar_por_vencimiento(fin + PLAZO, PLAZO) is True
    assert ciclo.sesion.estado == EstadoSesionEnum.FINALIZADO
    assert ciclo.cerrar_por_vencimiento(fin + PLAZO * 2, PLAZO) is False  # idempotente


# --- Transiciones vía API ----------------------------------------------------------------


@pytest.fixture
def sesion(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    return programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)


def test_start_and_finish_persist_state(client, creador_headers, reloj, sesion):
    url = f"{SESSIONS_URL}/{sesion['id']}"
    response = client.post(f"{url}/start", headers=creador_headers)
    assert response.status_code == 409 and "antes de su hora" in response.json()["detail"]  # 07:00 < 08:00

    reloj.avanzar(hours=1)
    assert client.post(f"{url}/start", headers=creador_headers).json()["estado"] == "EN_CURSO"
    assert estado_bd(sesion["id"]) == "EN_CURSO"
    assert client.post(f"{url}/finish", headers=creador_headers).json()["estado"] == "FINALIZADO"
    assert estado_bd(sesion["id"]) == "FINALIZADO"


def test_invalid_transitions_via_api(client, creador_headers, reloj, sesion):
    url = f"{SESSIONS_URL}/{sesion['id']}"
    assert client.post(f"{url}/finish", headers=creador_headers).status_code == 409  # SIN_INICIAR -> FINALIZADO manual
    reloj.avanzar(hours=3)  # 10:00: la ventana terminó
    assert client.post(f"{url}/start", headers=creador_headers).status_code == 409
    assert estado_bd(sesion["id"]) == "SIN_INICIAR"


def test_finished_session_cannot_be_reopened(client, creador_headers, reloj, sesion):
    url = f"{SESSIONS_URL}/{sesion['id']}"
    iniciar(client, creador_headers, sesion["id"], reloj)
    client.post(f"{url}/finish", headers=creador_headers)
    response = client.post(f"{url}/start", headers=creador_headers)
    assert response.status_code == 409 and response.json() == {"detail": "No se puede iniciar una sesión finalizada."}
    assert client.post(f"{url}/finish", headers=creador_headers).status_code == 409


def test_only_responsible_teacher_changes_state(client, otro_creador_headers, estudiante_headers, reloj, sesion):
    reloj.avanzar(hours=1)
    assert client.post(f"{SESSIONS_URL}/{sesion['id']}/start", headers=otro_creador_headers).status_code == 404
    assert client.post(f"{SESSIONS_URL}/{sesion['id']}/start", headers=estudiante_headers).status_code == 403
    assert estado_bd(sesion["id"]) == "SIN_INICIAR"


# --- Concurrencia ------------------------------------------------------------------------


def _en_paralelo(*tareas):
    resultados = [None] * len(tareas)

    def ejecutar(i, tarea):
        try:
            resultados[i] = tarea()
        except Exception as exc:  # noqa: BLE001
            resultados[i] = exc

    hilos = [threading.Thread(target=ejecutar, args=(i, t)) for i, t in enumerate(tareas)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=30)
        assert not h.is_alive()
    return resultados


@pytest.fixture
def barrera(monkeypatch):
    barrera = threading.Barrier(2, timeout=10)
    original = SesionRepository.get_owned

    def get_owned(self, *args, **kwargs):
        if kwargs.get("for_update"):
            barrera.wait()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SesionRepository, "get_owned", get_owned)
    return barrera


def _tarea(operacion, docente, sesion_id, reloj):
    def ejecutar():
        with SessionLocal() as s:
            return getattr(SesionService(s, reloj), operacion)(docente, sesion_id)

    return ejecutar


def test_concurrent_starts_only_one_succeeds(creador, reloj, sesion, barrera):
    reloj.avanzar(hours=1)
    sesion_id = uuid.UUID(sesion["id"])
    resultados = _en_paralelo(_tarea("iniciar", creador, sesion_id, reloj), _tarea("iniciar", creador, sesion_id, reloj))
    assert sum(isinstance(r, Sesion) for r in resultados) == 1
    assert sum(isinstance(r, TransicionSesionNoPermitidaError) for r in resultados) == 1
    assert estado_bd(sesion_id) == "EN_CURSO"


def test_concurrent_finish_and_convocation_are_serialized(creador, reloj, sesion, barrera, make_user):
    from app.modules.auth.models import RolUsuario
    from app.modules.sessions.services import ConvocatoriaService

    reloj.avanzar(hours=1)
    sesion_id = uuid.UUID(sesion["id"])
    # Se pone EN_CURSO directamente: iniciar por el servicio pasaría por la barrera de esta prueba.
    with SessionLocal() as s:
        s.execute(text("UPDATE sesion SET estado = 'EN_CURSO' WHERE id = :id"), {"id": sesion_id})
        s.commit()
    nuevo = make_user(RolUsuario.ESTUDIANTE, clave=None)

    def convocar():
        with SessionLocal() as s:
            return ConvocatoriaService(s, reloj).agregar(creador, sesion_id, [nuevo.correo])

    finalizacion, convocatoria = _en_paralelo(_tarea("finalizar", creador, sesion_id, reloj), convocar)
    assert isinstance(finalizacion, Sesion), finalizacion
    with engine.connect() as conn:
        convocado = conn.execute(
            text("SELECT count(*) FROM convocatoria WHERE sesion_id = :s AND estudiante_id = :e"), {"s": sesion_id, "e": nuevo.id}
        ).scalar_one()
    # O se convocó antes de finalizar, o fue rechazada por la sesión ya finalizada. Nunca ambas cosas a medias.
    if isinstance(convocatoria, TransicionSesionNoPermitidaError):
        assert convocado == 0
    else:
        assert convocado == 1
    assert estado_bd(sesion_id) == "FINALIZADO"


# --- Cierre automático --------------------------------------------------------------------


def _crear_sesiones_vencidas(client, creador_headers, reloj, caso_id, espacio_id):
    """Crea una sesión SIN_INICIAR y otra EN_CURSO; devuelve sus ids."""
    sin_iniciar = programar(client, creador_headers, reloj, caso_id, espacio_id)
    en_curso = programar(client, creador_headers, reloj, caso_id, espacio_id)
    iniciar(client, creador_headers, en_curso["id"], reloj)
    return sin_iniciar["id"], en_curso["id"]


def test_automatic_close_of_expired_sessions(client, creador_headers, reloj, espacio, caso_publicado):
    sin_iniciar, en_curso = _crear_sesiones_vencidas(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    futura = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, **{
        "inicio": (reloj.actual + timedelta(days=5)).isoformat(), "fin": (reloj.actual + timedelta(days=5, hours=2)).isoformat()
    })

    # 10:00 + 24 h - 1 s: todavía no corresponde cerrar.
    reloj.actual = datetime(2026, 11, 4, 9, 59, 59, tzinfo=UTC)
    assert ejecutar_una_vez(reloj=reloj) == []

    reloj.actual = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
    cerradas = ejecutar_una_vez(reloj=reloj)
    assert {str(i) for i in cerradas} == {sin_iniciar, en_curso}
    assert estado_bd(sin_iniciar) == estado_bd(en_curso) == "FINALIZADO"
    assert estado_bd(futura["id"]) == "SIN_INICIAR"


def test_automatic_close_is_idempotent(client, creador_headers, reloj, espacio, caso_publicado):
    _crear_sesiones_vencidas(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    reloj.avanzar(days=2)
    assert len(ejecutar_una_vez(reloj=reloj)) == 2
    assert ejecutar_una_vez(reloj=reloj) == []
    assert ejecutar_una_vez(reloj=reloj) == []


def test_automatic_close_grace_period_is_configurable(client, creador_headers, reloj, espacio, caso_publicado, monkeypatch):
    from app.core.config import get_settings

    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    monkeypatch.setattr(get_settings(), "SESSION_AUTO_CLOSE_HOURS", 1)
    reloj.actual = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)  # fin 10:00 + 1 h
    assert [str(i) for i in ejecutar_una_vez(reloj=reloj)] == [sesion["id"]]


def test_concurrent_automatic_closers_never_finish_twice(client, creador_headers, reloj, espacio, caso_publicado):
    ids = {programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)["id"] for _ in range(6)}
    reloj.avanzar(days=2)
    barrera = threading.Barrier(2, timeout=10)

    def cerrar():
        with SessionLocal() as s:
            servicio = CierreSesionesService(s, reloj)
            barrera.wait()
            return servicio.cerrar_vencidas()

    a, b = _en_paralelo(cerrar, cerrar)
    assert not set(a) & set(b)  # ninguna sesión la cerró dos veces
    assert {str(i) for i in a + b} == ids
    assert {estado_bd(i) for i in ids} == {"FINALIZADO"}


def test_worker_command_runs_without_http(client, creador_headers, reloj, espacio, caso_publicado, monkeypatch):
    from app.workers.session_expiration import __main__ as worker
    from app.workers.session_expiration import runner

    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    monkeypatch.setattr(runner, "RelojSistema", lambda: RelojFijo(reloj.actual + timedelta(days=2)))
    assert worker.main([]) == 0
    assert estado_bd(sesion["id"]) == "FINALIZADO"


# --- Base de datos y migración ---------------------------------------------------------------


def test_database_constraints(sesion, caso_publicado):
    for sql, nombre in (
        ("UPDATE sesion SET fin = inicio", "ck_sesion_inicio_antes_de_fin"),
        ("UPDATE sesion SET tiempo_max_min = 0", "ck_sesion_tiempo_max_en_rango"),
        ("UPDATE sesion SET tiempo_max_min = 46", "ck_sesion_tiempo_max_hasta_estimado"),
        ("UPDATE convocatoria SET intentos_autorizados = 0", "ck_convocatoria_intentos_positivos"),
    ):
        with pytest.raises(IntegrityError) as info, engine.begin() as conn:
            conn.execute(text(sql))
        assert info.value.orig.diag.constraint_name == nombre, sql


def test_trigger_blocks_unpublished_case_and_non_teacher(client, creador_headers, sesion, docente, estudiantes):
    from tests.cases_helpers import crear_caso_completo

    borrador = crear_caso_completo(client, creador_headers)
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        conn.execute(text("UPDATE sesion SET caso_id = :c"), {"c": borrador["id"]})
    assert info.value.orig.diag.constraint_name == "ck_sesion_caso_publicado"
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        conn.execute(text("UPDATE sesion SET docente_id = :d"), {"d": estudiantes[0].id})
    assert info.value.orig.diag.constraint_name == "ck_sesion_responsable_docente"


def test_no_cascade_deletes(sesion, caso_publicado, espacio, creador):
    for sql, params in (
        ("DELETE FROM caso_estudio WHERE id = :id", {"id": caso_publicado["id"]}),
        ("DELETE FROM espacio_academico WHERE id = :id", {"id": espacio.id}),
        ("DELETE FROM sesion WHERE id = :id", {"id": sesion["id"]}),  # tiene convocatorias
        ("DELETE FROM usuario WHERE id = :id", {"id": creador.id}),
    ):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(sql), params)


def test_migration_0005_schema():
    inspector = inspect(engine)
    assert all(inspector.has_table(t) for t in ("espacio_academico", "sesion", "convocatoria"))
    indices = {i["name"]: i["column_names"] for i in inspector.get_indexes("sesion")}
    assert indices["ix_sesion_estado_fin"] == ["estado", "fin"]
    columnas = {c["name"]: c for c in inspector.get_columns("sesion")}
    assert columnas["inicio"]["type"].timezone is True and columnas["fin"]["type"].timezone is True
    # 0005 creó VARCHAR(12); en head (0006) la columna guarda el código cifrado.
    assert str({c["name"]: c for c in inspector.get_columns("convocatoria")}["codigo_autorizacion"]["type"]) == "VARCHAR(100)"
    fks = {fk["referred_table"]: fk["options"].get("ondelete") for fk in inspector.get_foreign_keys("sesion")}
    assert fks == {"caso_estudio": "RESTRICT", "espacio_academico": "RESTRICT", "usuario": "RESTRICT"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT unnest(enum_range(NULL::estado_sesion))::text")).scalars().all() == [
            "SIN_INICIAR", "EN_CURSO", "FINALIZADO"
        ]


def test_downgrade_to_0004_removes_module_3_and_keeps_data(caso_publicado, make_user):
    usuario = make_user()
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "0004_tiempo_max_240")
    try:
        inspector = inspect(engine)
        assert not any(inspector.has_table(t) for t in ("espacio_academico", "sesion", "convocatoria"))
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM usuario WHERE id = :id"), {"id": usuario.id}).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM caso_estudio WHERE id = :id"), {"id": caso_publicado["id"]}).scalar_one() == 1
            assert conn.execute(text("SELECT to_regtype('estado_sesion')")).scalar() is None
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_migration_0005_is_self_contained():
    arbol = ast.parse((PROJECT_ROOT / "alembic" / "versions" / "0005_sesiones_convocatorias.py").read_text(encoding="utf-8"))
    importados = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any(m == "app" or m.startswith("app.") for m in importados)
