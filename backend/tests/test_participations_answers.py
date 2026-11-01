"""Cuestionario seguro y registro de respuestas."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.modules.participations.services import RespuestaService
from app.modules.sessions.services import AccesoEstudiantilService
from tests.cases_helpers import CASES_URL, crear_caso_completo
from tests.participations_helpers import PART_URL, contar, en_paralelo, entorno, fila, guardar  # noqa: F401

PROHIBIDOS = ("es_correcta", "esCorrecta", "justificacion", "criterio", "rubrica", "peso", "niveles", "dslVersion", "nota")


# --- Cuestionario ---------------------------------------------------------------------------


def test_questionnaire_is_ordered_and_complete(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    cuestionario = entorno.client.get(f"{PART_URL}/{p['id']}/questionnaire", headers=headers)
    assert cuestionario.status_code == 200
    c = cuestionario.json()
    assert c["caso"] == {
        "nombre": "Conformidad en el aula",
        "desarrollo_situacional": entorno.caso["desarrollo_situacional"],
        "historia": entorno.caso["historia"],
    }
    assert [e["orden"] for e in c["escenarios"]] == [1, 2]
    assert [e["titulo"] for e in c["escenarios"]] == [e["titulo"] for e in entorno.caso["escenarios"]]
    for esc, original in zip(c["escenarios"], entorno.caso["escenarios"]):
        assert [q["orden"] for q in esc["preguntas"]] == list(range(1, len(esc["preguntas"]) + 1))
        for q, q_orig in zip(esc["preguntas"], original["preguntas"]):
            assert q["id"] == q_orig["id"] and q["enunciado"] == q_orig["enunciado"]
            assert [o["orden"] for o in q["opciones"]] == [1, 2, 3]
            assert [o["texto"] for o in q["opciones"]] == [o["texto"] for o in q_orig["opciones"]]
            assert set(q["opciones"][0]) == {"id", "orden", "texto"}
            assert q["opcion_seleccionada_id"] is None
    assert c["tiempo_restante_segundos"] == 2400 and c["progreso"]["total_preguntas"] == 3


def test_questionnaire_never_exposes_corrections(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]
    guardar(entorno.client, headers, p["id"], pregunta["id"], pregunta["opciones"][0]["id"])  # la correcta
    for url in (f"{PART_URL}/{p['id']}/questionnaire", f"{PART_URL}/{p['id']}", f"{PART_URL}/{p['id']}/answers", PART_URL):
        cuerpo = entorno.client.get(url, headers=headers).text
        for prohibido in PROHIBIDOS:
            assert prohibido not in cuerpo, (url, prohibido)
        for justificacion in ("Considera la presión grupal", "Ignora el contexto social", "Refuerza el sesgo"):
            assert justificacion not in cuerpo


def test_participation_of_another_student_is_hidden(entorno):
    a, b = entorno.headers(0), entorno.headers(1)
    p = entorno.iniciar(a)
    pregunta = entorno.preguntas[0]
    for metodo, sufijo, cuerpo in (
        ("get", "", None), ("get", "/questionnaire", None), ("get", "/answers", None), ("post", "/finish", None),
        ("put", f"/answers/{pregunta['id']}", {"opcion_id": pregunta["opciones"][0]["id"]}),
        ("delete", f"/answers/{pregunta['id']}", None),
    ):
        kwargs = {"headers": b} | ({"json": cuerpo} if cuerpo else {})
        assert getattr(entorno.client, metodo)(f"{PART_URL}/{p['id']}{sufijo}", **kwargs).status_code == 404, (metodo, sufijo)
    assert entorno.client.get(PART_URL, headers=b).json() == []


def test_student_token_cannot_reach_teacher_routes(entorno):
    headers = entorno.headers()
    for url in (CASES_URL, f"{CASES_URL}/{entorno.caso['id']}", f"/api/v1/sessions/{entorno.sesion_id}", "/api/v1/users"):
        assert entorno.client.get(url, headers=headers).status_code == 401


def test_teacher_token_cannot_use_student_routes(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    assert entorno.client.get(f"{PART_URL}/{p['id']}", headers=entorno.docente_headers).status_code == 401


# --- Respuestas --------------------------------------------------------------------------------


def test_save_modify_and_delete_answer(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]
    a, b = pregunta["opciones"][0]["id"], pregunta["opciones"][1]["id"]

    r1 = guardar(entorno.client, headers, p["id"], pregunta["id"], a)
    assert r1.status_code == 200
    assert r1.json()["opcion_id"] == a and r1.json()["respondida_en"] == r1.json()["actualizada_en"]
    assert "es_correcta" not in r1.text

    entorno.reloj.avanzar(minutes=3)
    r2 = guardar(entorno.client, headers, p["id"], pregunta["id"], b).json()
    assert r2["opcion_id"] == b and r2["respondida_en"] == r1.json()["respondida_en"] and r2["actualizada_en"] > r2["respondida_en"]

    entorno.reloj.avanzar(minutes=1)
    r3 = guardar(entorno.client, headers, p["id"], pregunta["id"], b).json()  # reintento con la misma opción
    assert r3["actualizada_en"] == r2["actualizada_en"]
    assert contar("SELECT count(*) FROM respuesta") == 1

    detalle = entorno.client.get(f"{PART_URL}/{p['id']}", headers=headers).json()
    assert detalle["progreso"] == {"total_preguntas": 3, "respondidas": 1, "pendientes": 2, "porcentaje": 33}
    seleccion = entorno.client.get(f"{PART_URL}/{p['id']}/questionnaire", headers=headers).json()["escenarios"][0]["preguntas"][0]
    assert seleccion["opcion_seleccionada_id"] == b

    assert entorno.client.delete(f"{PART_URL}/{p['id']}/answers/{pregunta['id']}", headers=headers).status_code == 204
    assert entorno.client.delete(f"{PART_URL}/{p['id']}/answers/{pregunta['id']}", headers=headers).status_code == 204  # idempotente
    assert entorno.client.get(f"{PART_URL}/{p['id']}/answers", headers=headers).json() == []


def test_option_of_another_question_is_rejected(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    q1, q2 = entorno.preguntas[:2]
    response = guardar(entorno.client, headers, p["id"], q1["id"], q2["opciones"][0]["id"])
    assert response.status_code == 400 and contar("SELECT count(*) FROM respuesta") == 0


def test_question_of_another_case_is_rejected(entorno, client, creador_headers):
    otro = crear_caso_completo(client, creador_headers)
    ajena = otro["escenarios"][0]["preguntas"][0]
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    assert guardar(client, headers, p["id"], ajena["id"], ajena["opciones"][0]["id"]).status_code == 404
    assert guardar(client, headers, p["id"], uuid.uuid4(), uuid.uuid4()).status_code == 404


def test_closed_attempt_rejects_changes(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]
    guardar(entorno.client, headers, p["id"], pregunta["id"], pregunta["opciones"][0]["id"])
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    assert guardar(entorno.client, headers, p["id"], pregunta["id"], pregunta["opciones"][1]["id"]).status_code == 409
    assert entorno.client.delete(f"{PART_URL}/{p['id']}/answers/{pregunta['id']}", headers=headers).status_code == 409
    assert entorno.client.get(f"{PART_URL}/{p['id']}/answers", headers=headers).json()[0]["opcion_id"] == pregunta["opciones"][0]["id"]


def test_client_cannot_send_extra_fields(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]
    response = entorno.client.put(
        f"{PART_URL}/{p['id']}/answers/{pregunta['id']}", headers=headers,
        json={"opcion_id": pregunta["opciones"][0]["id"], "respondida_en": "2020-01-01T00:00:00Z"},
    )
    assert response.status_code == 422


def test_concurrent_answers_never_duplicate(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    token = headers["Authorization"].split()[1]
    pregunta = entorno.preguntas[0]

    def responder(opcion):
        def tarea():
            with SessionLocal() as s:
                acceso = AccesoEstudiantilService(s, entorno.reloj).resolver(token)
                return RespuestaService(s, entorno.reloj).guardar(acceso, uuid.UUID(p["id"]), uuid.UUID(pregunta["id"]), uuid.UUID(opcion))
        return tarea

    resultados = en_paralelo(*(responder(o["id"]) for o in pregunta["opciones"] * 2))
    assert not any(isinstance(r, Exception) for r in resultados), resultados
    assert contar("SELECT count(*) FROM respuesta WHERE participacion_id = :p", p=p["id"]) == 1


# --- Restricciones en PostgreSQL -----------------------------------------------------------------


def test_database_enforces_answer_integrity(entorno, client, creador_headers):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    q1, q2 = entorno.preguntas[:2]
    guardar(client, headers, p["id"], q1["id"], q1["opciones"][0]["id"])

    def insertar(pregunta_id, opcion_id):
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO respuesta (participacion_id, pregunta_id, opcion_id, respondida_en, actualizada_en) VALUES (:p, :q, :o, now(), now())"),
                {"p": p["id"], "q": pregunta_id, "o": opcion_id},
            )

    with pytest.raises(IntegrityError, match="uq_respuesta_participacion_pregunta"):
        insertar(q1["id"], q1["opciones"][1]["id"])
    with pytest.raises(IntegrityError, match="fk_respuesta_opcion_de_la_pregunta"):
        insertar(q2["id"], q1["opciones"][0]["id"])
    otro = crear_caso_completo(client, creador_headers)["escenarios"][0]["preguntas"][0]
    with pytest.raises(IntegrityError) as info:
        insertar(otro["id"], otro["opciones"][0]["id"])
    assert info.value.orig.diag.constraint_name == "ck_respuesta_pregunta_del_caso"

    client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        conn.execute(text("UPDATE respuesta SET opcion_id = :o WHERE participacion_id = :p"), {"o": q1["opciones"][2]["id"], "p": p["id"]})
    assert info.value.orig.diag.constraint_name == "ck_respuesta_participacion_en_progreso"
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("DELETE FROM respuesta WHERE participacion_id = :p"), {"p": p["id"]})
    assert fila("SELECT count(*) AS n FROM respuesta").n == 1
