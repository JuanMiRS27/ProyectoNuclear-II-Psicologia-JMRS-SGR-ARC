"""Espacios académicos y alta administrativa de estudiantes (extensión del módulo 1)."""

import uuid

import pytest

from tests.sessions_helpers import SPACES_URL

STUDENTS_URL = "/api/v1/users/students"


# --- Espacios académicos ----------------------------------------------------------------


def test_admin_creates_space(client, admin_headers):
    response = client.post(SPACES_URL, headers=admin_headers, json={"nombre": " Psicología Social ", "periodo": "2026-2"})
    assert response.status_code == 201
    body = response.json()
    assert body["nombre"] == "Psicología Social" and body["periodo"] == "2026-2" and body["activo"] is True


@pytest.mark.parametrize(
    "duplicado",
    [
        {"nombre": "Psicología Social", "periodo": "2026-2"},
        {"nombre": "psicología social", "periodo": "2026-2"},  # sin distinguir mayúsculas
    ],
)
def test_duplicate_name_and_period_rejected(client, admin_headers, duplicado):
    assert client.post(SPACES_URL, headers=admin_headers, json={"nombre": "Psicología Social", "periodo": "2026-2"}).status_code == 201
    response = client.post(SPACES_URL, headers=admin_headers, json=duplicado)
    assert response.status_code == 409
    # El mismo nombre en otro periodo sí se permite.
    assert client.post(SPACES_URL, headers=admin_headers, json={"nombre": "Psicología Social", "periodo": "2027-1"}).status_code == 201


def test_update_space_and_duplicate_on_update(client, admin_headers):
    a = client.post(SPACES_URL, headers=admin_headers, json={"nombre": "A", "periodo": "2026-2"}).json()
    client.post(SPACES_URL, headers=admin_headers, json={"nombre": "B", "periodo": "2026-2"})
    assert client.patch(f"{SPACES_URL}/{a['id']}", headers=admin_headers, json={"nombre": "B"}).status_code == 409
    response = client.patch(f"{SPACES_URL}/{a['id']}", headers=admin_headers, json={"nombre": "A renombrado"})
    assert response.status_code == 200 and response.json()["nombre"] == "A renombrado"
    assert client.patch(f"{SPACES_URL}/{a['id']}", headers=admin_headers, json={"periodo": None}).status_code == 400


def test_activate_and_deactivate_space(client, admin_headers, docente_headers):
    espacio = client.post(SPACES_URL, headers=admin_headers, json={"nombre": "Temporal", "periodo": "2026-2"}).json()
    url = f"{SPACES_URL}/{espacio['id']}"

    assert client.patch(f"{url}/status", headers=admin_headers, json={"activo": False}).json()["activo"] is False
    # El docente solo ve espacios activos.
    assert client.get(SPACES_URL, headers=docente_headers).json()["total"] == 0
    assert client.get(url, headers=docente_headers).status_code == 404
    # El administrador ve todos y puede filtrar.
    assert client.get(SPACES_URL, headers=admin_headers, params={"activo": False}).json()["total"] == 1

    assert client.patch(f"{url}/status", headers=admin_headers, json={"activo": True}).json()["activo"] is True
    assert client.get(SPACES_URL, headers=docente_headers).json()["total"] == 1
    assert client.get(url, headers=docente_headers).status_code == 200


def test_space_permissions(client, docente_headers, estudiante_headers, admin_headers):
    espacio = client.post(SPACES_URL, headers=admin_headers, json={"nombre": "X", "periodo": "2026-2"}).json()
    for headers in (docente_headers, estudiante_headers):
        assert client.post(SPACES_URL, headers=headers, json={"nombre": "Y", "periodo": "2026-2"}).status_code == 403
        assert client.patch(f"{SPACES_URL}/{espacio['id']}", headers=headers, json={"nombre": "Z"}).status_code == 403
        assert client.patch(f"{SPACES_URL}/{espacio['id']}/status", headers=headers, json={"activo": False}).status_code == 403
    assert client.get(SPACES_URL, headers=estudiante_headers).status_code == 403
    assert client.get(SPACES_URL).status_code == 401


@pytest.mark.parametrize(
    "datos", [{"nombre": "", "periodo": "2026-2"}, {"nombre": "X", "periodo": "2026-2-extra"}, {"nombre": "X", "periodo": "20 26"}]
)
def test_space_validation(client, admin_headers, datos):
    assert client.post(SPACES_URL, headers=admin_headers, json=datos).status_code == 422


def test_space_not_found(client, admin_headers):
    assert client.get(f"{SPACES_URL}/{uuid.uuid4()}", headers=admin_headers).status_code == 404


# --- Alta de estudiantes (módulo 1) ------------------------------------------------------


def test_admin_creates_student_without_password(client, admin_headers, db):
    response = client.post(
        STUDENTS_URL,
        headers=admin_headers,
        json={"nombre_completo": "Laura Gómez", "correo": "Laura.Gomez@Universidad.edu.co", "codigo_institucional": "EST-1"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["rol"] == "ESTUDIANTE" and body["correo"] == "laura.gomez@universidad.edu.co"
    assert body["puede_crear_casos"] is False and "clave_hash" not in body

    from app.modules.auth.models import Usuario

    assert db.get(Usuario, uuid.UUID(body["id"])).clave_hash is None


def test_student_creation_rejects_password_and_duplicates(client, admin_headers, docente):
    base = {"nombre_completo": "Laura Gómez", "correo": "laura@universidad.edu.co"}
    assert client.post(STUDENTS_URL, headers=admin_headers, json=base | {"clave": "NoDebe#2026"}).status_code == 422
    assert client.post(STUDENTS_URL, headers=admin_headers, json=base).status_code == 201
    assert client.post(STUDENTS_URL, headers=admin_headers, json=base).status_code == 409
    assert client.post(STUDENTS_URL, headers=admin_headers, json=base | {"correo": docente.correo}).status_code == 409


def test_only_admin_creates_students(client, docente_headers, estudiante_headers):
    datos = {"nombre_completo": "X", "correo": "x@universidad.edu.co"}
    assert client.post(STUDENTS_URL, json=datos).status_code == 401
    assert client.post(STUDENTS_URL, headers=docente_headers, json=datos).status_code == 403
    assert client.post(STUDENTS_URL, headers=estudiante_headers, json=datos).status_code == 403


def test_created_student_cannot_use_password_login(client, admin_headers):
    client.post(STUDENTS_URL, headers=admin_headers, json={"nombre_completo": "X", "correo": "x@universidad.edu.co"})
    response = client.post("/api/v1/auth/login", json={"correo": "x@universidad.edu.co", "clave": "cualquiera"})
    assert response.status_code == 401
