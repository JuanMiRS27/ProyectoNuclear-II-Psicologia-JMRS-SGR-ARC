"""Módulo 3 por HTTP: programación, convocatorias y acceso estudiantil contra PostgreSQL real."""

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.database import engine
from app.main import app
from app.modules.auth.models import RolUsuario
from app.workers.session_expiration import ejecutar_una_vez
from tests.cases_helpers import CASES_URL, crear_caso_completo, publicar
from tests.conftest import bearer
from tests.sessions_helpers import (
    SESSIONS_URL,
    STUDENT_LOGIN_URL,
    SPACES_URL,
    codigo_de,
    contar,
    convocatorias,
    datos_sesion,
    estado_bd,
    iniciar,
    ingresar,
    programar,
    ventana,
)


@pytest.fixture
def sesion(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    return programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)


@pytest.fixture
def conv(client, creador_headers, sesion):
    return convocatorias(client, creador_headers, sesion["id"])[0]


def _url(sesion, conv=None, sufijo=""):
    base = f"{SESSIONS_URL}/{sesion['id']}"
    return f"{base}/convocations/{conv['id']}{sufijo}" if conv else base


def _codigo(client, headers, sesion, conv):
    return codigo_de(client, headers, sesion["id"], conv["id"])


# --- Programación -----------------------------------------------------------------------------


def test_sesion_programada_expone_estado_limite_y_conteo(sesion, estudiantes):
    assert sesion["estado"] == "SIN_INICIAR"
    assert sesion["limite_ingreso"].startswith("2026-11-03T09:00:00")
    assert sesion["total_convocatorias"] == len(estudiantes)


def test_programar_sin_estudiantes_es_valido(client, creador_headers, reloj, espacio, caso_publicado):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)

    assert sesion["total_convocatorias"] == 0
    assert contar("convocatoria") == 0


def test_caso_ajeno_no_se_puede_programar(client, otro_creador_headers, reloj, espacio, caso_publicado):
    respuesta = client.post(SESSIONS_URL, headers=otro_creador_headers, json=datos_sesion(reloj, caso_publicado["id"], espacio.id))

    assert respuesta.status_code == 404


def test_espacio_desactivado_no_afecta_sesiones_existentes(client, admin_headers, creador_headers, reloj, espacio, sesion):
    client.patch(f"{SPACES_URL}/{espacio.id}/status", headers=admin_headers, json={"activo": False})

    assert client.get(_url(sesion), headers=creador_headers).status_code == 200
    respuesta = client.patch(_url(sesion), headers=creador_headers, json={"espacio_academico_id": str(espacio.id)})
    assert respuesta.status_code == 400


def test_filtro_por_estado_y_paginacion_de_sesiones(client, creador_headers, reloj, espacio, caso_publicado, sesion):
    otra = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    iniciar(client, creador_headers, otra["id"], reloj)

    en_curso = client.get(SESSIONS_URL, headers=creador_headers, params={"estado": "EN_CURSO"}).json()
    pagina = client.get(SESSIONS_URL, headers=creador_headers, params={"limit": 1, "skip": 1}).json()

    assert [s["id"] for s in en_curso["items"]] == [otra["id"]]
    assert (pagina["total"], len(pagina["items"])) == (2, 1)


def test_listado_cuenta_solo_convocatorias_activas(client, creador_headers, sesion, conv, estudiantes):
    client.delete(_url(sesion, conv), headers=creador_headers)

    item = client.get(SESSIONS_URL, headers=creador_headers).json()["items"][0]

    assert item["total_convocatorias"] == len(estudiantes) - 1


def test_sesion_de_otro_docente_es_invisible(client, otro_creador_headers, sesion, conv):
    for metodo, url in (
        ("get", _url(sesion)),
        ("post", f"{_url(sesion)}/start"),
        ("get", f"{_url(sesion)}/convocations"),
        ("get", _url(sesion, conv, "/code")),
    ):
        assert client.request(metodo, url, headers=otro_creador_headers).status_code == 404


