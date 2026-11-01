"""Autenticación estudiantil por convocatoria, alcance del token y revocación."""

from datetime import timedelta

import pytest

from app.core.config import get_settings
from app.modules.sessions.security import LimitadorIntentos, crear_token_estudiante, get_limitador, huella_codigo
from tests.cases_helpers import CASES_URL
from tests.conftest import bearer
from tests.sessions_helpers import SESSIONS_URL, codigo_de, convocatorias, iniciar, ingresar, programar


@pytest.fixture
def escenario(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    """Sesión 08:00-10:00 iniciada a las 08:00, con 3 estudiantes y sus códigos."""
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)
    lista = convocatorias(client, creador_headers, sesion["id"])
    por_correo = {c["estudiante"]["correo"]: c for c in lista}
    codigos = {e.correo: codigo_de(client, creador_headers, sesion["id"], por_correo[e.correo]["id"]) for e in estudiantes}
    assert iniciar(client, creador_headers, sesion["id"], reloj).status_code == 200
    return {"sesion": sesion, "convocatorias": por_correo, "codigos": codigos, "estudiantes": estudiantes}


def _acceso(client, sesion_id, token):
    return client.get(f"/api/v1/student/sessions/{sesion_id}", headers=bearer(token))


def test_student_login_with_valid_code(client, escenario):
    estudiante = escenario["estudiantes"][0]
    response = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo])
    assert response.status_code == 200
    body = response.json()
    assert body["sesion_id"] == escenario["sesion"]["id"]
    assert body["convocatoria_id"] == escenario["convocatorias"][estudiante.correo]["id"]
    # Expiración de seguridad configurable, independiente de tiempo_max_min (40 min en esta sesión).
    assert body["expires_in"] == get_settings().STUDENT_TOKEN_EXPIRE_MINUTES * 60 != 40 * 60
    assert response.headers["cache-control"] == "no-store"

    acceso = _acceso(client, escenario["sesion"]["id"], body["access_token"])
    assert acceso.status_code == 200
    datos = acceso.json()
    assert datos["estudiante_id"] == str(estudiante.id)
    assert datos["sesion"]["caso_nombre"] == "Conformidad en el aula"
    assert "codigo" not in acceso.text and "es_correcta" not in acceso.text


def test_code_is_normalized(client, escenario):
    estudiante = escenario["estudiantes"][0]
    codigo = escenario["codigos"][estudiante.correo]
    escrito = f" {codigo[:4].lower()}-{codigo[4:8]} {codigo[8:]} "
    assert ingresar(client, estudiante.correo.upper(), escrito).status_code == 200


@pytest.mark.parametrize("caso", ["codigo-incorrecto", "correo-incorrecto", "codigo-de-otro", "formato-invalido", "docente"])
def test_invalid_credentials_get_the_same_generic_error(client, escenario, docente, caso):
    a, b = escenario["estudiantes"][:2]
    correo, codigo = {
        "codigo-incorrecto": (a.correo, "0" * 12),
        "correo-incorrecto": ("nadie@universidad.edu.co", escenario["codigos"][a.correo]),
        "codigo-de-otro": (a.correo, escenario["codigos"][b.correo]),
        "formato-invalido": (a.correo, "x"),
        "docente": (docente.correo, escenario["codigos"][a.correo]),
    }[caso]
    response = ingresar(client, correo, codigo)
    assert response.status_code == 401
    assert response.json() == {"detail": "Correo o código de acceso incorrectos."}
    assert escenario["codigos"][a.correo] not in response.text


def test_inactive_student_rejected(client, escenario, db):
    estudiante = escenario["estudiantes"][0]
    estudiante.activo = False
    db.merge(estudiante)
    db.commit()
    response = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo])
    assert response.status_code == 403


def test_session_not_started_rejected(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)
    convocatoria = convocatorias(client, creador_headers, sesion["id"])[0]
    codigo = codigo_de(client, creador_headers, sesion["id"], convocatoria["id"])
    reloj.avanzar(hours=1, minutes=10)  # dentro de la ventana, pero el docente no la inició
    response = ingresar(client, convocatoria["estudiante"]["correo"], codigo)
    assert response.status_code == 403
    assert "no ha sido iniciada" in response.json()["detail"]


