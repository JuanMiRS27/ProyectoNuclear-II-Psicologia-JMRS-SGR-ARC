"""Módulo 4 por HTTP: inicio, respuestas, cierre y datos para la evaluación contra PostgreSQL real."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.modules.participations.services import ConsultaEvaluacionService
from app.workers.participation_expiration import ejecutar_una_vez
from tests.conftest import bearer
from tests.participations_helpers import PART_URL, contar, entorno, fila, guardar, iniciar_participacion  # noqa: F401
from tests.sessions_helpers import ingresar


def _opcion(pregunta, indice=0):
    return pregunta["opciones"][indice]["id"]


def _responder(entorno, headers, participacion_id, cuantas=None):
    for pregunta in entorno.preguntas[:cuantas]:
        assert guardar(entorno.client, headers, participacion_id, pregunta["id"], _opcion(pregunta)).status_code == 200


# --- Inicio -----------------------------------------------------------------------------------------


def test_inicio_responde_201_y_recuperacion_200_sin_cache(entorno):
    headers = entorno.headers()

    primero = iniciar_participacion(entorno.client, headers, entorno.sesion_id)
    segundo = iniciar_participacion(entorno.client, headers, entorno.sesion_id)

    assert (primero.status_code, segundo.status_code) == (201, 200)
    assert primero.headers["cache-control"] == "no-store"
    assert primero.json()["id"] == segundo.json()["id"]
    assert contar("SELECT count(*) FROM participacion") == 1


def test_vista_inicial_del_intento(entorno):
    vista = entorno.iniciar(entorno.headers())

    assert vista["estado"] == "EN_PROGRESO"
    assert vista["numero_intento"] == 1
    assert vista["tiempo_restante_segundos"] == 40 * 60
    assert vista["fecha_limite"].startswith("2026-11-03T08:40:00")
    assert vista["progreso"] == {"total_preguntas": 3, "respondidas": 0, "pendientes": 3, "porcentaje": 0}
    assert (vista["intentos_autorizados"], vista["intentos_utilizados"]) == (1, 1)


def test_cuerpo_del_cliente_no_altera_el_inicio_del_intento(entorno):
    headers = entorno.headers()

    respuesta = entorno.client.post(
        f"/api/v1/student/sessions/{entorno.sesion_id}/participations", headers=headers, json={"inicio": "2000-01-01T00:00:00Z"}
    )

    assert respuesta.json()["inicio"].startswith("2026-11-03T08:00:00")


def test_intentos_extra_numeran_y_conservan_el_historial(entorno):
    headers = entorno.headers()
    primero = entorno.iniciar(headers)
    _responder(entorno, headers, primero["id"], 1)
    entorno.client.post(f"{PART_URL}/{primero['id']}/finish", headers=headers)
    entorno.client.patch(
        entorno.conv_url(0, "/attempts"), headers=entorno.docente_headers, json={"intentos_autorizados": 2, "motivo": "Falla eléctrica"}
    )

    segundo = entorno.iniciar(headers)
    lista = entorno.client.get(PART_URL, headers=headers).json()

    assert segundo["numero_intento"] == 2
    assert segundo["progreso"]["respondidas"] == 0  # cada intento tiene sus propias respuestas
    assert [p["estado"] for p in lista] == ["FINALIZADA", "EN_PROGRESO"]


def test_token_estudiantil_ausente_o_de_otro_tipo(entorno):
    assert entorno.client.get(PART_URL).status_code == 401
    assert entorno.client.get(PART_URL, headers=entorno.docente_headers).status_code == 401


def test_token_de_otra_sesion_o_ruta_inexistente(entorno):
    respuesta = iniciar_participacion(entorno.client, entorno.headers(), uuid.uuid4())

    assert respuesta.status_code == 403


# --- Respuestas ---------------------------------------------------------------------------------


def test_respuesta_persiste_y_actualiza_el_progreso(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]

    respuesta = guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta))

    assert respuesta.status_code == 200
    assert "es_correcta" not in respuesta.text
    vista = entorno.client.get(f"{PART_URL}/{intento['id']}", headers=headers).json()
    assert vista["progreso"] == {"total_preguntas": 3, "respondidas": 1, "pendientes": 2, "porcentaje": 33}


def test_cambiar_de_opcion_conserva_la_primera_marca_de_tiempo(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]
    guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta, 0))

    entorno.reloj.avanzar(minutes=5)
    cambio = guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta, 1)).json()

    assert cambio["opcion_id"] == _opcion(pregunta, 1)
    assert cambio["respondida_en"].startswith("2026-11-03T08:00:00")
    assert cambio["actualizada_en"].startswith("2026-11-03T08:05:00")
    assert contar("SELECT count(*) FROM respuesta") == 1


def test_reintento_con_la_misma_opcion_no_es_una_modificacion(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]
    guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta))

    entorno.reloj.avanzar(minutes=5)
    reintento = guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta)).json()

    assert reintento["actualizada_en"].startswith("2026-11-03T08:00:00")


def test_eliminar_respuesta_es_idempotente(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]
    guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta))
    url = f"{PART_URL}/{intento['id']}/answers/{pregunta['id']}"

    assert entorno.client.delete(url, headers=headers).status_code == 204
    assert entorno.client.delete(url, headers=headers).status_code == 204
    assert entorno.client.get(f"{PART_URL}/{intento['id']}/answers", headers=headers).json() == []


def test_seleccion_guardada_aparece_en_el_cuestionario(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    pregunta = entorno.preguntas[1]
    guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta, 2))

    cuestionario = entorno.client.get(f"{PART_URL}/{intento['id']}/questionnaire", headers=headers).json()

    seleccionadas = {p["id"]: p["opcion_seleccionada_id"] for e in cuestionario["escenarios"] for p in e["preguntas"]}
    assert seleccionadas[pregunta["id"]] == _opcion(pregunta, 2)
    assert sum(v is not None for v in seleccionadas.values()) == 1
    assert cuestionario["progreso"]["porcentaje"] == 33


@pytest.mark.parametrize(
    "cuerpo", [{}, {"opcion_id": "no-uuid"}, {"opcion_id": None}], ids=["vacio", "mal-formado", "nulo"]
)
def test_respuesta_con_cuerpo_invalido(entorno, cuerpo):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)

    respuesta = entorno.client.put(f"{PART_URL}/{intento['id']}/answers/{entorno.preguntas[0]['id']}", headers=headers, json=cuerpo)

    assert respuesta.status_code == 422


def test_pregunta_inexistente_en_guardar_y_eliminar(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    ajena = uuid.uuid4()

    assert guardar(entorno.client, headers, intento["id"], ajena, uuid.uuid4()).status_code == 404
    assert entorno.client.delete(f"{PART_URL}/{intento['id']}/answers/{ajena}", headers=headers).status_code == 404


@pytest.mark.parametrize("sufijo", ["", "/questionnaire", "/answers"])
def test_intento_inexistente_o_ajeno_en_lecturas(entorno, sufijo):
    propio = entorno.headers(0)
    ajeno = entorno.iniciar(entorno.headers(1))

    for participacion_id in (uuid.uuid4(), ajeno["id"]):
        assert entorno.client.get(f"{PART_URL}/{participacion_id}{sufijo}", headers=propio).status_code == 404


def test_finalizar_intento_ajeno(entorno):
    ajeno = entorno.iniciar(entorno.headers(1))

    assert entorno.client.post(f"{PART_URL}/{ajeno['id']}/finish", headers=entorno.headers(0)).status_code == 404
    assert fila("SELECT estado::text AS e FROM participacion WHERE id = :id", id=ajeno["id"]).e == "EN_PROGRESO"


# --- Cierre y permisos según estado ---------------------------------------------------------------


def test_finalizar_conserva_respuestas_y_bloquea_cambios(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    _responder(entorno, headers, intento["id"], 2)
    entorno.reloj.avanzar(minutes=10)

    cerrado = entorno.client.post(f"{PART_URL}/{intento['id']}/finish", headers=headers).json()

    assert (cerrado["estado"], cerrado["motivo_cierre"]) == ("FINALIZADA", "VOLUNTARIO")
    assert cerrado["fin"].startswith("2026-11-03T08:10:00")
    assert cerrado["tiempo_restante_segundos"] == 0
    assert cerrado["progreso"]["respondidas"] == 2
    pregunta = entorno.preguntas[2]
    assert guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta)).status_code == 409


def test_intento_cerrado_sigue_siendo_consultable(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    entorno.client.post(f"{PART_URL}/{intento['id']}/finish", headers=headers)

    assert entorno.client.get(f"{PART_URL}/{intento['id']}/questionnaire", headers=headers).status_code == 200
    assert entorno.client.get(f"{PART_URL}/{intento['id']}/answers", headers=headers).status_code == 200


def test_listar_intentos_expira_el_vencido(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    entorno.reloj.avanzar(minutes=41)

    lista = entorno.client.get(PART_URL, headers=headers).json()

    assert (lista[0]["id"], lista[0]["estado"], lista[0]["motivo_cierre"]) == (intento["id"], "EXPIRADA", "TIEMPO_AGOTADO")


def test_listar_respuestas_expira_el_vencido(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    entorno.reloj.avanzar(minutes=40)

    assert entorno.client.get(f"{PART_URL}/{intento['id']}/answers", headers=headers).status_code == 200

    assert fila("SELECT estado::text AS e FROM participacion WHERE id = :id", id=intento["id"]).e == "EXPIRADA"


def test_eliminar_respuesta_tras_agotar_el_tiempo(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    pregunta = entorno.preguntas[0]
    guardar(entorno.client, headers, intento["id"], pregunta["id"], _opcion(pregunta))
    entorno.reloj.avanzar(minutes=40)

    respuesta = entorno.client.delete(f"{PART_URL}/{intento['id']}/answers/{pregunta['id']}", headers=headers)

    assert respuesta.status_code == 409
    assert contar("SELECT count(*) FROM respuesta") == 1


def test_revocacion_durante_el_intento_corta_el_token(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)

    assert entorno.client.delete(entorno.conv_url(), headers=entorno.docente_headers).status_code == 204

    assert entorno.client.get(f"{PART_URL}/{intento['id']}", headers=headers).status_code == 401
    cierre = fila("SELECT estado::text AS e, motivo_cierre::text AS m FROM participacion WHERE id = :id", id=intento["id"])
    assert (cierre.e, cierre.m) == ("EXPIRADA", "CONVOCATORIA_REVOCADA")


def test_fin_de_sesion_cierra_intentos_en_una_sola_operacion(entorno):
    primero, segundo = entorno.iniciar(entorno.headers(0)), entorno.iniciar(entorno.headers(1))
    entorno.client.post(f"{PART_URL}/{primero['id']}/finish", headers=entorno.headers(0))

    entorno.client.post(f"/api/v1/sessions/{entorno.sesion_id}/finish", headers=entorno.docente_headers)

    estados = {
        str(f.id): (f.estado, f.motivo)
        for f in _filas("SELECT id, estado::text AS estado, motivo_cierre::text AS motivo FROM participacion")
    }
    assert estados == {
        primero["id"]: ("FINALIZADA", "VOLUNTARIO"),
        segundo["id"]: ("EXPIRADA", "SESION_FINALIZADA"),
    }


def _filas(sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).all()


def test_estudiante_no_puede_entrar_tras_el_cierre_de_sesion(entorno):
    estudiante = entorno.estudiantes[0]
    entorno.client.post(f"/api/v1/sessions/{entorno.sesion_id}/finish", headers=entorno.docente_headers)

    assert ingresar(entorno.client, estudiante.correo, entorno.codigos[estudiante.correo]).status_code == 403


def test_worker_real_expira_con_el_instante_del_limite(entorno):
    intento = entorno.iniciar(entorno.headers())
    entorno.reloj.avanzar(hours=1)

    assert [str(i) for i in ejecutar_una_vez(reloj=entorno.reloj)] == [intento["id"]]

    assert fila("SELECT fin FROM participacion WHERE id = :id", id=intento["id"]).fin.isoformat().startswith("2026-11-03T08:40:00")
    assert ejecutar_una_vez(reloj=entorno.reloj) == []


# --- Evaluación: lo que existe y lo que aún no ----------------------------------------------------


def test_cierre_no_genera_nota_ni_retroalimentacion(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    _responder(entorno, headers, intento["id"])

    cerrado = entorno.client.post(f"{PART_URL}/{intento['id']}/finish", headers=headers)

    assert "nota" not in cerrado.text
    datos = fila("SELECT nota_final, retroalimentacion_general FROM participacion WHERE id = :id", id=intento["id"])
    assert (datos.nota_final, datos.retroalimentacion_general) == (None, None)


def test_no_existen_rutas_de_calificacion(client):
    rutas = client.get("/openapi.json").json()["paths"]

    assert not [r for r in rutas if "evaluat" in r or "grade" in r or "feedback" in r or "result" in r]


def test_consulta_para_evaluacion_con_datos_reales(entorno):
    headers = entorno.headers(0)
    intento = entorno.iniciar(headers)
    _responder(entorno, headers, intento["id"], 2)
    entorno.client.post(f"{PART_URL}/{intento['id']}/finish", headers=headers)

    with SessionLocal() as sesion:
        servicio = ConsultaEvaluacionService(sesion)
        datos = servicio.obtener(uuid.UUID(intento["id"]))
        cerradas = servicio.cerradas_de_sesion(uuid.UUID(entorno.sesion_id))
        ausentes = {c.estudiante_id for c in servicio.ausencias(uuid.UUID(entorno.sesion_id))}

    assert datos.completa_voluntariamente is True
    assert len(datos.respuestas) == 2
    assert [str(p) for p in datos.preguntas_sin_responder] == [entorno.preguntas[2]["id"]]
    assert sum(c.peso for c in datos.rubrica.criterios) == 100
    assert [str(c) for c in cerradas] == [intento["id"]]
    assert ausentes == {e.id for e in entorno.estudiantes[1:]}


def test_convocatoria_revocada_no_cuenta_como_ausencia(entorno):
    entorno.client.delete(entorno.conv_url(1), headers=entorno.docente_headers)

    with SessionLocal() as sesion:
        ausentes = {c.estudiante_id for c in ConsultaEvaluacionService(sesion).ausencias(uuid.UUID(entorno.sesion_id))}

    assert entorno.estudiantes[1].id not in ausentes
    assert len(ausentes) == 2


def test_token_expirado_de_estudiante_no_interrumpe_el_temporizador(entorno):
    headers = entorno.headers()
    intento = entorno.iniciar(headers)
    entorno.reloj.avanzar(minutes=10)

    nuevo = bearer(ingresar(entorno.client, entorno.estudiantes[0].correo, entorno.codigos[entorno.estudiantes[0].correo]).json()["access_token"])
    vista = entorno.client.get(f"{PART_URL}/{intento['id']}", headers=nuevo).json()

    assert vista["tiempo_restante_segundos"] == timedelta(minutes=30).total_seconds()