@pytest.mark.parametrize("sufijo", ["", "/start", "/finish", "/convocations"])
def test_sesion_inexistente(client, creador_headers, sufijo):
    metodo = "get" if sufijo in ("", "/convocations") else "post"

    assert client.request(metodo, f"{SESSIONS_URL}/{uuid.uuid4()}{sufijo}", headers=creador_headers).status_code == 404


def test_patch_vacio_no_cambia_nada(client, creador_headers, sesion):
    respuesta = client.patch(_url(sesion), headers=creador_headers, json={})

    assert respuesta.status_code == 200
    assert respuesta.json() == sesion


def test_cambiar_ventana_actualiza_el_limite_de_ingreso(client, creador_headers, reloj, sesion):
    respuesta = client.patch(_url(sesion), headers=creador_headers, json=ventana(reloj, 2, 4))

    assert respuesta.status_code == 200
    assert respuesta.json()["limite_ingreso"].startswith("2026-11-03T10:00:00")


def test_datos_informativos_se_pueden_vaciar_con_la_sesion_en_curso(client, creador_headers, reloj, sesion):
    iniciar(client, creador_headers, sesion["id"], reloj)

    respuesta = client.patch(_url(sesion), headers=creador_headers, json={"lugar": None, "mensaje": "Nuevo"})

    assert (respuesta.json()["lugar"], respuesta.json()["mensaje"]) == (None, "Nuevo")


def test_fecha_extrema_es_error_de_validacion(client, creador_headers, reloj, espacio, caso_publicado):
    # Regresión DEF-04: la conversión a UTC desbordaba y producía HTTP 500.
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id, inicio="0001-01-01T00:00:00+05:00")

    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos).status_code == 422


# --- Relación con el módulo 2 -------------------------------------------------------------------


def test_caso_con_sesiones_no_se_puede_eliminar_tras_archivar(client, creador_headers, caso_publicado, sesion):
    url = f"{CASES_URL}/{caso_publicado['id']}"

    assert client.post(f"{url}/archive", headers=creador_headers).status_code == 200
    assert client.delete(url, headers=creador_headers).status_code == 409
    assert client.get(_url(sesion), headers=creador_headers).status_code == 200


def test_sesion_no_puede_cambiarse_a_un_caso_archivado(client, creador_headers, sesion):
    otro = crear_caso_completo(client, creador_headers)
    publicar(client, creador_headers, otro["id"])
    client.post(f"{CASES_URL}/{otro['id']}/archive", headers=creador_headers)

    respuesta = client.patch(_url(sesion), headers=creador_headers, json={"caso_id": otro["id"]})

    assert respuesta.status_code == 409


def test_disparador_impide_cambiar_tiempo_si_el_caso_quedo_archivado(client, creador_headers, caso_publicado, sesion):
    client.post(f"{CASES_URL}/{caso_publicado['id']}/archive", headers=creador_headers)

    respuesta = client.patch(_url(sesion), headers=creador_headers, json={"tiempo_max_min": 30})

    assert respuesta.status_code == 409
    assert "publicados" in respuesta.json()["detail"]


# --- Convocatorias ------------------------------------------------------------------------------


def test_convocatoria_inexistente_o_de_otra_sesion(client, creador_headers, reloj, espacio, caso_publicado, sesion, conv):
    otra = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)

    for url in (_url(otra, conv), _url(sesion, {"id": uuid.uuid4()})):
        assert client.get(url, headers=creador_headers).status_code == 404


def test_convocar_valida_el_lote_completo(client, creador_headers, make_user, sesion, estudiantes):
    nuevo = make_user(RolUsuario.ESTUDIANTE, clave=None)
    antes = contar("convocatoria")

    respuesta = client.post(
        f"{_url(sesion)}/convocations", headers=creador_headers, json={"estudiantes": [nuevo.correo, estudiantes[0].correo]}
    )

    assert respuesta.status_code == 409
    assert contar("convocatoria") == antes


def test_codigo_solo_se_entrega_sin_cache(client, creador_headers, sesion, conv):
    respuesta = client.get(_url(sesion, conv, "/code"), headers=creador_headers)

    assert respuesta.headers["cache-control"] == "no-store"
    assert respuesta.headers["pragma"] == "no-cache"


