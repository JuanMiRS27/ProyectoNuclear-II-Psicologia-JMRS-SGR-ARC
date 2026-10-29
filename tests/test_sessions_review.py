"""Correcciones del módulo 3: expiración del token, ventana de ingreso tras `fin` y cifrado de códigos."""

import ast
import base64
import os
import uuid
from datetime import timedelta

import jwt
import pytest
from alembic import command
from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, engine
from app.modules.auth.models import RolUsuario
from app.modules.sessions.models import Convocatoria
from app.modules.sessions.security import CodigoCifradoInvalidoError, cifrar_codigo, descifrar_codigo
from app.modules.sessions.services import AccesoEstudiantilService, sesion_service
from app.modules.sessions.states import IngresoNoPermitidoError
from tests.conftest import PROJECT_ROOT, alembic_config, bearer
from tests.sessions_helpers import SESSIONS_URL, codigo_de, convocatorias, datos_sesion, iniciar, ingresar, programar


@pytest.fixture
def en_curso(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    """Sesión 08:00-10:00 (límite 09:00) iniciada a las 08:00, con 3 estudiantes y sus códigos."""
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)
    lista = {c["estudiante"]["correo"]: c for c in convocatorias(client, creador_headers, sesion["id"])}
    codigos = {correo: codigo_de(client, creador_headers, sesion["id"], c["id"]) for correo, c in lista.items()}
    assert iniciar(client, creador_headers, sesion["id"], reloj).status_code == 200
    return {"sesion": sesion, "convocatorias": lista, "codigos": codigos, "estudiantes": estudiantes}


def _token(client, en_curso, indice=0) -> str:
    estudiante = en_curso["estudiantes"][indice]
    response = ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo])
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _acceso(client, sesion_id, token):
    return client.get(f"/api/v1/student/sessions/{sesion_id}", headers=bearer(token))


# --- 1. Expiración del token independiente del tiempo de participación -----------------------


@pytest.mark.parametrize("tiempo_max", [5, 45])
def test_token_expiration_does_not_depend_on_participation_time(
    client, creador_headers, reloj, espacio, caso_publicado, estudiantes, tiempo_max
):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes[:1], tiempo_max_min=tiempo_max)
    conv = convocatorias(client, creador_headers, sesion["id"])[0]
    codigo = codigo_de(client, creador_headers, sesion["id"], conv["id"])
    iniciar(client, creador_headers, sesion["id"], reloj)

    body = ingresar(client, estudiantes[0].correo, codigo).json()
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    esperado = get_settings().STUDENT_TOKEN_EXPIRE_MINUTES * 60
    assert claims["exp"] - claims["iat"] == esperado == body["expires_in"]
    assert esperado != tiempo_max * 60


def test_token_expiration_is_configurable(client, en_curso, monkeypatch):
    monkeypatch.setattr(get_settings(), "STUDENT_TOKEN_EXPIRE_MINUTES", 30)
    estudiante = en_curso["estudiantes"][0]
    assert ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo]).json()["expires_in"] == 1800


def test_token_still_authenticates_after_tiempo_max_elapsed(client, reloj, en_curso):
    token = _token(client, en_curso)
    reloj.avanzar(minutes=41)  # tiempo_max_min de la sesión = 40
    assert _acceso(client, en_curso["sesion"]["id"], token).status_code == 200


def test_revocation_still_works_with_long_lived_token(client, creador_headers, en_curso):
    token = _token(client, en_curso)
    estudiante = en_curso["estudiantes"][0]
    conv = en_curso["convocatorias"][estudiante.correo]
    client.post(
        f"{SESSIONS_URL}/{en_curso['sesion']['id']}/convocations/{conv['id']}/regenerate-code",
        headers=creador_headers,
        json={"motivo": "Código expuesto en clase"},
    )
    assert _acceso(client, en_curso["sesion"]["id"], token).status_code == 401


# --- 2. Ventana de ingreso tras el fin programado ------------------------------------------------


