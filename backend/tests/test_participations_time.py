"""Temporizador individual, finalización voluntaria y expiración (perezosa y por worker)."""

import threading
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.modules.participations.services import ExpiracionParticipacionesService, ParticipacionService
from app.modules.sessions.services import AccesoEstudiantilService
from app.workers.participation_expiration import ejecutar_una_vez
from tests.participations_helpers import (  # noqa: F401
    PART_URL,
    en_paralelo,
    entorno,
    fila,
    guardar,
    iniciar_participacion,
)
from tests.sessions_helpers import codigo_de, convocatorias, iniciar, ingresar, programar

T8 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


def _estado(pid):
    return fila("SELECT estado::text AS estado, motivo_cierre::text AS motivo, fin, nota_final FROM participacion WHERE id = :id", id=pid)


# --- Temporizador ---------------------------------------------------------------------------


def test_remaining_time_is_computed_by_server(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    entorno.reloj.avanzar(minutes=15, seconds=30)
    detalle = entorno.client.get(f"{PART_URL}/{p['id']}", headers=headers).json()
    assert detalle["tiempo_restante_segundos"] == 24 * 60 + 30
    assert detalle["fecha_limite"] == p["fecha_limite"]


def test_timer_survives_reconnection_with_new_token(entorno):
    p = entorno.iniciar(entorno.headers())
    entorno.reloj.avanzar(minutes=20)
    nuevo = entorno.headers()  # "cerró el navegador" y volvió a ingresar (todavía dentro de la ventana)
    recuperada = iniciar_participacion(entorno.client, nuevo, entorno.sesion_id)
    assert recuperada.status_code == 200
    assert recuperada.json()["id"] == p["id"] and recuperada.json()["inicio"] == p["inicio"]
    assert recuperada.json()["tiempo_restante_segundos"] == 20 * 60


def test_expires_exactly_at_the_limit_and_keeps_answers(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    q = entorno.preguntas[0]
    assert guardar(entorno.client, headers, p["id"], q["id"], q["opciones"][1]["id"]).status_code == 200

    entorno.reloj.avanzar(minutes=39, seconds=59)
    assert guardar(entorno.client, headers, p["id"], q["id"], q["opciones"][2]["id"]).status_code == 200  # 1 s antes
    entorno.reloj.avanzar(seconds=1)  # ahora == fecha_limite
    response = guardar(entorno.client, headers, p["id"], q["id"], q["opciones"][0]["id"])
    assert response.status_code == 409 and "tiempo" in response.json()["detail"]

    estado = _estado(p["id"])
    assert (estado.estado, estado.motivo) == ("EXPIRADA", "TIEMPO_AGOTADO")
    assert estado.fin == T8 + timedelta(minutes=40) and estado.nota_final is None
    respuestas = entorno.client.get(f"{PART_URL}/{p['id']}/answers", headers=headers).json()
    assert [r["opcion_id"] for r in respuestas] == [q["opciones"][2]["id"]]


def test_reading_after_deadline_closes_the_attempt(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    entorno.reloj.avanzar(minutes=45)
    detalle = entorno.client.get(f"{PART_URL}/{p['id']}", headers=headers).json()
    assert detalle["estado"] == "EXPIRADA" and detalle["tiempo_restante_segundos"] == 0
    assert detalle["fin"].startswith("2026-11-03T08:40:00")  # fin = fecha límite, no el momento de la consulta


def test_attempt_continues_after_session_fin_while_in_progress(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    """Sesión 08:00-08:40 (límite 08:20), tiempo 40 min. Inicia 08:15 → límite individual 08:55 > fin."""
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes[:2], **{
        "inicio": T8.isoformat(), "fin": (T8 + timedelta(minutes=40)).isoformat()
    })
    convs = {c["estudiante"]["correo"]: c for c in convocatorias(client, creador_headers, sesion["id"])}
    codigos = {c: codigo_de(client, creador_headers, sesion["id"], v["id"]) for c, v in convs.items()}
    iniciar(client, creador_headers, sesion["id"], reloj)
    reloj.actual = T8 + timedelta(minutes=15)
    a = estudiantes[0]
    headers = {"Authorization": f"Bearer {ingresar(client, a.correo, codigos[a.correo]).json()['access_token']}"}
    p = iniciar_participacion(client, headers, sesion["id"]).json()
    assert p["fecha_limite"].startswith("2026-11-03T08:55:00")

    caso = client.get(f"/api/v1/cases/{caso_publicado['id']}", headers=creador_headers).json()
    q = caso["escenarios"][0]["preguntas"][0]
    reloj.actual = T8 + timedelta(minutes=50)  # después de sesion.fin (08:40), sesión todavía EN_CURSO
    assert guardar(client, headers, p["id"], q["id"], q["opciones"][0]["id"]).status_code == 200
    # Pero nadie puede empezar un intento nuevo.
    b = estudiantes[1]
    assert ingresar(client, b.correo, codigos[b.correo]).status_code == 403
    reloj.actual = T8 + timedelta(minutes=55)
    assert guardar(client, headers, p["id"], q["id"], q["opciones"][1]["id"]).status_code == 409


# --- Finalización ------------------------------------------------------------------------------


def test_voluntary_finish_incomplete_and_idempotent(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    q = entorno.preguntas[0]
    guardar(entorno.client, headers, p["id"], q["id"], q["opciones"][0]["id"])
    entorno.reloj.avanzar(minutes=10)

    fin1 = entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    assert fin1.status_code == 200
    body = fin1.json()
    assert body["estado"] == "FINALIZADA" and body["motivo_cierre"] == "VOLUNTARIO"
    assert body["fin"].startswith("2026-11-03T08:10:00") and body["progreso"]["respondidas"] == 1

    entorno.reloj.avanzar(minutes=5)
    fin2 = entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers).json()
    assert fin2["estado"] == "FINALIZADA" and fin2["fin"] == body["fin"]  # sin cambios
    assert _estado(p["id"]).nota_final is None


def test_finish_after_deadline_is_expired_not_finished(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    entorno.reloj.avanzar(minutes=41)
    body = entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers).json()
    assert (body["estado"], body["motivo_cierre"]) == ("EXPIRADA", "TIEMPO_AGOTADO")


def test_concurrent_finish_is_consistent(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    token = headers["Authorization"].split()[1]

    def finalizar():
        with SessionLocal() as s:
            acceso = AccesoEstudiantilService(s, entorno.reloj).resolver(token)
            return ParticipacionService(s, entorno.reloj).finalizar(acceso, uuid.UUID(p["id"]))

    a, b = en_paralelo(finalizar, finalizar)
    assert a.estado.value == b.estado.value == "FINALIZADA" and a.fin == b.fin


# --- Worker de expiración ----------------------------------------------------------------------


def test_worker_expires_overdue_attempts_idempotently(entorno):
    p1 = entorno.iniciar(entorno.headers(0))
    entorno.reloj.avanzar(minutes=20)
    p2 = entorno.iniciar(entorno.headers(1))
    entorno.reloj.avanzar(minutes=20)  # 08:40: p1 vence, p2 no

    assert [str(i) for i in ejecutar_una_vez(reloj=entorno.reloj)] == [p1["id"]]
    assert _estado(p1["id"]).fin == T8 + timedelta(minutes=40)
    assert _estado(p2["id"]).estado == "EN_PROGRESO"
    assert ejecutar_una_vez(reloj=entorno.reloj) == []
    entorno.reloj.avanzar(hours=3)
    assert [str(i) for i in ejecutar_una_vez(reloj=entorno.reloj)] == [p2["id"]]
    assert _estado(p2["id"]).fin == T8 + timedelta(minutes=60)


def test_concurrent_workers_never_expire_twice(entorno):
    ids = {entorno.iniciar(entorno.headers(i))["id"] for i in range(3)}
    entorno.reloj.avanzar(hours=1)
    barrera = threading.Barrier(2, timeout=10)

    def expirar():
        with SessionLocal() as s:
            servicio = ExpiracionParticipacionesService(s, entorno.reloj)
            barrera.wait()
            return servicio.expirar_vencidas()

    a, b = en_paralelo(expirar, expirar)
    assert not set(a) & set(b) and {str(i) for i in a + b} == ids


def test_session_auto_close_never_grants_extra_time(entorno):
    from app.workers.session_expiration import ejecutar_una_vez as cerrar_sesiones

    p = entorno.iniciar(entorno.headers())
    entorno.reloj.actual = T8 + timedelta(hours=2, days=1)  # fin + 24 h; el intento venció a las 08:40
    assert len(cerrar_sesiones(reloj=entorno.reloj)) == 1
    estado = _estado(p["id"])
    assert (estado.estado, estado.motivo) == ("EXPIRADA", "TIEMPO_AGOTADO")
    assert estado.fin == T8 + timedelta(minutes=40)


def test_worker_command_line(entorno, monkeypatch):
    from app.workers.participation_expiration import __main__ as worker
    from app.workers.participation_expiration import runner
    from tests.conftest import RelojFijo

    p = entorno.iniciar(entorno.headers())
    monkeypatch.setattr(runner, "RelojSistema", lambda: RelojFijo(T8 + timedelta(hours=1)))
    assert worker.main([]) == 0
    assert _estado(p["id"]).estado == "EXPIRADA"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM participacion WHERE estado = 'EN_PROGRESO'")).scalar_one() == 0
