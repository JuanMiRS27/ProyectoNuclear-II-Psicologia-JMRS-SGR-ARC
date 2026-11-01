"""Convocatorias, códigos de autorización e intentos."""

import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import engine
from app.modules.auth.models import RolUsuario
from app.modules.sessions.models import ALFABETO_CODIGO, LONGITUD_CODIGO
from app.modules.sessions.security import cifrar_codigo, generar_codigo
from tests.sessions_helpers import SESSIONS_URL, codigo_de, contar, convocatorias, iniciar, ingresar, programar

FORMATO = re.compile(rf"^[{ALFABETO_CODIGO}]{{{LONGITUD_CODIGO}}}$")


@pytest.fixture
def sesion(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    return programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)


def test_individual_convocations_without_codes_in_listing(client, creador_headers, sesion, estudiantes):
    lista = convocatorias(client, creador_headers, sesion["id"])
    assert {c["estudiante"]["correo"] for c in lista} == {e.correo for e in estudiantes}
    assert all(c["intentos_autorizados"] == 1 and c["sesion_id"] == sesion["id"] for c in lista)
    # Ningún listado ni detalle expone el código.
    texto = client.get(f"{SESSIONS_URL}/{sesion['id']}/convocations", headers=creador_headers).text
    detalle = client.get(f"{SESSIONS_URL}/{sesion['id']}/convocations/{lista[0]['id']}", headers=creador_headers)
    assert "codigo_autorizacion" not in texto and "codigo_autorizacion" not in detalle.text
    assert "codigo_autorizacion" not in client.get(f"{SESSIONS_URL}/{sesion['id']}", headers=creador_headers).text


def test_codes_are_valid_unique_and_retrievable_only_by_owner(client, creador_headers, otro_creador_headers, admin_headers, sesion):
    lista = convocatorias(client, creador_headers, sesion["id"])
    codigos = [codigo_de(client, creador_headers, sesion["id"], c["id"]) for c in lista]
    assert all(FORMATO.match(c) for c in codigos)
    assert len(set(codigos)) == len(codigos)

    url = f"{SESSIONS_URL}/{sesion['id']}/convocations/{lista[0]['id']}/code"
    response = client.get(url, headers=creador_headers)
    assert response.headers["cache-control"] == "no-store"
    assert client.get(url, headers=otro_creador_headers).status_code == 404
    assert client.get(url, headers=admin_headers).status_code == 403
    assert client.get(url).status_code == 401


def test_code_retrieval_can_be_disabled(client, creador_headers, sesion, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "CONVOCATION_CODE_RETRIEVAL_ENABLED", False)
    convocatoria = convocatorias(client, creador_headers, sesion["id"])[0]
    response = client.get(f"{SESSIONS_URL}/{sesion['id']}/convocations/{convocatoria['id']}/code", headers=creador_headers)
    assert response.status_code == 403


def test_code_generator_uses_full_alphabet_and_is_not_predictable():
    muestras = [generar_codigo() for _ in range(2000)]
    assert len(set(muestras)) == len(muestras)
    assert all(FORMATO.match(m) for m in muestras)
    # Con 24 000 símbolos generados aparecen los 32 del alfabeto.
    assert set("".join(muestras)) == set(ALFABETO_CODIGO)


def test_add_students_and_reject_duplicates(client, creador_headers, sesion, estudiantes, make_user):
    nuevo = make_user(RolUsuario.ESTUDIANTE, clave=None)
    url = f"{SESSIONS_URL}/{sesion['id']}/convocations"
    response = client.post(url, headers=creador_headers, json={"estudiantes": [nuevo.correo]})
    assert response.status_code == 201 and response.json()[0]["estudiante"]["id"] == str(nuevo.id)

    response = client.post(url, headers=creador_headers, json={"estudiantes": [estudiantes[0].correo]})
    assert response.status_code == 409
    assert contar("convocatoria") == 4


def _insertar(conn, sesion_id, estudiante_id):
    """Inserta una convocatoria con un código cifrado válido (formato exigido desde la migración 0006)."""
    nuevo_id = uuid.uuid4()
    conn.execute(
        text("INSERT INTO convocatoria (id, sesion_id, estudiante_id, codigo_autorizacion) VALUES (:i, :s, :e, :c)"),
        {"i": nuevo_id, "s": sesion_id, "e": estudiante_id, "c": cifrar_codigo(generar_codigo(), nuevo_id)},
    )


def test_database_rejects_duplicate_convocation(sesion, estudiantes):
    with pytest.raises(IntegrityError, match="uq_convocatoria_sesion_estudiante"), engine.begin() as conn:
        _insertar(conn, sesion["id"], estudiantes[0].id)


def test_remove_student(client, creador_headers, sesion):
    lista = convocatorias(client, creador_headers, sesion["id"])
    url = f"{SESSIONS_URL}/{sesion['id']}/convocations/{lista[0]['id']}"
    assert client.delete(url, headers=creador_headers).status_code == 204
    # Revocación lógica (migración 0007): la convocatoria se conserva, marcada como revocada.
    retirada = client.get(url, headers=creador_headers).json()
    assert retirada["activa"] is False and retirada["revocada_en"] is not None
    assert len(convocatorias(client, creador_headers, sesion["id"])) == 2  # el listado muestra las activas