def test_codigo_cifrado_en_la_base_y_ausente_de_los_listados(client, creador_headers, sesion, conv):
    codigo = _codigo(client, creador_headers, sesion, conv)
    with engine.connect() as conn:
        guardado = conn.execute(text("SELECT codigo_autorizacion FROM convocatoria WHERE id = :id"), {"id": conv["id"]}).scalar_one()

    assert guardado.startswith("v1.") and codigo not in guardado
    assert codigo not in client.get(f"{_url(sesion)}/convocations", headers=creador_headers).text


def test_codigo_danado_en_la_base_se_informa_y_se_recupera_regenerando(client, creador_headers, sesion, conv):
    with engine.begin() as conn:
        conn.execute(text("UPDATE convocatoria SET codigo_autorizacion = 'v1.AAAA' WHERE id = :id"), {"id": conv["id"]})

    assert client.get(_url(sesion, conv, "/code"), headers=creador_headers).status_code == 409

    respuesta = client.post(_url(sesion, conv, "/regenerate-code"), headers=creador_headers, json={"motivo": "Código dañado"})
    assert respuesta.status_code == 200
    assert _codigo(client, creador_headers, sesion, conv) == respuesta.json()["codigo_autorizacion"]


@pytest.mark.parametrize(
    ("metodo", "sufijo", "cuerpo"),
    [
        ("patch", "/attempts", {"intentos_autorizados": 5, "motivo": "Falla eléctrica"}),
        ("post", "/regenerate-code", {"motivo": "Código expuesto"}),
        ("get", "/code", None),
        ("delete", "", None),
    ],
)
def test_convocatoria_revocada_rechaza_operaciones(client, creador_headers, sesion, conv, metodo, sufijo, cuerpo):
    client.delete(_url(sesion, conv), headers=creador_headers)

    respuesta = client.request(metodo, _url(sesion, conv, sufijo), headers=creador_headers, json=cuerpo)

    assert respuesta.status_code == 409


def test_intentos_no_pueden_disminuir_por_api(client, creador_headers, sesion, conv):
    respuesta = client.patch(
        _url(sesion, conv, "/attempts"), headers=creador_headers, json={"intentos_autorizados": 1, "motivo": "Sin razón válida"}
    )

    assert respuesta.status_code == 400


@pytest.mark.parametrize("actor", ["anonimo", "admin", "estudiante"])
def test_gestion_de_sesiones_solo_para_docentes(client, admin_headers, estudiante_headers, sesion, actor):
    cabeceras = {"anonimo": {}, "admin": admin_headers, "estudiante": estudiante_headers}[actor]

    respuesta = client.get(SESSIONS_URL, headers=cabeceras)

    assert respuesta.status_code == (401 if actor == "anonimo" else 403)


# --- Acceso estudiantil -------------------------------------------------------------------------


@pytest.fixture
def en_curso(client, creador_headers, reloj, sesion, conv):
    assert iniciar(client, creador_headers, sesion["id"], reloj).status_code == 200
    return sesion


def _ingresar_conv(client, creador_headers, sesion, conv, estudiante):
    respuesta = ingresar(client, estudiante.correo, _codigo(client, creador_headers, sesion, conv))
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _estudiante_de(estudiantes, conv):
    return next(e for e in estudiantes if str(e.id) == conv["estudiante"]["id"])


def test_ingreso_devuelve_alcance_y_evita_cache(client, creador_headers, en_curso, conv, estudiantes):
    estudiante = _estudiante_de(estudiantes, conv)

    respuesta = ingresar(client, estudiante.correo, _codigo(client, creador_headers, en_curso, conv))

    assert respuesta.headers["cache-control"] == "no-store"
    assert (respuesta.json()["sesion_id"], respuesta.json()["convocatoria_id"]) == (en_curso["id"], conv["id"])


def test_estudiante_consulta_su_sesion_sin_ver_datos_ajenos(client, creador_headers, en_curso, conv, estudiantes):
    token = _ingresar_conv(client, creador_headers, en_curso, conv, _estudiante_de(estudiantes, conv))["access_token"]

    respuesta = client.get(f"/api/v1/student/sessions/{en_curso['id']}", headers=bearer(token))

    cuerpo = respuesta.json()
    assert cuerpo["convocatoria_id"] == conv["id"]
    assert cuerpo["puede_iniciar_participacion"] is True
    assert "docente_id" not in cuerpo["sesion"] and "caso_id" not in cuerpo["sesion"]


