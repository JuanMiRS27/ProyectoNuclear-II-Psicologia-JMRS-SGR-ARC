"""Programación y modificación de sesiones."""

import uuid
from datetime import timedelta

import pytest

from app.modules.auth.models import RolUsuario
from app.modules.sessions.services import sesion_service
from tests.cases_helpers import CASES_URL, crear_caso_completo
from tests.conftest import bearer, login
from tests.sessions_helpers import SESSIONS_URL, contar, datos_sesion, iniciar, programar, ventana


def test_schedule_session_with_published_case(client, creador, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)

    assert sesion["estado"] == "SIN_INICIAR"
    assert sesion["docente_id"] == str(creador.id)
    assert sesion["caso_id"] == caso_publicado["id"] and sesion["espacio_academico_id"] == str(espacio.id)
    assert sesion["total_convocatorias"] == 3
    assert sesion["tiempo_max_min"] == 40 and sesion["lugar"] == "Aula 301"
    # Límite de ingreso = inicio + (fin - inicio) / 2 → 09:00 UTC.
    assert sesion["limite_ingreso"].startswith("2026-11-03T09:00:00")
    assert contar("convocatoria") == 3


def test_times_are_normalized_to_utc(client, creador_headers, reloj, espacio, caso_publicado):
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id, inicio="2026-11-03T03:00:00-05:00", fin="2026-11-03T05:00:00-05:00")
    sesion = client.post(SESSIONS_URL, headers=creador_headers, json=datos).json()
    assert sesion["inicio"].startswith("2026-11-03T08:00:00") and sesion["inicio"].endswith(("Z", "+00:00"))


def test_naive_datetimes_are_rejected(client, creador_headers, reloj, espacio, caso_publicado):
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id, inicio="2026-11-03T08:00:00", fin="2026-11-03T10:00:00")
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos).status_code == 422


@pytest.mark.parametrize("estado", ["BORRADOR", "ARCHIVADO"])
def test_draft_and_archived_cases_cannot_be_scheduled(client, creador_headers, reloj, espacio, estado):
    caso = crear_caso_completo(client, creador_headers)
    if estado == "ARCHIVADO":
        client.post(f"{CASES_URL}/{caso['id']}/publish", headers=creador_headers)
        client.post(f"{CASES_URL}/{caso['id']}/archive", headers=creador_headers)
    response = client.post(SESSIONS_URL, headers=creador_headers, json=datos_sesion(reloj, caso["id"], espacio.id))
    assert response.status_code == 409
    assert estado in response.json()["detail"]
    assert contar("sesion") == 0


@pytest.mark.parametrize(
    ("cambio", "codigo"),
    [
        ({"inicio_h": 3, "fin_h": 1}, 422),  # inicio posterior a fin
        ({"inicio_h": 2, "fin_h": 2}, 422),  # inicio igual a fin
        ({"inicio_h": -3, "fin_h": -1}, 400),  # ventana ya terminada
    ],
)
def test_invalid_dates_rejected(client, creador_headers, reloj, espacio, caso_publicado, cambio, codigo):
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id) | ventana(reloj, **cambio)
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos).status_code == codigo


def test_session_already_started_window_is_allowed(client, creador_headers, reloj, espacio, caso_publicado):
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id) | ventana(reloj, inicio_h=-0.5, fin_h=1)
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos).status_code == 201


@pytest.mark.parametrize(("tiempo", "codigo"), [(46, 400), (0, 422), (241, 422)])
def test_max_time_cannot_exceed_case_estimate(client, creador_headers, reloj, espacio, caso_publicado, tiempo, codigo):
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id, tiempo_max_min=tiempo)
    response = client.post(SESSIONS_URL, headers=creador_headers, json=datos)
    assert response.status_code == codigo
    if codigo == 400:
        assert "45 min" in response.json()["detail"]


def test_max_time_equal_to_estimate_is_allowed(client, creador_headers, reloj, espacio, caso_publicado):
    assert programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, tiempo_max_min=45)["tiempo_max_min"] == 45


def test_inactive_or_missing_space_rejected(client, creador_headers, reloj, espacio, caso_publicado, db):
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos_sesion(reloj, caso_publicado["id"], uuid.uuid4())).status_code == 404
    espacio.activo = False
    db.commit()
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos_sesion(reloj, caso_publicado["id"], espacio.id)).status_code == 400


