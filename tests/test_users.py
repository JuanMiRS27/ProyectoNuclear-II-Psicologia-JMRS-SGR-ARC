import uuid

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import register_exception_handlers
from app.modules.auth.dependencies import CaseCreator
from app.modules.auth.models import RolUsuario
from tests.conftest import DEFAULT_PASSWORD, bearer, login

USERS_URL = "/api/v1/users"
TEACHERS_URL = "/api/v1/users/teachers"


def teacher_payload(**overrides):
    return {
        "nombre_completo": "Ana María Pérez",
        "correo": "ana.perez@universidad.edu.co",
        "clave": "ClaveInicial#2026",
        "codigo_institucional": "DOC-001",
    } | overrides


# 5. Creación de docente por un administrador
def test_admin_creates_teacher(client, admin_headers):
    response = client.post(TEACHERS_URL, headers=admin_headers, json=teacher_payload(correo="  Ana.Perez@Universidad.EDU.co "))

    assert response.status_code == 201
    body = response.json()
    assert body["rol"] == "DOCENTE"
    assert body["correo"] == "ana.perez@universidad.edu.co"
    assert body["activo"] is True
    assert body["puede_crear_casos"] is False
    assert "clave_hash" not in body and "clave" not in body

    # El docente creado puede iniciar sesión con la contraseña inicial.
    login(client, "ana.perez@universidad.edu.co", "ClaveInicial#2026")


def test_create_teacher_cannot_set_role_or_permission(client, admin_headers):
    response = client.post(
        TEACHERS_URL, headers=admin_headers, json=teacher_payload(rol="ADMINISTRADOR", puede_crear_casos=True)
    )
    assert response.status_code == 422


def test_create_teacher_validation(client, admin_headers):
    assert client.post(TEACHERS_URL, headers=admin_headers, json=teacher_payload(clave="corta")).status_code == 422
    assert client.post(TEACHERS_URL, headers=admin_headers, json=teacher_payload(correo="x")).status_code == 422
    assert client.post(TEACHERS_URL, headers=admin_headers, json=teacher_payload(nombre_completo=" ")).status_code == 422


# 6. Prohibición de crear docentes desde una cuenta docente
def test_teacher_cannot_create_teacher(client, docente_headers):
    response = client.post(TEACHERS_URL, headers=docente_headers, json=teacher_payload())
    assert response.status_code == 403


# 7. Rechazo de correos duplicados
def test_duplicate_email_rejected(client, admin_headers):
    assert client.post(TEACHERS_URL, headers=admin_headers, json=teacher_payload()).status_code == 201
    response = client.post(
        TEACHERS_URL,
        headers=admin_headers,
        json=teacher_payload(correo="ANA.PEREZ@universidad.edu.co", codigo_institucional="DOC-002"),
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "Ya existe un usuario registrado con ese correo."}


def test_duplicate_institutional_code_rejected(client, admin_headers):
    assert client.post(TEACHERS_URL, headers=admin_headers, json=teacher_payload()).status_code == 201
    response = client.post(TEACHERS_URL, headers=admin_headers, json=teacher_payload(correo="otro@universidad.edu.co"))
    assert response.status_code == 409


def test_update_to_duplicate_email_rejected(client, admin_headers, make_user):
    a = make_user(RolUsuario.DOCENTE)
    b = make_user(RolUsuario.DOCENTE)
    response = client.patch(f"{USERS_URL}/{b.id}", headers=admin_headers, json={"correo": a.correo})
    assert response.status_code == 409


# Consulta
def test_list_users_with_filters(client, admin_headers, make_user):
    make_user(RolUsuario.DOCENTE)
    make_user(RolUsuario.DOCENTE, activo=False)

    body = client.get(USERS_URL, headers=admin_headers).json()
    assert body["total"] == 3
    assert all("clave_hash" not in u for u in body["items"])

    docentes = client.get(USERS_URL, headers=admin_headers, params={"rol": "DOCENTE", "activo": True}).json()
    assert docentes["total"] == 1

    paged = client.get(USERS_URL, headers=admin_headers, params={"limit": 1, "skip": 1}).json()
    assert len(paged["items"]) == 1 and paged["total"] == 3


def test_teacher_cannot_list_users(client, docente_headers):
    assert client.get(USERS_URL, headers=docente_headers).status_code == 403