def test_indicador_de_inicio_cambia_al_cerrar_la_ventana(client, creador_headers, reloj, en_curso, conv, estudiantes):
    token = _ingresar_conv(client, creador_headers, en_curso, conv, _estudiante_de(estudiantes, conv))["access_token"]

    reloj.avanzar(hours=1, seconds=1)

    respuesta = client.get(f"/api/v1/student/sessions/{en_curso['id']}", headers=bearer(token))
    assert (respuesta.status_code, respuesta.json()["puede_iniciar_participacion"]) == (200, False)


def test_estudiante_de_dos_sesiones_entra_a_cada_una_con_su_codigo(
    client, creador_headers, reloj, espacio, caso_publicado, en_curso, conv, estudiantes
):
    estudiante = _estudiante_de(estudiantes, conv)
    otra = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, [estudiante], **ventana(reloj, 0, 2))
    assert iniciar(client, creador_headers, otra["id"]).status_code == 200
    otra_conv = convocatorias(client, creador_headers, otra["id"])[0]

    primera = ingresar(client, estudiante.correo, _codigo(client, creador_headers, en_curso, conv)).json()
    segunda = ingresar(client, estudiante.correo, _codigo(client, creador_headers, otra, otra_conv)).json()

    assert (primera["sesion_id"], segunda["sesion_id"]) == (en_curso["id"], otra["id"])


def test_login_bloqueado_informa_retry_after(client, en_curso, estudiantes):
    correo = estudiantes[0].correo
    for _ in range(5):
        assert ingresar(client, correo, "ZZZZZZZZZZZZ").status_code == 401

    respuesta = ingresar(client, correo, "ZZZZZZZZZZZZ")

    assert respuesta.status_code == 429
    assert int(respuesta.headers["retry-after"]) > 0


def test_codigo_no_ascii_es_credencial_invalida_y_cuenta_como_fallo(en_curso, conv, estudiantes):
    # Regresión DEF-01: producía HTTP 500 y no contaba para el limitador de intentos.
    correo = _estudiante_de(estudiantes, conv).correo
    with TestClient(app, raise_server_exceptions=False) as cliente:
        estados = [cliente.post(STUDENT_LOGIN_URL, json={"correo": correo, "codigo": "ÑÑÑÑÑÑÑÑÑÑÑÑ"}).status_code for _ in range(6)]

    assert estados == [401] * 5 + [429]


def test_correo_invalido_en_login_estudiantil(client):
    assert client.post(STUDENT_LOGIN_URL, json={"correo": "x", "codigo": "ABC"}).status_code == 422


def test_ingreso_sin_token_a_rutas_estudiantiles(client, en_curso):
    respuesta = client.get(f"/api/v1/student/sessions/{en_curso['id']}")

    assert respuesta.status_code == 401
    assert client.get(f"/api/v1/student/sessions/{en_curso['id']}", headers={"Authorization": "Basic abc"}).status_code == 401


def test_cierre_de_sesion_revoca_tokens_y_persiste(client, creador_headers, en_curso, conv, estudiantes):
    token = _ingresar_conv(client, creador_headers, en_curso, conv, _estudiante_de(estudiantes, conv))["access_token"]

    assert client.post(f"{_url(en_curso)}/finish", headers=creador_headers).json()["estado"] == "FINALIZADO"

    assert estado_bd(en_curso["id"]) == "FINALIZADO"
    assert client.get(f"/api/v1/student/sessions/{en_curso['id']}", headers=bearer(token)).status_code == 401


def test_cierre_automatico_tras_el_plazo_mediante_worker(reloj, en_curso):
    reloj.avanzar(hours=26)

    assert [str(s) for s in ejecutar_una_vez(reloj=reloj, plazo=timedelta(hours=24))] == [en_curso["id"]]
    assert estado_bd(en_curso["id"]) == "FINALIZADO"
    assert ejecutar_una_vez(reloj=reloj, plazo=timedelta(hours=24)) == []