def test_only_case_author_can_schedule(client, reloj, espacio, caso_publicado, make_user, docente_headers):
    # Otro docente (incluso con permiso de crear casos) no puede programar un caso ajeno.
    otro = make_user(RolUsuario.DOCENTE, puede_crear_casos=True)
    for headers in (docente_headers, bearer(login(client, otro.correo))):
        response = client.post(SESSIONS_URL, headers=headers, json=datos_sesion(reloj, caso_publicado["id"], espacio.id))
        assert response.status_code == 404


def test_author_without_create_permission_can_still_schedule(client, creador, creador_headers, reloj, espacio, caso_publicado, db):
    creador.puede_crear_casos = False
    db.merge(creador)
    db.commit()
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos_sesion(reloj, caso_publicado["id"], espacio.id)).status_code == 201


@pytest.mark.parametrize("fixture", ["admin_headers", "estudiante_headers"])
def test_admin_and_student_cannot_schedule(client, request, reloj, espacio, caso_publicado, fixture):
    headers = request.getfixturevalue(fixture)
    assert client.post(SESSIONS_URL, headers=headers, json=datos_sesion(reloj, caso_publicado["id"], espacio.id)).status_code == 403
    assert client.get(SESSIONS_URL, headers=headers).status_code == 403


def test_docente_id_cannot_be_sent(client, creador_headers, reloj, espacio, caso_publicado, docente):
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id, docente_id=str(docente.id))
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos).status_code == 422


@pytest.mark.parametrize(
    ("estudiantes_extra", "codigo"),
    [
        (["noexiste@universidad.edu.co"], 400),
        ("docente", 400),  # un docente no es estudiante
        ("inactivo", 400),
        ("repetido", 422),
    ],
)
def test_invalid_students_reject_whole_scheduling(
    client, creador_headers, reloj, espacio, caso_publicado, estudiantes, make_user, docente, estudiantes_extra, codigo
):
    correos = [e.correo for e in estudiantes]
    if estudiantes_extra == "docente":
        correos.append(docente.correo)
    elif estudiantes_extra == "inactivo":
        correos.append(make_user(RolUsuario.ESTUDIANTE, clave=None, activo=False).correo)
    elif estudiantes_extra == "repetido":
        correos.append(correos[0])
    else:
        correos += estudiantes_extra
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id) | {"estudiantes": correos}
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos).status_code == codigo
    assert contar("sesion") == 0 and contar("convocatoria") == 0


def test_failure_during_convocation_rolls_back_session(client, creador_headers, reloj, espacio, caso_publicado, estudiantes, monkeypatch):
    llamadas = {"n": 0}
    original = sesion_service.generar_codigo

    def falla_en_el_tercero():
        llamadas["n"] += 1
        if llamadas["n"] == 3:
            raise RuntimeError("fallo simulado al generar el código")
        return original()

    monkeypatch.setattr(sesion_service, "generar_codigo", falla_en_el_tercero)
    with pytest.raises(RuntimeError):
        client.post(SESSIONS_URL, headers=creador_headers, json=datos_sesion(reloj, caso_publicado["id"], espacio.id, estudiantes))
    assert contar("sesion") == 0 and contar("convocatoria") == 0


def test_scheduling_does_not_modify_the_case(client, creador_headers, reloj, espacio, caso_publicado):
    antes = client.get(f"{CASES_URL}/{caso_publicado['id']}", headers=creador_headers).json()
    programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    assert client.get(f"{CASES_URL}/{caso_publicado['id']}", headers=creador_headers).json() == antes


# --- Consulta y aislamiento -------------------------------------------------------------