def test_get_user(client, admin_headers, docente):
    response = client.get(f"{USERS_URL}/{docente.id}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["correo"] == docente.correo


def test_get_user_not_found(client, admin_headers):
    assert client.get(f"{USERS_URL}/{uuid.uuid4()}", headers=admin_headers).status_code == 404


def test_get_user_invalid_id(client, admin_headers):
    assert client.get(f"{USERS_URL}/no-es-uuid", headers=admin_headers).status_code == 422


# Actualización de datos personales
def test_update_user_personal_data(client, admin_headers, docente):
    response = client.patch(
        f"{USERS_URL}/{docente.id}",
        headers=admin_headers,
        json={"nombre_completo": "Nombre Actualizado", "correo": "Nuevo@Universidad.edu.co", "codigo_institucional": "D-9"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["nombre_completo"] == "Nombre Actualizado"
    assert body["correo"] == "nuevo@universidad.edu.co"
    assert body["codigo_institucional"] == "D-9"


@pytest.mark.parametrize("field", ["rol", "clave_hash", "activo", "puede_crear_casos", "clave"])
def test_update_rejects_non_personal_fields(client, admin_headers, docente, field):
    response = client.patch(f"{USERS_URL}/{docente.id}", headers=admin_headers, json={field: "ADMINISTRADOR"})
    assert response.status_code == 422


def test_update_rejects_null_required_fields(client, admin_headers, docente):
    response = client.patch(f"{USERS_URL}/{docente.id}", headers=admin_headers, json={"correo": None})
    assert response.status_code == 400


def test_admin_cannot_modify_other_admin(client, admin_headers, make_user):
    otro_admin = make_user(RolUsuario.ADMINISTRADOR)
    response = client.patch(f"{USERS_URL}/{otro_admin.id}", headers=admin_headers, json={"correo": "x@universidad.edu.co"})
    assert response.status_code == 403


# 8. Activación y desactivación de cuentas
def test_deactivate_and_reactivate_account(client, admin_headers, docente):
    url = f"{USERS_URL}/{docente.id}/status"

    response = client.patch(url, headers=admin_headers, json={"activo": False})
    assert response.status_code == 200 and response.json()["activo"] is False
    assert client.post("/api/v1/auth/login", json={"correo": docente.correo, "clave": DEFAULT_PASSWORD}).status_code == 403

    response = client.patch(url, headers=admin_headers, json={"activo": True})
    assert response.status_code == 200 and response.json()["activo"] is True
    login(client, docente.correo)


def test_admin_cannot_deactivate_self(client, admin, admin_headers):
    response = client.patch(f"{USERS_URL}/{admin.id}/status", headers=admin_headers, json={"activo": False})
    assert response.status_code == 400


def test_admin_can_deactivate_other_admin_keeping_one_active(client, admin_headers, make_user):
    otro_admin = make_user(RolUsuario.ADMINISTRADOR)
    response = client.patch(f"{USERS_URL}/{otro_admin.id}/status", headers=admin_headers, json={"activo": False})
    assert response.status_code == 200


def test_teacher_cannot_change_status(client, docente_headers, make_user):
    otro = make_user(RolUsuario.DOCENTE)
    response = client.patch(f"{USERS_URL}/{otro.id}/status", headers=docente_headers, json={"activo": False})
    assert response.status_code == 403


def test_status_not_found(client, admin_headers):
    response = client.patch(f"{USERS_URL}/{uuid.uuid4()}/status", headers=admin_headers, json={"activo": False})
    assert response.status_code == 404


# 9. Asignación y revocación del permiso de creación de casos
def test_grant_and_revoke_case_permission(client, admin_headers, docente):
    url = f"{USERS_URL}/{docente.id}/case-permission"

    response = client.patch(url, headers=admin_headers, json={"puede_crear_casos": True})
    assert response.status_code == 200 and response.json()["puede_crear_casos"] is True

    response = client.patch(url, headers=admin_headers, json={"puede_crear_casos": False})
    assert response.status_code == 200 and response.json()["puede_crear_casos"] is False


# 10. Prohibición de conceder el permiso a roles diferentes de DOCENTE
@pytest.mark.parametrize("rol", [RolUsuario.ADMINISTRADOR, RolUsuario.ESTUDIANTE])
def test_case_permission_only_for_teachers(client, admin_headers, make_user, rol):
    usuario = make_user(rol, clave=None if rol == RolUsuario.ESTUDIANTE else DEFAULT_PASSWORD)
    response = client.patch(
        f"{USERS_URL}/{usuario.id}/case-permission", headers=admin_headers, json={"puede_crear_casos": True}
    )
    assert response.status_code == 400


def test_teacher_cannot_grant_self_permission(client, docente, docente_headers):
    response = client.patch(
        f"{USERS_URL}/{docente.id}/case-permission", headers=docente_headers, json={"puede_crear_casos": True}
    )
    assert response.status_code == 403


# Dependencia reutilizable para el módulo 2
@pytest.fixture
def case_creator_client() -> TestClient:
    router = APIRouter()

    @router.get("/protected-case-endpoint")
    def protected(docente: CaseCreator) -> dict[str, str]:
        return {"docente": str(docente.id)}

    probe = FastAPI()
    register_exception_handlers(probe)
    probe.include_router(router)
    return TestClient(probe)


def test_require_case_creator_dependency(case_creator_client, client, make_user, admin):
    con_permiso = make_user(RolUsuario.DOCENTE, puede_crear_casos=True)
    sin_permiso = make_user(RolUsuario.DOCENTE)

    ok = case_creator_client.get("/protected-case-endpoint", headers=bearer(login(client, con_permiso.correo)))
    assert ok.status_code == 200 and ok.json() == {"docente": str(con_permiso.id)}

    for usuario in (sin_permiso, admin):
        response = case_creator_client.get("/protected-case-endpoint", headers=bearer(login(client, usuario.correo)))
        assert response.status_code == 403


def test_revoked_permission_takes_effect_with_existing_token(case_creator_client, client, admin_headers, make_user):
    docente = make_user(RolUsuario.DOCENTE, puede_crear_casos=True)
    headers = bearer(login(client, docente.correo))
    assert case_creator_client.get("/protected-case-endpoint", headers=headers).status_code == 200

    client.patch(f"{USERS_URL}/{docente.id}/case-permission", headers=admin_headers, json={"puede_crear_casos": False})
    assert case_creator_client.get("/protected-case-endpoint", headers=headers).status_code == 403