def test_regenerate_code_invalidates_previous(client, creador_headers, reloj, sesion, estudiantes):
    convocatoria = next(c for c in convocatorias(client, creador_headers, sesion["id"]) if c["estudiante"]["correo"] == estudiantes[0].correo)
    anterior = codigo_de(client, creador_headers, sesion["id"], convocatoria["id"])
    url = f"{SESSIONS_URL}/{sesion['id']}/convocations/{convocatoria['id']}/regenerate-code"

    assert client.post(url, headers=creador_headers, json={"motivo": "abc"}).status_code == 422  # motivo de al menos 5 caracteres
    response = client.post(url, headers=creador_headers, json={"motivo": "Código compartido por error en el chat"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    nuevo = response.json()["codigo_autorizacion"]
    assert FORMATO.match(nuevo) and nuevo != anterior
    assert codigo_de(client, creador_headers, sesion["id"], convocatoria["id"]) == nuevo

    iniciar(client, creador_headers, sesion["id"], reloj)
    assert ingresar(client, estudiantes[0].correo, anterior).status_code == 401
    assert ingresar(client, estudiantes[0].correo, nuevo).status_code == 200


def test_increase_authorized_attempts(client, creador_headers, sesion):
    convocatoria = convocatorias(client, creador_headers, sesion["id"])[0]
    url = f"{SESSIONS_URL}/{sesion['id']}/convocations/{convocatoria['id']}/attempts"

    response = client.patch(url, headers=creador_headers, json={"intentos_autorizados": 2, "motivo": "Falla de conexión del estudiante"})
    assert response.status_code == 200 and response.json()["intentos_autorizados"] == 2
    # Solo aumentar; con motivo; dentro de rango.
    assert client.patch(url, headers=creador_headers, json={"intentos_autorizados": 2, "motivo": "Otra vez lo mismo"}).status_code == 400
    assert client.patch(url, headers=creador_headers, json={"intentos_autorizados": 1, "motivo": "Reducir intentos"}).status_code == 400
    assert client.patch(url, headers=creador_headers, json={"intentos_autorizados": 3}).status_code == 422
    assert client.patch(url, headers=creador_headers, json={"intentos_autorizados": 0, "motivo": "Valor inválido"}).status_code == 422


def test_students_cannot_manage_convocations(client, estudiante_headers, admin_headers, sesion):
    convocatoria_id = uuid.uuid4()
    base = f"{SESSIONS_URL}/{sesion['id']}/convocations"
    for headers in (estudiante_headers, admin_headers):
        assert client.post(base, headers=headers, json={"estudiantes": ["x@universidad.edu.co"]}).status_code == 403
        assert client.patch(f"{base}/{convocatoria_id}/attempts", headers=headers, json={"intentos_autorizados": 5, "motivo": "Quiero más"}).status_code == 403
        assert client.delete(f"{base}/{convocatoria_id}", headers=headers).status_code == 403


def test_convocation_of_other_session_is_not_found(client, creador_headers, reloj, espacio, caso_publicado, sesion, make_user):
    otra = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, [make_user(RolUsuario.ESTUDIANTE, clave=None)])
    ajena = convocatorias(client, creador_headers, otra["id"])[0]
    assert client.get(f"{SESSIONS_URL}/{sesion['id']}/convocations/{ajena['id']}", headers=creador_headers).status_code == 404


def test_convocations_follow_session_state(client, creador_headers, reloj, sesion, make_user):
    url = f"{SESSIONS_URL}/{sesion['id']}/convocations"
    convocatoria = convocatorias(client, creador_headers, sesion["id"])[0]

    iniciar(client, creador_headers, sesion["id"], reloj)
    # EN_CURSO: se pueden convocar rezagados y gestionar intentos.
    tardio = make_user(RolUsuario.ESTUDIANTE, clave=None)
    assert client.post(url, headers=creador_headers, json={"estudiantes": [tardio.correo]}).status_code == 201

    client.post(f"{SESSIONS_URL}/{sesion['id']}/finish", headers=creador_headers)
    # FINALIZADO: nada cambia.
    otro = make_user(RolUsuario.ESTUDIANTE, clave=None)
    assert client.post(url, headers=creador_headers, json={"estudiantes": [otro.correo]}).status_code == 409
    assert client.delete(f"{url}/{convocatoria['id']}", headers=creador_headers).status_code == 409
    assert client.patch(f"{url}/{convocatoria['id']}/attempts", headers=creador_headers, json={"intentos_autorizados": 2, "motivo": "Después del cierre"}).status_code == 409
    assert client.post(f"{url}/{convocatoria['id']}/regenerate-code", headers=creador_headers, json={"motivo": "Después del cierre"}).status_code == 409


def test_database_rejects_non_student_and_plaintext_codes(sesion, docente):
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        _insertar(conn, sesion["id"], docente.id)
    assert info.value.orig.diag.constraint_name == "ck_convocatoria_estudiante_rol"
    # Desde la migración 0006 solo se admite el formato cifrado: un código en claro se rechaza.
    for codigo in (generar_codigo(), "corto", "v1", "v1.con espacios"):
        with pytest.raises(IntegrityError, match="ck_convocatoria_codigo_cifrado"), engine.begin() as conn:
            conn.execute(text("UPDATE convocatoria SET codigo_autorizacion = :c"), {"c": codigo})