@pytest.mark.parametrize("despues_del_fin", [timedelta(0), timedelta(seconds=1), timedelta(hours=23, minutes=59)])
def test_no_new_entries_after_fin_while_still_in_progress(client, reloj, en_curso, despues_del_fin):
    estudiante = en_curso["estudiantes"][0]
    reloj.actual = reloj.actual.replace(hour=10, minute=0, second=0) + despues_del_fin
    # Sigue EN_CURSO: el cierre automático (fin + 24 h) aún no ha corrido.
    response = ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo])
    assert response.status_code == 403
    assert "ventana programada" in response.json()["detail"]


def test_administrative_state_entry_window_and_participation_are_distinct(client, creador_headers, reloj, en_curso):
    sesion_id = en_curso["sesion"]["id"]
    token = _token(client, en_curso)  # 08:00, dentro de la ventana de ingreso

    datos = _acceso(client, sesion_id, token).json()
    assert datos["sesion"]["estado"] == "EN_CURSO" and datos["puede_iniciar_participacion"] is True

    reloj.avanzar(hours=1, seconds=1)  # 09:00:01: pasado el límite, antes de fin
    datos = _acceso(client, sesion_id, token).json()
    assert datos["sesion"]["estado"] == "EN_CURSO" and datos["puede_iniciar_participacion"] is False

    reloj.avanzar(hours=5)  # 14:00: después de fin, aún EN_CURSO
    response = _acceso(client, sesion_id, token)
    assert response.status_code == 200  # sigue autenticado (la continuidad la decidirá el módulo 4)
    assert response.json()["puede_iniciar_participacion"] is False
    assert client.get(f"{SESSIONS_URL}/{sesion_id}", headers=creador_headers).json()["estado"] == "EN_CURSO"


def test_participation_start_guard_for_module_4(client, reloj, en_curso):
    """Contrato para el módulo 4: crear una participación exige `asegurar_inicio_participacion`."""
    token = _token(client, en_curso)
    with SessionLocal() as s:
        acceso = AccesoEstudiantilService(s, reloj).resolver(token, uuid.UUID(en_curso["sesion"]["id"]))
        acceso.asegurar_inicio_participacion(reloj.ahora())  # 08:00: permitido
        acceso.asegurar_inicio_participacion(reloj.ahora() + timedelta(hours=1))  # 09:00 exacto: permitido
        for instante in (timedelta(hours=1, seconds=1), timedelta(hours=2), timedelta(hours=20)):
            with pytest.raises(IngresoNoPermitidoError):
                acceso.asegurar_inicio_participacion(reloj.ahora() + instante)


def test_automatic_close_does_not_extend_the_entry_window(client, reloj, en_curso):
    from app.workers.session_expiration import ejecutar_una_vez

    estudiante = en_curso["estudiantes"][0]
    reloj.actual = reloj.actual.replace(hour=10) + timedelta(hours=23)
    assert ejecutar_una_vez(reloj=reloj) == []  # todavía no corresponde cerrar
    assert ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo]).status_code == 403
    reloj.avanzar(hours=1)
    assert len(ejecutar_una_vez(reloj=reloj)) == 1
    response = ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo])
    assert response.status_code == 403 and "finalizado" in response.json()["detail"]


def test_no_new_convocations_after_entry_window(client, creador_headers, reloj, en_curso, make_user):
    url = f"{SESSIONS_URL}/{en_curso['sesion']['id']}/convocations"
    reloj.avanzar(hours=1)  # 09:00 exacto: todavía se puede convocar
    assert client.post(url, headers=creador_headers, json={"estudiantes": [make_user(RolUsuario.ESTUDIANTE, clave=None).correo]}).status_code == 201
    reloj.avanzar(seconds=1)
    response = client.post(url, headers=creador_headers, json={"estudiantes": [make_user(RolUsuario.ESTUDIANTE, clave=None).correo]})
    assert response.status_code == 409 and "ventana de ingreso" in response.json()["detail"]


