"""Revisión final del módulo 4: evaluación tras el cierre (módulo 5) y vencimiento bajo concurrencia."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.core.exceptions import ConflictError, PermissionDeniedError
from app.modules.participations.services import ParticipacionService, RespuestaService
from app.modules.sessions import integracion
from app.modules.sessions.services import AccesoEstudiantilService
from app.workers.participation_expiration import ejecutar_una_vez
from tests.participations_helpers import PART_URL, contar, entorno, fila, guardar  # noqa: F401
from tests.sessions_helpers import SESSIONS_URL

T8 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


def _estado(pid):
    return fila(
        "SELECT estado::text AS estado, motivo_cierre::text AS motivo, fin, inicio, numero_intento, convocatoria_id,"
        " nota_final, retroalimentacion_general FROM participacion WHERE id = :id",
        id=pid,
    )


def _sql(sql, **params):
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def _falla(sql, restriccion, **params):
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        conn.execute(text(sql), params)
    assert info.value.orig.diag.constraint_name == restriccion, sql


def _calificar(pid, nota=None, retro=None):
    """Lo que hará el módulo 5: solo nota y retroalimentación, directamente en la base."""
    _sql(
        "UPDATE participacion SET nota_final = COALESCE(:n, nota_final),"
        " retroalimentacion_general = COALESCE(:r, retroalimentacion_general) WHERE id = :id",
        n=nota, r=retro, id=pid,
    )


def _token(headers):
    return headers["Authorization"].split()[1]


def _esperar_bloqueo(minimo=1, limite=10.0):
    """Espera a que `minimo` backends de esta base estén detenidos esperando un bloqueo de fila."""
    fin = time.monotonic() + limite
    while time.monotonic() < fin:
        with engine.connect() as conn:
            esperando = conn.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar_one()
        if esperando >= minimo:
            return
        time.sleep(0.05)
    raise AssertionError("Ninguna transacción quedó esperando el bloqueo.")


class _Hilo(threading.Thread):
    def __init__(self, tarea):
        super().__init__(daemon=True)
        self._tarea, self.resultado = tarea, None

    def run(self):
        try:
            self.resultado = self._tarea()
        except Exception as exc:  # noqa: BLE001
            self.resultado = exc

    def terminar(self):
        self.join(timeout=30)
        assert not self.is_alive()
        return self.resultado


# --- 1. Evaluación (módulo 5) sobre participaciones cerradas ----------------------------------------


def test_grade_can_be_recorded_on_finished_attempt(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    entorno.reloj.avanzar(minutes=12)
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    antes = _estado(p["id"])
    assert (antes.estado, antes.motivo) == ("FINALIZADA", "VOLUNTARIO")

    _calificar(p["id"], nota=4.25)
    _calificar(p["id"], nota=3.75)  # recalificación permitida
    despues = _estado(p["id"])
    assert float(despues.nota_final) == 3.75
    assert (despues.estado, despues.motivo, despues.fin, despues.inicio) == (antes.estado, antes.motivo, antes.fin, antes.inicio)


@pytest.mark.parametrize("causa", ["tiempo", "revocacion", "fin_de_sesion"])
def test_grade_can_be_recorded_on_expired_attempt(entorno, causa):
    p = entorno.iniciar(entorno.headers())
    if causa == "tiempo":
        entorno.reloj.avanzar(minutes=45)
        assert [str(i) for i in ejecutar_una_vez(reloj=entorno.reloj)] == [p["id"]]
    elif causa == "revocacion":
        entorno.reloj.avanzar(minutes=5)
        assert entorno.client.delete(entorno.conv_url(0), headers=entorno.docente_headers).status_code == 204
    else:
        entorno.reloj.avanzar(minutes=5)
        assert entorno.client.post(f"{SESSIONS_URL}/{entorno.sesion_id}/finish", headers=entorno.docente_headers).status_code == 200
    motivo = {"tiempo": "TIEMPO_AGOTADO", "revocacion": "CONVOCATORIA_REVOCADA", "fin_de_sesion": "SESION_FINALIZADA"}[causa]
    assert (_estado(p["id"]).estado, _estado(p["id"]).motivo) == ("EXPIRADA", motivo)

    _calificar(p["id"], nota=0)
    estado = _estado(p["id"])
    assert float(estado.nota_final) == 0 and (estado.estado, estado.motivo) == ("EXPIRADA", motivo)


def test_feedback_can_be_added_after_close(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    _calificar(p["id"], nota=4.0)
    _calificar(p["id"], retro="Buen análisis de la presión grupal.")
    _calificar(p["id"], retro="Buen análisis; revisa el sesgo de confirmación.")  # el docente puede editarla
    estado = _estado(p["id"])
    assert float(estado.nota_final) == 4.0
    assert estado.retroalimentacion_general == "Buen análisis; revisa el sesgo de confirmación."


def test_evaluation_is_rejected_while_attempt_in_progress(entorno):
    p = entorno.iniciar(entorno.headers())
    _falla("UPDATE participacion SET nota_final = 5 WHERE id = :id", "ck_participacion_evaluacion_tras_cierre", id=p["id"])
    _falla("UPDATE participacion SET retroalimentacion_general = 'x' WHERE id = :id", "ck_participacion_evaluacion_tras_cierre", id=p["id"])
    assert _estado(p["id"]).nota_final is None


def test_answers_of_closed_attempt_cannot_change(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    q1, q2, _ = entorno.preguntas
    guardar(entorno.client, headers, p["id"], q1["id"], q1["opciones"][0]["id"])
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    _calificar(p["id"], nota=2.5, retro="Revisa el escenario 1.")

    # Por la API: 409.
    assert guardar(entorno.client, headers, p["id"], q1["id"], q1["opciones"][1]["id"]).status_code == 409
    assert guardar(entorno.client, headers, p["id"], q2["id"], q2["opciones"][0]["id"]).status_code == 409
    assert entorno.client.delete(f"{PART_URL}/{p['id']}/answers/{q1['id']}", headers=headers).status_code == 409

    # Directamente en PostgreSQL: los disparadores lo impiden.
    _falla("UPDATE respuesta SET opcion_id = :o WHERE participacion_id = :p", "ck_respuesta_participacion_en_progreso",
           o=q1["opciones"][1]["id"], p=p["id"])
    _falla("DELETE FROM respuesta WHERE participacion_id = :p", "ck_respuesta_participacion_en_progreso", p=p["id"])
    _falla("INSERT INTO respuesta (participacion_id, pregunta_id, opcion_id, respondida_en, actualizada_en)"
           " VALUES (:p, :q, :o, now(), now())", "ck_respuesta_participacion_en_progreso",
           p=p["id"], q=q2["id"], o=q2["opciones"][0]["id"])
    # Tampoco se puede "mover" la respuesta cerrada a un intento abierto de otro estudiante.
    otra = entorno.iniciar(entorno.headers(1))
    _falla("UPDATE respuesta SET participacion_id = :otra WHERE participacion_id = :p", "ck_respuesta_identidad_inmutable",
           otra=otra["id"], p=p["id"])

    respuestas = entorno.client.get(f"{PART_URL}/{p['id']}/answers", headers=headers).json()
    assert [(r["pregunta_id"], r["opcion_id"]) for r in respuestas] == [(q1["id"], q1["opciones"][0]["id"])]


def test_historical_data_of_closed_attempt_is_immutable(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    otra = entorno.iniciar(entorno.headers(1))
    entorno.reloj.avanzar(minutes=10)
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    _calificar(p["id"], nota=3.0)
    original = _estado(p["id"])

    cerrada = "ck_participacion_cerrada_inmutable"
    _falla("UPDATE participacion SET estado = 'EXPIRADA', motivo_cierre = 'TIEMPO_AGOTADO' WHERE id = :id", cerrada, id=p["id"])
    _falla("UPDATE participacion SET estado = 'EN_PROGRESO', fin = NULL, motivo_cierre = NULL, nota_final = NULL WHERE id = :id",
           cerrada, id=p["id"])
    _falla("UPDATE participacion SET fin = fin + interval '5 minutes' WHERE id = :id", cerrada, id=p["id"])
    datos = "ck_participacion_datos_inmutables"
    _falla("UPDATE participacion SET inicio = inicio - interval '1 minute' WHERE id = :id", datos, id=p["id"])
    _falla("UPDATE participacion SET numero_intento = 2 WHERE id = :id", datos, id=p["id"])
    _falla("UPDATE participacion SET convocatoria_id = :c WHERE id = :id", datos,
           c=str(otra["convocatoria_id"]), id=p["id"])
    _falla("UPDATE participacion SET id = gen_random_uuid() WHERE id = :id", datos, id=p["id"])
    _falla("DELETE FROM participacion WHERE id = :id", "ck_participacion_sin_borrado", id=p["id"])
    # Ni siquiera junto con un cambio legítimo de nota.
    _falla("UPDATE participacion SET nota_final = 5, fin = fin - interval '1 minute' WHERE id = :id", cerrada, id=p["id"])

    assert _estado(p["id"]) == original


def test_students_cannot_set_or_see_evaluation(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    # Ninguna ruta estudiantil permite modificar la participación: solo las respuestas usan PUT.
    rutas = {
        (metodo.upper(), ruta)
        for ruta, operaciones in entorno.client.get("/openapi.json").json()["paths"].items()
        if ruta.startswith("/api/v1/student")
        for metodo in operaciones
    }
    assert ("POST", "/api/v1/student/participations/{participation_id}/finish") in rutas  # la lista no está vacía
    assert {m for m, _ in rutas if m in ("PUT", "PATCH")} == {"PUT"}
    assert [r for m, r in rutas if m == "PUT"] == ["/api/v1/student/participations/{participation_id}/answers/{question_id}"]
    assert entorno.client.patch(f"{PART_URL}/{p['id']}", headers=headers, json={"nota_final": 5}).status_code == 405

    # Un cuerpo con nota en la finalización se ignora.
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers, json={"nota_final": 5, "retroalimentacion_general": "x"})
    assert _estado(p["id"]).nota_final is None and _estado(p["id"]).retroalimentacion_general is None

    # Ya calificada, las vistas del estudiante siguen sin exponer evaluación ni respuestas correctas.
    _calificar(p["id"], nota=4.5, retro="RETROALIMENTACION-PRIVADA")
    for sufijo in ("", "/questionnaire", "/answers"):
        cuerpo = entorno.client.get(f"{PART_URL}/{p['id']}{sufijo}", headers=headers).text
        for prohibido in ("nota", "4.5", "RETROALIMENTACION-PRIVADA", "es_correcta", "justificacion", "criterio", "rubrica"):
            assert prohibido not in cuerpo, (sufijo, prohibido)


# --- 2. Vencimiento y cierres concurrentes ---------------------------------------------------------


def _servicio(clase, entorno, token):
    def ejecutar(metodo, *args):
        with SessionLocal() as s:
            acceso = AccesoEstudiantilService(s, entorno.reloj).resolver(token)
            return getattr(clase(s, entorno.reloj), metodo)(acceso, *args)
    return ejecutar


def test_save_waiting_for_lock_is_rejected_if_time_expires_meanwhile(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    q = entorno.preguntas[0]
    guardar(entorno.client, headers, p["id"], q["id"], q["opciones"][0]["id"])
    entorno.reloj.actual = T8 + timedelta(minutes=39, seconds=59)  # la solicitud llega 1 s antes del límite

    bloqueo = engine.connect()
    tx = bloqueo.begin()
    bloqueo.execute(text("SELECT 1 FROM participacion WHERE id = :id FOR UPDATE"), {"id": p["id"]})
    guardar_srv = _servicio(RespuestaService, entorno, _token(headers))
    hilo = _Hilo(lambda: guardar_srv("guardar", uuid.UUID(p["id"]), uuid.UUID(q["id"]), uuid.UUID(q["opciones"][2]["id"])))
    try:
        hilo.start()
        _esperar_bloqueo()                 # la solicitud está detenida en el bloqueo de la participación
        entorno.reloj.avanzar(seconds=1)   # 08:40:00 = fecha límite, mientras espera
    finally:
        tx.commit()
        bloqueo.close()

    resultado = hilo.terminar()
    assert isinstance(resultado, ConflictError) and "tiempo" in str(resultado)
    estado = _estado(p["id"])
    assert (estado.estado, estado.motivo, estado.fin) == ("EXPIRADA", "TIEMPO_AGOTADO", T8 + timedelta(minutes=40))
    assert fila("SELECT opcion_id::text AS o FROM respuesta WHERE participacion_id = :p", p=p["id"]).o == q["opciones"][0]["id"]


def test_finish_waiting_for_lock_expires_if_time_runs_out_meanwhile(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    entorno.reloj.actual = T8 + timedelta(minutes=39, seconds=59)

    bloqueo = engine.connect()
    tx = bloqueo.begin()
    bloqueo.execute(text("SELECT 1 FROM participacion WHERE id = :id FOR UPDATE"), {"id": p["id"]})
    finalizar = _servicio(ParticipacionService, entorno, _token(headers))
    hilo = _Hilo(lambda: finalizar("finalizar", uuid.UUID(p["id"])))
    try:
        hilo.start()
        _esperar_bloqueo()
        entorno.reloj.avanzar(seconds=1)
    finally:
        tx.commit()
        bloqueo.close()

    vista = hilo.terminar()
    assert (vista.estado.value, vista.motivo_cierre.value) == ("EXPIRADA", "TIEMPO_AGOTADO")
    assert vista.fin == T8 + timedelta(minutes=40)


class _CierrePausado:
    """Envuelve el cierre real del módulo 4 y detiene la transacción del módulo 3 antes del commit."""

    def __init__(self):
        self._real = integracion.cierre_participaciones()
        self.dentro = threading.Event()
        self.continuar = threading.Event()

    def _pausar(self):
        self.dentro.set()
        assert self.continuar.wait(timeout=20)

    def cerrar_por_revocacion(self, db, convocatoria_id, ahora):
        self._real.cerrar_por_revocacion(db, convocatoria_id, ahora)
        self._pausar()

    def cerrar_por_fin_de_sesion(self, db, sesion_id, ahora):
        self._real.cerrar_por_fin_de_sesion(db, sesion_id, ahora)
        self._pausar()


@pytest.mark.parametrize("cierre", ["revocacion", "fin_de_sesion"])
def test_answer_is_not_saved_after_concurrent_administrative_close(entorno, monkeypatch, cierre):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    q = entorno.preguntas[0]
    entorno.reloj.avanzar(minutes=10)
    pausado = _CierrePausado()
    monkeypatch.setattr(integracion, "cierre_participaciones", lambda: pausado)

    if cierre == "revocacion":
        docente = _Hilo(lambda: entorno.client.delete(entorno.conv_url(0), headers=entorno.docente_headers))
    else:
        docente = _Hilo(lambda: entorno.client.post(f"{SESSIONS_URL}/{entorno.sesion_id}/finish", headers=entorno.docente_headers))
    guardar_srv = _servicio(RespuestaService, entorno, _token(headers))
    estudiante = _Hilo(lambda: guardar_srv("guardar", uuid.UUID(p["id"]), uuid.UUID(q["id"]), uuid.UUID(q["opciones"][0]["id"])))
    try:
        docente.start()
        assert pausado.dentro.wait(timeout=10)  # el docente tiene la transacción abierta, sin confirmar
        estudiante.start()                      # el token todavía es válido: la revocación no se ha confirmado
        _esperar_bloqueo()                      # el estudiante espera el bloqueo de la sesión
    finally:
        pausado.continuar.set()

    assert docente.terminar().status_code in (200, 204)
    resultado = estudiante.terminar()
    esperado = PermissionDeniedError if cierre == "revocacion" else ConflictError
    assert isinstance(resultado, esperado), resultado
    motivo = "CONVOCATORIA_REVOCADA" if cierre == "revocacion" else "SESION_FINALIZADA"
    estado = _estado(p["id"])
    assert (estado.estado, estado.motivo, estado.fin) == ("EXPIRADA", motivo, T8 + timedelta(minutes=10))
    assert contar("SELECT count(*) FROM respuesta WHERE participacion_id = :p", p=p["id"]) == 0


def test_database_rejects_answer_written_during_concurrent_close(entorno):
    """Defensa en PostgreSQL (0010): aunque se escriba directo en `respuesta`, un cierre concurrente prevalece."""
    p = entorno.iniciar(entorno.headers())
    q = entorno.preguntas[0]

    cierre = engine.connect()
    tx = cierre.begin()
    cierre.execute(text(
        "UPDATE participacion SET estado = 'FINALIZADA', motivo_cierre = 'VOLUNTARIO', fin = inicio + interval '1 minute' WHERE id = :id"
    ), {"id": p["id"]})

    def insertar():
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO respuesta (participacion_id, pregunta_id, opcion_id, respondida_en, actualizada_en)"
                " VALUES (:p, :q, :o, now(), now())"
            ), {"p": p["id"], "q": q["id"], "o": q["opciones"][0]["id"]})

    hilo = _Hilo(insertar)
    try:
        hilo.start()
        _esperar_bloqueo()  # el disparador espera (FOR SHARE) a que el cierre se confirme
    finally:
        tx.commit()
        cierre.close()

    resultado = hilo.terminar()
    assert isinstance(resultado, IntegrityError)
    assert resultado.orig.diag.constraint_name == "ck_respuesta_participacion_en_progreso"
    assert contar("SELECT count(*) FROM respuesta WHERE participacion_id = :p", p=p["id"]) == 0


def test_migration_0010_is_self_contained_and_reversible(entorno):
    import ast

    from alembic import command

    from tests.conftest import PROJECT_ROOT, alembic_config

    arbol = ast.parse((PROJECT_ROOT / "alembic" / "versions" / "0010_evaluacion_tras_cierre.py").read_text(encoding="utf-8"))
    importados = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any(m == "app" or m.startswith("app.") for m in importados)

    p = entorno.iniciar(entorno.headers())
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "0009_participaciones")
    try:
        # Con 0009 la nota durante el intento no estaba restringida: el downgrade restaura ese estado.
        with engine.begin() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname = 'ck_participacion_evaluacion_tras_cierre'"
            )).scalar_one() == 0
    finally:
        engine.dispose()
        command.upgrade(config, "head")
    _falla("UPDATE participacion SET nota_final = 1 WHERE id = :id", "ck_participacion_evaluacion_tras_cierre", id=p["id"])
