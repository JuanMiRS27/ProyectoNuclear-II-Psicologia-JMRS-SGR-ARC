import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core import security
from app.core.config import get_settings
from app.modules.auth.models import RolUsuario
from tests.conftest import DEFAULT_PASSWORD, bearer, login

LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/auth/me"
PASSWORD_URL = "/api/v1/auth/password"


# 1. Inicio de sesión correcto
@pytest.mark.parametrize("rol", [RolUsuario.ADMINISTRADOR, RolUsuario.DOCENTE])
def test_login_success(client, make_user, rol):
    usuario = make_user(rol)
    response = client.post(LOGIN_URL, json={"correo": usuario.correo, "clave": DEFAULT_PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert security.decode_access_token(body["access_token"]).user_id == usuario.id


def test_login_normalizes_email(client, docente):
    response = client.post(LOGIN_URL, json={"correo": f"  {docente.correo.upper()} ", "clave": DEFAULT_PASSWORD})
    assert response.status_code == 200


# 2. Rechazo de contraseña incorrecta (y respuesta idéntica para correo inexistente)
def test_login_wrong_password(client, docente):
    response = client.post(LOGIN_URL, json={"correo": docente.correo, "clave": "otra-clave-123"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Correo o contraseña incorrectos."}


def test_login_unknown_email_same_response(client):
    response = client.post(LOGIN_URL, json={"correo": "nadie@universidad.edu.co", "clave": "otra-clave-123"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Correo o contraseña incorrectos."}


def test_student_cannot_login_with_password(client, make_user):
    estudiante = make_user(RolUsuario.ESTUDIANTE, clave=None)
    response = client.post(LOGIN_URL, json={"correo": estudiante.correo, "clave": DEFAULT_PASSWORD})
    assert response.status_code == 401


def test_login_validation_error(client):
    response = client.post(LOGIN_URL, json={"correo": "no-es-correo", "clave": "x"})
    assert response.status_code == 422


# 3. Rechazo de cuenta inactiva
def test_login_inactive_account(client, make_user):
    usuario = make_user(RolUsuario.DOCENTE, activo=False)
    response = client.post(LOGIN_URL, json={"correo": usuario.correo, "clave": DEFAULT_PASSWORD})
    assert response.status_code == 403
    assert "access_token" not in response.json()


def test_login_inactive_account_wrong_password_does_not_reveal_status(client, make_user):
    usuario = make_user(RolUsuario.DOCENTE, activo=False)
    response = client.post(LOGIN_URL, json={"correo": usuario.correo, "clave": "otra-clave-123"})
    assert response.status_code == 401


# 4. Rechazo de token inválido, manipulado o expirado
def test_expired_token_rejected(client, docente):
    token = security.create_access_token(
        docente.id, docente.rol.value, docente.version_token, expires_delta=timedelta(seconds=-1)
    )
    response = client.get(ME_URL, headers=bearer(token))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_tampered_token_rejected(client, docente):
    token = login(client, docente.correo)
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-4]}AAAA"
    assert client.get(ME_URL, headers=bearer(tampered)).status_code == 401


def test_token_signed_with_other_key_rejected(client, docente):
    now = datetime.now(UTC)
    forged = jwt.encode(
        {"sub": str(docente.id), "rol": "ADMINISTRADOR", "ver": 0, "type": "access", "iat": now, "exp": now + timedelta(hours=1)},
        "clave-del-atacante-que-no-es-la-del-servidor",
        algorithm="HS256",
    )
    assert client.get(ME_URL, headers=bearer(forged)).status_code == 401


def test_unsigned_token_rejected(client, docente):
    now = datetime.now(UTC)
    unsigned = jwt.encode(
        {"sub": str(docente.id), "ver": 0, "type": "access", "iat": now, "exp": now + timedelta(hours=1)},
        None,
        algorithm="none",
    )
    assert client.get(ME_URL, headers=bearer(unsigned)).status_code == 401


@pytest.mark.parametrize("token", ["basura", "a.b.c", ""])
def test_malformed_token_rejected(client, token):
    assert client.get(ME_URL, headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_for_nonexistent_user_rejected(client):
    token = security.create_access_token(uuid.uuid4(), "ADMINISTRADOR", 0)
    assert client.get(ME_URL, headers=bearer(token)).status_code == 401


# 11. Restricción de endpoints protegidos
@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", ME_URL),
        ("patch", PASSWORD_URL),
        ("get", "/api/v1/users"),
        ("post", "/api/v1/users/teachers"),
        ("get", f"/api/v1/users/{uuid.uuid4()}"),
        ("patch", f"/api/v1/users/{uuid.uuid4()}"),
        ("patch", f"/api/v1/users/{uuid.uuid4()}/status"),
        ("patch", f"/api/v1/users/{uuid.uuid4()}/case-permission"),
    ],
)
def test_protected_endpoints_require_token(client, method, url):
    response = getattr(client, method)(url)
    assert response.status_code == 401


def test_me_returns_profile_without_password_hash(client, docente, docente_headers):
    response = client.get(ME_URL, headers=docente_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(docente.id)
    assert body["rol"] == "DOCENTE"
    assert "clave_hash" not in body and "clave" not in body


# 12. Cambio de contraseña
def test_change_password(client, docente, docente_headers):
    nueva = "NuevaClave#2026"
    response = client.patch(
        PASSWORD_URL, headers=docente_headers, json={"clave_actual": DEFAULT_PASSWORD, "clave_nueva": nueva}
    )
    assert response.status_code == 200

    assert client.post(LOGIN_URL, json={"correo": docente.correo, "clave": DEFAULT_PASSWORD}).status_code == 401
    assert client.post(LOGIN_URL, json={"correo": docente.correo, "clave": nueva}).status_code == 200


def test_change_password_wrong_current(client, docente_headers):
    response = client.patch(
        PASSWORD_URL, headers=docente_headers, json={"clave_actual": "incorrecta-123", "clave_nueva": "NuevaClave#2026"}
    )
    assert response.status_code == 400


def test_change_password_too_short(client, docente_headers):
    response = client.patch(
        PASSWORD_URL, headers=docente_headers, json={"clave_actual": DEFAULT_PASSWORD, "clave_nueva": "corta"}
    )
    assert response.status_code == 422


def test_change_password_must_differ(client, docente_headers):
    response = client.patch(
        PASSWORD_URL, headers=docente_headers, json={"clave_actual": DEFAULT_PASSWORD, "clave_nueva": DEFAULT_PASSWORD}
    )
    assert response.status_code == 400


# 13. JWT vigente bloqueado cuando la cuenta se desactiva
def test_valid_token_rejected_after_deactivation(client, docente, docente_headers, admin_headers):
    assert client.get(ME_URL, headers=docente_headers).status_code == 200

    response = client.patch(f"/api/v1/users/{docente.id}/status", headers=admin_headers, json={"activo": False})
    assert response.status_code == 200

    assert client.get(ME_URL, headers=docente_headers).status_code == 401


# Invalidación de tokens tras cambiar la contraseña (claim "ver" + usuario.version_token)
def test_password_change_invalidates_previous_tokens(client, docente):
    token_1 = login(client, docente.correo)
    token_2 = login(client, docente.correo)  # p. ej. otra sesión en otro dispositivo
    nueva = "NuevaClave#2026"

    response = client.patch(
        PASSWORD_URL, headers=bearer(token_1), json={"clave_actual": DEFAULT_PASSWORD, "clave_nueva": nueva}
    )
    assert response.status_code == 200

    for old_token in (token_1, token_2):
        assert client.get(ME_URL, headers=bearer(old_token)).status_code == 401

    fresh = login(client, docente.correo, nueva)
    assert client.get(ME_URL, headers=bearer(fresh)).status_code == 200


def test_failed_password_change_keeps_tokens_valid(client, docente_headers):
    response = client.patch(
        PASSWORD_URL, headers=docente_headers, json={"clave_actual": "incorrecta-123", "clave_nueva": "NuevaClave#2026"}
    )
    assert response.status_code == 400
    assert client.get(ME_URL, headers=docente_headers).status_code == 200


def test_password_change_does_not_affect_other_users(client, docente, docente_headers, admin_headers):
    client.patch(
        PASSWORD_URL, headers=docente_headers, json={"clave_actual": DEFAULT_PASSWORD, "clave_nueva": "NuevaClave#2026"}
    )
    assert client.get(ME_URL, headers=admin_headers).status_code == 200


def test_token_with_stale_version_rejected(client, db, docente):
    db.refresh(docente)
    stale = security.create_access_token(docente.id, docente.rol.value, docente.version_token - 1)
    current = security.create_access_token(docente.id, docente.rol.value, docente.version_token)
    assert client.get(ME_URL, headers=bearer(stale)).status_code == 401
    assert client.get(ME_URL, headers=bearer(current)).status_code == 200


@pytest.mark.parametrize("version", [None, "0", True, 1.0])
def test_token_without_valid_version_claim_rejected(client, docente, version):
    now = datetime.now(UTC)
    payload = {"sub": str(docente.id), "type": "access", "iat": now, "exp": now + timedelta(hours=1)}
    if version is not None:
        payload["ver"] = version
    settings = get_settings()
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    assert client.get(ME_URL, headers=bearer(token)).status_code == 401