def test_list_and_get_own_sessions(client, creador_headers, otro_creador_headers, reloj, espacio, caso_publicado):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    listado = client.get(SESSIONS_URL, headers=creador_headers).json()
    assert listado["total"] == 1 and listado["items"][0]["id"] == sesion["id"]
    assert client.get(SESSIONS_URL, headers=creador_headers, params={"estado": "EN_CURSO"}).json()["total"] == 0

    assert client.get(SESSIONS_URL, headers=otro_creador_headers).json()["total"] == 0
    for metodo, sufijo, cuerpo in (
        ("get", "", None),
        ("patch", "", {"lugar": "Otro"}),
        ("post", "/start", None),
        ("post", "/finish", None),
        ("get", "/convocations", None),
        ("post", "/convocations", {"estudiantes": ["x@universidad.edu.co"]}),
    ):
        kwargs = {"headers": otro_creador_headers} | ({"json": cuerpo} if cuerpo else {})
        response = getattr(client, metodo)(f"{SESSIONS_URL}/{sesion['id']}{sufijo}", **kwargs)
        assert response.status_code == 404, (metodo, sufijo)


# --- Modificación -----------------------------------------------------------------------


def test_update_scheduled_session(client, creador_headers, reloj, espacio, caso_publicado):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    nueva = ventana(reloj, inicio_h=2, fin_h=6)
    response = client.patch(
        f"{SESSIONS_URL}/{sesion['id']}", headers=creador_headers, json=nueva | {"tiempo_max_min": 30, "lugar": None}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tiempo_max_min"] == 30 and body["lugar"] is None
    assert body["limite_ingreso"].startswith("2026-11-03T11:00:00")


@pytest.mark.parametrize(
    ("cambio", "codigo"),
    [
        ({"tiempo_max_min": 46}, 400),
        ({"fin": "2026-11-03T07:30:00+00:00"}, 400),  # fin antes que el inicio actual (08:00)
        ({"inicio": None}, 400),
        ({"estado": "EN_CURSO"}, 422),
        ({"docente_id": str(uuid.uuid4())}, 422),
    ],
)
def test_invalid_updates(client, creador_headers, reloj, espacio, caso_publicado, cambio, codigo):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    assert client.patch(f"{SESSIONS_URL}/{sesion['id']}", headers=creador_headers, json=cambio).status_code == codigo


def test_change_case_only_to_another_published_own_case(client, creador_headers, reloj, espacio, caso_publicado):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    borrador = crear_caso_completo(client, creador_headers)
    url = f"{SESSIONS_URL}/{sesion['id']}"
    assert client.patch(url, headers=creador_headers, json={"caso_id": borrador["id"]}).status_code == 409
    client.post(f"{CASES_URL}/{borrador['id']}/publish", headers=creador_headers)
    assert client.patch(url, headers=creador_headers, json={"caso_id": borrador["id"]}).json()["caso_id"] == borrador["id"]


def test_in_progress_session_only_allows_informative_changes(client, creador_headers, reloj, espacio, caso_publicado):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    assert iniciar(client, creador_headers, sesion["id"], reloj).status_code == 200
    url = f"{SESSIONS_URL}/{sesion['id']}"

    response = client.patch(url, headers=creador_headers, json={"lugar": "Aula 105", "mensaje": "Cambio de salón"})
    assert response.status_code == 200 and response.json()["lugar"] == "Aula 105"
    for cambio in ({"tiempo_max_min": 30}, {"fin": (reloj.actual + timedelta(hours=5)).isoformat()}, {"caso_id": caso_publicado["id"]}):
        response = client.patch(url, headers=creador_headers, json=cambio)
        assert response.status_code == 409, cambio


def test_finished_session_cannot_be_modified(client, creador_headers, reloj, espacio, caso_publicado):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    iniciar(client, creador_headers, sesion["id"], reloj)
    client.post(f"{SESSIONS_URL}/{sesion['id']}/finish", headers=creador_headers)
    assert client.patch(f"{SESSIONS_URL}/{sesion['id']}", headers=creador_headers, json={"lugar": "X"}).status_code == 409


def test_archived_case_keeps_existing_sessions(client, creador_headers, reloj, espacio, caso_publicado):
    """Archivar impide programar NUEVAS sesiones, pero no altera las ya programadas."""
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    assert client.post(f"{CASES_URL}/{caso_publicado['id']}/archive", headers=creador_headers).status_code == 200
    assert client.get(f"{SESSIONS_URL}/{sesion['id']}", headers=creador_headers).json()["estado"] == "SIN_INICIAR"
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos_sesion(reloj, caso_publicado["id"], espacio.id)).status_code == 409