def test_existing_convocations_remain_manageable_after_entry_window(client, creador_headers, reloj, en_curso):
    """Retirar, aumentar intentos y regenerar códigos siguen permitidos mientras no esté FINALIZADO."""
    sesion_id = en_curso["sesion"]["id"]
    a, b = list(en_curso["convocatorias"].values())[:2]
    reloj.avanzar(hours=1, minutes=30)
    base = f"{SESSIONS_URL}/{sesion_id}/convocations"
    assert client.patch(f"{base}/{a['id']}/attempts", headers=creador_headers, json={"intentos_autorizados": 2, "motivo": "Falla de energía"}).status_code == 200
    assert client.post(f"{base}/{a['id']}/regenerate-code", headers=creador_headers, json={"motivo": "Código expuesto"}).status_code == 200
    assert client.delete(f"{base}/{b['id']}", headers=creador_headers).status_code == 204


def test_scheduling_with_students_after_entry_window_is_rejected(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    # Ventana 05:00-08:00 con el reloj a las 07:00: el límite de ingreso (06:30) ya pasó.
    ventana_pasada = {"inicio": (reloj.actual - timedelta(hours=2)).isoformat(), "fin": (reloj.actual + timedelta(hours=1)).isoformat()}
    datos = datos_sesion(reloj, caso_publicado["id"], espacio.id, estudiantes[:1]) | ventana_pasada
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos).status_code == 409
    # Sin estudiantes sí se puede (p. ej., para registrar la sesión), aunque nadie podría ingresar.
    assert client.post(SESSIONS_URL, headers=creador_headers, json=datos | {"estudiantes": []}).status_code == 201


# --- 3. Cifrado de los códigos en reposo ---------------------------------------------------------


def _valor_bd(convocatoria_id) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT codigo_autorizacion FROM convocatoria WHERE id = :id"), {"id": convocatoria_id}).scalar_one()


def test_codes_are_stored_encrypted(en_curso):
    with engine.connect() as conn:
        filas = conn.execute(text("SELECT id, codigo_autorizacion FROM convocatoria")).all()
    claros = set(en_curso["codigos"].values())
    for convocatoria_id, almacenado in filas:
        assert almacenado.startswith("v1.") and len(almacenado) == 57
        assert all(claro not in almacenado for claro in claros)
        assert descifrar_codigo(almacenado, convocatoria_id) in claros
    assert len({a for _, a in filas}) == len(filas)


def test_same_code_encrypts_differently_each_time():
    convocatoria_id = uuid.uuid4()
    a, b = cifrar_codigo("ABCDEFGHJKMN", convocatoria_id), cifrar_codigo("ABCDEFGHJKMN", convocatoria_id)
    assert a != b and descifrar_codigo(a, convocatoria_id) == descifrar_codigo(b, convocatoria_id) == "ABCDEFGHJKMN"


def test_tampered_ciphertext_is_detected(client, creador_headers, en_curso):
    estudiante = en_curso["estudiantes"][0]
    conv = en_curso["convocatorias"][estudiante.correo]
    original = _valor_bd(conv["id"])
    alterado = original[:-2] + ("AA" if original[-2:] != "AA" else "BB")
    with engine.begin() as conn:
        conn.execute(text("UPDATE convocatoria SET codigo_autorizacion = :v WHERE id = :id"), {"v": alterado, "id": conv["id"]})

    assert ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo]).status_code == 401
    url = f"{SESSIONS_URL}/{en_curso['sesion']['id']}/convocations/{conv['id']}"
    response = client.get(f"{url}/code", headers=creador_headers)
    assert response.status_code == 409 and "regenérelo" in response.json()["detail"]
    # Regenerar repara la convocatoria.
    nuevo = client.post(f"{url}/regenerate-code", headers=creador_headers, json={"motivo": "Valor dañado"}).json()["codigo_autorizacion"]
    assert ingresar(client, estudiante.correo, nuevo).status_code == 200


def test_ciphertext_is_bound_to_its_convocation(client, en_curso):
    a, b = en_curso["estudiantes"][:2]
    conv_a, conv_b = en_curso["convocatorias"][a.correo], en_curso["convocatorias"][b.correo]
    cifrado_b = _valor_bd(conv_b["id"])
    with pytest.raises(CodigoCifradoInvalidoError):
        descifrar_codigo(cifrado_b, uuid.UUID(conv_a["id"]))
    # Copiar el cifrado de B en la fila de A no le da a A el código de B.
    with engine.begin() as conn:
        conn.execute(text("UPDATE convocatoria SET codigo_autorizacion = :v WHERE id = :id"), {"v": cifrado_b, "id": conv_a["id"]})
    assert ingresar(client, a.correo, en_curso["codigos"][b.correo]).status_code == 401