def test_finished_session_rejected(client, creador_headers, escenario):
    client.post(f"{SESSIONS_URL}/{escenario['sesion']['id']}/finish", headers=creador_headers)
    estudiante = escenario["estudiantes"][0]
    response = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo])
    assert response.status_code == 403 and "finalizado" in response.json()["detail"]


def test_late_entry_rejected_and_exact_limit_allowed(client, reloj, escenario):
    a, b = escenario["estudiantes"][:2]
    reloj.avanzar(hours=1)  # 09:00:00 exacto = límite
    assert ingresar(client, a.correo, escenario["codigos"][a.correo]).status_code == 200
    reloj.avanzar(seconds=1)  # 09:00:01
    response = ingresar(client, b.correo, escenario["codigos"][b.correo])
    assert response.status_code == 403 and "plazo de ingreso" in response.json()["detail"]


def test_late_limit_does_not_expel_students_already_inside(client, reloj, escenario):
    estudiante = escenario["estudiantes"][0]
    token = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).json()["access_token"]
    reloj.avanzar(minutes=61)  # pasado el límite de ingreso
    assert _acceso(client, escenario["sesion"]["id"], token).status_code == 200


def test_token_is_bound_to_its_session(client, creador_headers, reloj, espacio, caso_publicado, escenario):
    estudiante = escenario["estudiantes"][0]
    token = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).json()["access_token"]
    otra = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, [estudiante], **{
        "inicio": reloj.actual.isoformat(), "fin": (reloj.actual + timedelta(hours=2)).isoformat()
    })
    iniciar(client, creador_headers, otra["id"])
    response = _acceso(client, otra["id"], token)
    assert response.status_code == 403
    # Tampoco sirve un token "estirado" a otra convocatoria con la misma huella: la firma lo impide.
    otra_conv = convocatorias(client, creador_headers, otra["id"])[0]
    falso = crear_token_estudiante(
        estudiante_id=estudiante.id,
        convocatoria_id=otra_conv["id"],
        sesion_id=escenario["sesion"]["id"],
        version_usuario=0,
        huella_codigo=huella_codigo(escenario["codigos"][estudiante.correo]),
        duracion=timedelta(minutes=5),
    )
    assert _acceso(client, escenario["sesion"]["id"], falso).status_code == 401


def test_access_revoked_after_removal(client, creador_headers, escenario):
    estudiante = escenario["estudiantes"][0]
    token = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).json()["access_token"]
    convocatoria = escenario["convocatorias"][estudiante.correo]
    assert client.delete(f"{SESSIONS_URL}/{escenario['sesion']['id']}/convocations/{convocatoria['id']}", headers=creador_headers).status_code == 204

    assert _acceso(client, escenario["sesion"]["id"], token).status_code == 401
    # Con revocación lógica el código sigue identificando la convocatoria: se informa el motivo (403).
    response = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo])
    assert response.status_code == 403 and "revocada" in response.json()["detail"]


@pytest.mark.parametrize("motivo", ["regenerar-codigo", "desactivar-cuenta", "finalizar-sesion"])
def test_existing_tokens_stop_authorizing(client, creador_headers, admin_headers, escenario, motivo):
    estudiante = escenario["estudiantes"][0]
    token = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).json()["access_token"]
    sesion_id = escenario["sesion"]["id"]
    conv_id = escenario["convocatorias"][estudiante.correo]["id"]
    if motivo == "regenerar-codigo":
        client.post(f"{SESSIONS_URL}/{sesion_id}/convocations/{conv_id}/regenerate-code", headers=creador_headers, json={"motivo": "Código filtrado"})
    elif motivo == "desactivar-cuenta":
        client.patch(f"/api/v1/users/{estudiante.id}/status", headers=admin_headers, json={"activo": False})
    else:
        client.post(f"{SESSIONS_URL}/{sesion_id}/finish", headers=creador_headers)
    assert _acceso(client, sesion_id, token).status_code == 401


def test_student_token_cannot_reach_admin_or_teacher_endpoints(client, escenario):
    estudiante = escenario["estudiantes"][0]
    headers = bearer(ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).json()["access_token"])
    for metodo, url in (
        ("get", "/api/v1/auth/me"),
        ("get", "/api/v1/users"),
        ("get", CASES_URL),
        ("get", SESSIONS_URL),
        ("get", f"{SESSIONS_URL}/{escenario['sesion']['id']}/convocations"),
        ("get", "/api/v1/academic-spaces"),
        ("patch", f"{SESSIONS_URL}/{escenario['sesion']['id']}/convocations/{escenario['convocatorias'][estudiante.correo]['id']}/attempts"),
    ):
        assert getattr(client, metodo)(url, headers=headers).status_code == 401, url


