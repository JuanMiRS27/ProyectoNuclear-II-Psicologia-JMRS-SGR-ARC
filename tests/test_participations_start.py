"""Inicio, recuperación e intentos de participaciones."""

import uuid
from datetime import timedelta

from app.core.database import SessionLocal
from app.modules.participations.services import ParticipacionService
from app.modules.sessions.security import crear_token_estudiante, huella_codigo
from app.modules.sessions.services import AccesoEstudiantilService
from tests.conftest import bearer
from tests.participations_helpers import PART_URL, contar, en_paralelo, entorno, iniciar_participacion  # noqa: F401
from tests.sessions_helpers import SESSIONS_URL, codigo_de, convocatorias, ingresar, programar


def test_start_creates_first_attempt(entorno):
    response = iniciar_participacion(entorno.client, entorno.headers(), entorno.sesion_id)
    assert response.status_code == 201
    p = response.json()
    assert p["numero_intento"] == 1 and p["estado"] == "EN_PROGRESO" and p["motivo_cierre"] is None
    assert p["inicio"].startswith("2026-11-03T08:00:00") and p["fin"] is None
    assert p["fecha_limite"].startswith("2026-11-03T08:40:00")
    assert p["tiempo_restante_segundos"] == 40 * 60
    assert p["intentos_autorizados"] == 1 and p["intentos_utilizados"] == 1
    assert p["progreso"] == {"total_preguntas": 3, "respondidas": 0, "pendientes": 3, "porcentaje": 0}
    assert "nota_final" not in p and "retroalimentacion_general" not in p


def test_second_request_recovers_same_attempt(entorno):
    headers = entorno.headers()
    primero = iniciar_participacion(entorno.client, headers, entorno.sesion_id)
    entorno.reloj.avanzar(minutes=5)
    segundo = iniciar_participacion(entorno.client, headers, entorno.sesion_id)
    assert (primero.status_code, segundo.status_code) == (201, 200)
    assert segundo.json()["id"] == primero.json()["id"]
    assert segundo.json()["inicio"] == primero.json()["inicio"]  # el temporizador no se reinicia
    assert segundo.json()["tiempo_restante_segundos"] == 35 * 60
    assert contar("SELECT count(*) FROM participacion") == 1


def test_exact_entry_limit_is_allowed_and_late_start_rejected(entorno):
    a, b = entorno.headers(0), entorno.headers(1)
    entorno.reloj.avanzar(hours=1)  # 09:00:00 exacto
    assert iniciar_participacion(entorno.client, a, entorno.sesion_id).status_code == 201
    entorno.reloj.avanzar(seconds=1)
    response = iniciar_participacion(entorno.client, b, entorno.sesion_id)
    assert response.status_code == 403 and "plazo de ingreso" in response.json()["detail"]


def test_attempts_exhausted(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    response = iniciar_participacion(entorno.client, headers, entorno.sesion_id)
    assert response.status_code == 409 and "intentos" in response.json()["detail"]


def test_extra_attempt_authorized_by_teacher(entorno):
    headers = entorno.headers()
    p1 = entorno.iniciar(headers)
    # Con un intento en progreso, el intento extra no puede empezar: se recupera el actual.
    entorno.client.patch(entorno.conv_url(0, "/attempts"), headers=entorno.docente_headers, json={"intentos_autorizados": 2, "motivo": "Corte de luz"})
    assert iniciar_participacion(entorno.client, headers, entorno.sesion_id).json()["id"] == p1["id"]

    entorno.client.post(f"{PART_URL}/{p1['id']}/finish", headers=headers)
    p2 = iniciar_participacion(entorno.client, headers, entorno.sesion_id)
    assert p2.status_code == 201 and p2.json()["numero_intento"] == 2 and p2.json()["intentos_utilizados"] == 2


def test_extra_attempt_only_within_entry_window(entorno):
    headers = entorno.headers()
    p1 = entorno.iniciar(headers)
    entorno.client.post(f"{PART_URL}/{p1['id']}/finish", headers=headers)
    entorno.client.patch(entorno.conv_url(0, "/attempts"), headers=entorno.docente_headers, json={"intentos_autorizados": 2, "motivo": "Corte de luz"})
    entorno.reloj.avanzar(hours=1, minutes=1)  # 09:01: ventana cerrada
    assert iniciar_participacion(entorno.client, headers, entorno.sesion_id).status_code == 403


def test_token_of_another_session_cannot_start_here(entorno, client, creador_headers, reloj, espacio, caso_publicado):
    otra = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, [entorno.estudiantes[0]])
    response = iniciar_participacion(client, entorno.headers(), otra["id"])
    assert response.status_code == 403


def test_revoked_convocation_cannot_start(entorno):
    headers = entorno.headers()
    entorno.client.delete(entorno.conv_url(0), headers=entorno.docente_headers)
    assert iniciar_participacion(entorno.client, headers, entorno.sesion_id).status_code == 401
    estudiante = entorno.estudiantes[0]
    assert ingresar(entorno.client, estudiante.correo, entorno.codigos[estudiante.correo]).status_code == 403


def test_not_started_and_finished_sessions_reject_start(entorno, client, creador_headers, reloj, espacio, caso_publicado):
    # Sesión SIN_INICIAR: ni siquiera un token bien firmado autoriza (el módulo 3 exige EN_CURSO).
    futura = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, [entorno.estudiantes[0]])
    conv = convocatorias(client, creador_headers, futura["id"])[0]
    codigo = codigo_de(client, creador_headers, futura["id"], conv["id"])
    with SessionLocal() as s:
        from app.modules.sessions.models import Convocatoria

        cifrado = s.get(Convocatoria, uuid.UUID(conv["id"])).codigo_autorizacion
    token = crear_token_estudiante(
        estudiante_id=entorno.estudiantes[0].id, convocatoria_id=uuid.UUID(conv["id"]), sesion_id=uuid.UUID(futura["id"]),
        version_usuario=0, huella_codigo=huella_codigo(cifrado), duracion=timedelta(minutes=5),
    )
    assert iniciar_participacion(client, bearer(token), futura["id"]).status_code == 401
    assert ingresar(client, entorno.estudiantes[0].correo, codigo).status_code == 403

    # Sesión FINALIZADO.
    headers = entorno.headers(1)
    client.post(f"{SESSIONS_URL}/{entorno.sesion_id}/finish", headers=creador_headers)
    assert iniciar_participacion(client, headers, entorno.sesion_id).status_code == 401


def test_concurrent_starts_consume_a_single_attempt(entorno):
    headers = entorno.headers()
    token = headers["Authorization"].split()[1]
    sesion_id = uuid.UUID(entorno.sesion_id)

    def iniciar():
        with SessionLocal() as s:
            acceso = AccesoEstudiantilService(s, entorno.reloj).resolver(token, sesion_id)
            vista, creada = ParticipacionService(s, entorno.reloj).iniciar_o_recuperar(acceso)
            return vista.id, creada

    a, b = en_paralelo(iniciar, iniciar)
    assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
    assert a[0] == b[0] and sorted([a[1], b[1]]) == [False, True]
    assert contar("SELECT count(*) FROM participacion") == 1