def test_wrong_key_cannot_decrypt(monkeypatch):
    convocatoria_id = uuid.uuid4()
    cifrado = cifrar_codigo("ABCDEFGHJKMN", convocatoria_id)
    monkeypatch.setattr(get_settings(), "CONVOCATION_CODE_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    with pytest.raises(CodigoCifradoInvalidoError):
        descifrar_codigo(cifrado, convocatoria_id)


@pytest.mark.parametrize("clave", [None, "corta", base64.urlsafe_b64encode(os.urandom(16)).decode(), "no/es+base64-url!"])
def test_encryption_key_is_required_and_validated(clave, monkeypatch):
    monkeypatch.delenv("CONVOCATION_CODE_ENCRYPTION_KEY", raising=False)
    valores = {"DATABASE_URL": "postgresql+psycopg://u:p@localhost/x_test", "JWT_SECRET_KEY": "x" * 40}
    if clave is not None:
        valores["CONVOCATION_CODE_ENCRYPTION_KEY"] = clave
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **valores)


def test_codes_are_unique_among_the_students_convocations(client, creador_headers, reloj, espacio, caso_publicado, estudiantes, monkeypatch):
    estudiante = estudiantes[0]
    primera = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, [estudiante])
    conv1 = convocatorias(client, creador_headers, primera["id"])[0]
    repetido = codigo_de(client, creador_headers, primera["id"], conv1["id"])

    secuencia = iter([repetido, repetido, "ZZZZZZZZZZZZ"])
    monkeypatch.setattr(sesion_service, "generar_codigo", lambda: next(secuencia))
    segunda = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, [estudiante])
    conv2 = convocatorias(client, creador_headers, segunda["id"])[0]
    assert codigo_de(client, creador_headers, segunda["id"], conv2["id"]) == "ZZZZZZZZZZZZ"


def test_code_is_recoverable_for_notifications(db, en_curso):
    """Compatibilidad con el módulo 6: con la clave del entorno, el código se recupera desde la convocatoria."""
    estudiante = en_curso["estudiantes"][0]
    conv = db.get(Convocatoria, uuid.UUID(en_curso["convocatorias"][estudiante.correo]["id"]))
    assert descifrar_codigo(conv.codigo_autorizacion, conv.id) == en_curso["codigos"][estudiante.correo]


def test_migration_0006_encrypts_and_decrypts_existing_rows(client, creador_headers, en_curso):
    """Downgrade a 0005 deja los códigos en claro; upgrade los vuelve a cifrar sin perderlos."""
    estudiante = en_curso["estudiantes"][0]
    conv_id = en_curso["convocatorias"][estudiante.correo]["id"]
    codigo = en_curso["codigos"][estudiante.correo]
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "0005_sesiones")
    try:
        assert _valor_bd(conv_id) == codigo
    finally:
        engine.dispose()
        command.upgrade(config, "head")
    almacenado = _valor_bd(conv_id)
    assert almacenado.startswith("v1.") and descifrar_codigo(almacenado, uuid.UUID(conv_id)) == codigo
    assert ingresar(client, estudiante.correo, codigo).status_code == 200


def test_migration_0006_is_self_contained():
    arbol = ast.parse((PROJECT_ROOT / "alembic" / "versions" / "0006_cifrado_codigos_convocatoria.py").read_text(encoding="utf-8"))
    importados = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any(m == "app" or m.startswith("app.") for m in importados)
    texto = (PROJECT_ROOT / "alembic" / "versions" / "0006_cifrado_codigos_convocatoria.py").read_text(encoding="utf-8")
    assert "CONVOCATION_CODE_ENCRYPTION_KEY=" not in texto  # la clave nunca está en el repositorio