def test_staff_token_cannot_be_used_as_student_token(client, creador_headers, escenario):
    assert client.get(f"/api/v1/student/sessions/{escenario['sesion']['id']}", headers=creador_headers).status_code == 401
    assert client.get(f"/api/v1/student/sessions/{escenario['sesion']['id']}").status_code == 401


def test_expired_student_token_rejected(client, escenario):
    estudiante = escenario["estudiantes"][0]
    conv = escenario["convocatorias"][estudiante.correo]
    vencido = crear_token_estudiante(
        estudiante_id=estudiante.id,
        convocatoria_id=conv["id"],
        sesion_id=escenario["sesion"]["id"],
        version_usuario=0,
        huella_codigo=huella_codigo(escenario["codigos"][estudiante.correo]),
        duracion=timedelta(seconds=-1),
    )
    assert _acceso(client, escenario["sesion"]["id"], vencido).status_code == 401


# --- Limitación de intentos ---------------------------------------------------------------


@pytest.fixture
def limitador_de_prueba(reloj):
    from app.main import app

    limitador = LimitadorIntentos(reloj, timedelta(minutes=15), max_por_correo=3, max_por_ip=10)
    app.dependency_overrides[get_limitador] = lambda: limitador
    yield limitador
    app.dependency_overrides.pop(get_limitador, None)


def test_rate_limit_blocks_guessing_even_with_correct_code(client, escenario, limitador_de_prueba, reloj):
    estudiante = escenario["estudiantes"][0]
    for _ in range(3):
        assert ingresar(client, estudiante.correo, "0" * 12).status_code == 401
    bloqueado = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo])
    assert bloqueado.status_code == 429
    assert int(bloqueado.headers["retry-after"]) == 15 * 60

    # Otro estudiante no queda afectado por el bloqueo de este correo.
    otro = escenario["estudiantes"][1]
    assert ingresar(client, otro.correo, escenario["codigos"][otro.correo]).status_code == 200

    reloj.avanzar(minutes=15)
    assert ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).status_code == 200


def test_rate_limit_per_ip(client, escenario, limitador_de_prueba):
    for i in range(10):
        ingresar(client, f"intruso{i}@universidad.edu.co", "0" * 12)
    estudiante = escenario["estudiantes"][2]
    assert ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).status_code == 429


def test_successful_login_clears_failures(client, escenario, limitador_de_prueba):
    estudiante = escenario["estudiantes"][0]
    for _ in range(2):
        ingresar(client, estudiante.correo, "0" * 12)
    assert ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).status_code == 200
    for _ in range(2):
        assert ingresar(client, estudiante.correo, "0" * 12).status_code == 401  # el contador se reinició


def test_codes_never_appear_in_logs(client, creador_headers, escenario, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    estudiante = escenario["estudiantes"][0]
    sesion_id = escenario["sesion"]["id"]
    conv_id = escenario["convocatorias"][estudiante.correo]["id"]
    codigo = codigo_de(client, creador_headers, sesion_id, conv_id)
    ingresar(client, estudiante.correo, codigo)
    ingresar(client, estudiante.correo, "0" * 12)
    nuevo = client.post(
        f"{SESSIONS_URL}/{sesion_id}/convocations/{conv_id}/regenerate-code", headers=creador_headers, json={"motivo": "Prueba de registros"}
    ).json()["codigo_autorizacion"]
    registros = "\n".join(r.getMessage() for r in caplog.records)
    assert "regenerado" in registros
    assert codigo not in registros and nuevo not in registros


def test_user_version_change_revokes_student_token(client, escenario, db):
    """El token lleva la version_token del usuario (módulo 1): si cambia, deja de autorizar."""
    estudiante = escenario["estudiantes"][0]
    token = ingresar(client, estudiante.correo, escenario["codigos"][estudiante.correo]).json()["access_token"]
    from sqlalchemy import text

    db.execute(text("UPDATE usuario SET version_token = version_token + 1 WHERE id = :id"), {"id": estudiante.id})
    db.commit()
    assert _acceso(client, escenario["sesion"]["id"], token).status_code == 401
