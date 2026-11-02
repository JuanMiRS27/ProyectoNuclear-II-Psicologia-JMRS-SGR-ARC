"""Módulo 1 por HTTP: contrato de la API, roles, persistencia y espacios académicos (PostgreSQL real)."""

import uuid

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import main as modulo_main
from app.core import security
from app.core.database import SessionLocal, engine
from app.main import app, create_app
from app.modules.auth.models import RolUsuario
from app.scripts import create_admin
from tests.cases_helpers import contar_filas, dsl_valido
from tests.conftest import DEFAULT_PASSWORD, bearer, login
from tests.sessions_helpers import SPACES_URL

API = "/api/v1"
USERS_URL = f"{API}/users"
NUEVA_CLAVE = "OtraClave#2026"


def _docente(correo="nuevo@universidad.edu.co", **extra) -> dict:
    return {"nombre_completo": "Docente Nuevo", "correo": correo, "clave": DEFAULT_PASSWORD} | extra


def _fila_usuario(usuario_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT * FROM usuario WHERE id = :id"), {"id": usuario_id}).one()


# --- Sistema y contrato de errores --------------------------------------------------------------


def test_health_no_requiere_autenticacion(client):
    respuesta = client.get("/health")

    assert (respuesta.status_code, respuesta.json()) == (200, {"status": "ok"})


def test_openapi_publica_los_cuatro_modulos(client):
    rutas = client.get("/openapi.json").json()["paths"]

    for ruta in ("/auth/login", "/cases", "/sessions", "/student/participations"):
        assert f"{API}{ruta}" in rutas


def test_openapi_oculta_en_produccion(monkeypatch):
    produccion = type("S", (), {"APP_NAME": "x", "API_V1_PREFIX": API, "docs_enabled": False})
    monkeypatch.setattr(modulo_main, "get_settings", lambda: produccion)

    with TestClient(create_app()) as cliente:
        assert cliente.get("/openapi.json").status_code == 404
        assert cliente.get("/docs").status_code == 404


def test_error_de_autenticacion_tiene_formato_y_cabecera_estandar(client):
    respuesta = client.get(f"{API}/auth/me")

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == "Bearer"
    assert list(respuesta.json()) == ["detail"]


def test_error_de_validacion_senala_el_campo(client):
    respuesta = client.post(f"{API}/auth/login", json={"correo": "no-es-correo", "clave": "x"})

    assert respuesta.status_code == 422
    assert respuesta.json()["detail"][0]["loc"] == ["body", "correo"]


@pytest.mark.parametrize("cuerpo", ["{no-es-json", "[]", '"texto"'], ids=["json-roto", "lista", "cadena"])
def test_cuerpo_mal_formado(client, cuerpo):
    respuesta = client.post(f"{API}/auth/login", content=cuerpo, headers={"Content-Type": "application/json"})

    assert respuesta.status_code == 422


def test_metodo_no_permitido(client):
    assert client.delete(f"{API}/auth/login").status_code == 405


# --- Matriz de roles --------------------------------------------------------------------------


RUTAS_ADMIN = [
    ("get", "/users"),
    ("post", "/users/teachers"),
    ("post", "/users/students"),
    ("get", "/users/{id}"),
    ("patch", "/users/{id}"),
    ("patch", "/users/{id}/status"),
    ("patch", "/users/{id}/case-permission"),
]


@pytest.mark.parametrize(("metodo", "ruta"), RUTAS_ADMIN)
@pytest.mark.parametrize("actor", ["anonimo", "docente", "estudiante"])
def test_gestion_de_usuarios_solo_para_administradores(client, docente_headers, estudiante_headers, metodo, ruta, actor):
    cabeceras = {"anonimo": {}, "docente": docente_headers, "estudiante": estudiante_headers}[actor]

    respuesta = client.request(metodo, API + ruta.format(id=uuid.uuid4()), headers=cabeceras, json={})

    assert respuesta.status_code == (401 if actor == "anonimo" else 403)


def test_esquema_basic_no_autentica(client, admin):
    respuesta = client.get(USERS_URL, headers={"Authorization": f"Basic {admin.correo}"})

    assert respuesta.status_code == 401


def test_estudiante_no_consulta_perfil_de_contrasena(client, estudiante_headers):
    assert client.get(f"{API}/auth/me", headers=estudiante_headers).status_code == 403


# --- Persistencia y ciclo de vida de cuentas ----------------------------------------------------


def test_alta_de_docente_guarda_hash_y_devuelve_vista_publica(client, admin_headers):
    respuesta = client.post(f"{USERS_URL}/teachers", headers=admin_headers, json=_docente(correo="NUEVO@Universidad.edu.co"))

    cuerpo = respuesta.json()
    assert respuesta.status_code == 201
    assert "clave" not in str(cuerpo)
    fila = _fila_usuario(cuerpo["id"])
    assert fila.correo == "nuevo@universidad.edu.co"
    assert fila.clave_hash.startswith("$argon2") and DEFAULT_PASSWORD not in fila.clave_hash
    assert (fila.rol, fila.puede_crear_casos, fila.activo, fila.version_token) == ("DOCENTE", False, True, 0)


def test_docente_creado_puede_iniciar_sesion_y_ver_su_perfil(client, admin_headers):
    client.post(f"{USERS_URL}/teachers", headers=admin_headers, json=_docente())

    perfil = client.get(f"{API}/auth/me", headers=bearer(login(client, "nuevo@universidad.edu.co")))

    assert perfil.status_code == 200
    assert perfil.json()["rol"] == "DOCENTE"


def test_correo_duplicado_con_otra_capitalizacion(client, admin_headers, docente):
    respuesta = client.post(f"{USERS_URL}/teachers", headers=admin_headers, json=_docente(correo=docente.correo.upper()))

    assert respuesta.status_code == 409


def test_actualizar_correo_normaliza_y_permite_el_propio(client, admin_headers, docente):
    url = f"{USERS_URL}/{docente.id}"

    assert client.patch(url, headers=admin_headers, json={"correo": docente.correo}).status_code == 200
    respuesta = client.patch(url, headers=admin_headers, json={"correo": "CAMBIADO@universidad.edu.co"})

    assert respuesta.json()["correo"] == "cambiado@universidad.edu.co"
    assert _fila_usuario(docente.id).correo == "cambiado@universidad.edu.co"
    assert login(client, "cambiado@universidad.edu.co")


def test_retirar_codigo_institucional_lo_libera(client, admin_headers, make_user):
    a, b = make_user(RolUsuario.DOCENTE), make_user(RolUsuario.DOCENTE)
    client.patch(f"{USERS_URL}/{a.id}", headers=admin_headers, json={"codigo_institucional": "D-1"})
    assert client.patch(f"{USERS_URL}/{b.id}", headers=admin_headers, json={"codigo_institucional": "D-1"}).status_code == 409

    client.patch(f"{USERS_URL}/{a.id}", headers=admin_headers, json={"codigo_institucional": None})

    assert client.patch(f"{USERS_URL}/{b.id}", headers=admin_headers, json={"codigo_institucional": "D-1"}).status_code == 200


def test_cambio_de_contrasena_de_extremo_a_extremo(client, docente, docente_headers):
    respuesta = client.patch(
        f"{API}/auth/password", headers=docente_headers, json={"clave_actual": DEFAULT_PASSWORD, "clave_nueva": NUEVA_CLAVE}
    )

    assert respuesta.status_code == 200
    assert client.post(f"{API}/auth/login", json={"correo": docente.correo, "clave": DEFAULT_PASSWORD}).status_code == 401
    assert login(client, docente.correo, NUEVA_CLAVE)
    assert _fila_usuario(docente.id).version_token == 1


def test_desactivar_y_reactivar_restablece_el_acceso(client, admin_headers, docente, docente_headers):
    url = f"{USERS_URL}/{docente.id}/status"

    client.patch(url, headers=admin_headers, json={"activo": False})
    assert client.get(f"{API}/auth/me", headers=docente_headers).status_code == 401
    assert client.post(f"{API}/auth/login", json={"correo": docente.correo, "clave": DEFAULT_PASSWORD}).status_code == 403

    client.patch(url, headers=admin_headers, json={"activo": True})
    assert client.get(f"{API}/auth/me", headers=docente_headers).status_code == 200  # el token sigue siendo de su versión


def test_permiso_de_casos_se_aplica_de_inmediato_en_el_modulo_2(client, admin_headers, docente, docente_headers):
    url = f"{USERS_URL}/{docente.id}/case-permission"
    assert client.post(f"{API}/cases", headers=docente_headers, json={"nombre": "Caso"}).status_code == 403

    assert client.patch(url, headers=admin_headers, json={"puede_crear_casos": True}).json()["puede_crear_casos"] is True

    assert client.post(f"{API}/cases", headers=docente_headers, json={"nombre": "Caso"}).status_code == 201


# --- Paginación ---------------------------------------------------------------------------------


def test_paginacion_de_usuarios_es_estable(client, admin_headers, make_user):
    for _ in range(4):
        make_user(RolUsuario.ESTUDIANTE, clave=None)

    primera = client.get(USERS_URL, headers=admin_headers, params={"rol": "ESTUDIANTE", "limit": 3}).json()
    segunda = client.get(USERS_URL, headers=admin_headers, params={"rol": "ESTUDIANTE", "limit": 3, "skip": 3}).json()

    assert (primera["total"], len(primera["items"]), len(segunda["items"])) == (4, 3, 1)
    assert not {u["id"] for u in primera["items"]} & {u["id"] for u in segunda["items"]}


@pytest.mark.parametrize("parametros", [{"limit": 0}, {"limit": 101}, {"skip": -1}, {"rol": "OTRO"}, {"activo": "quizas"}])
def test_parametros_de_consulta_invalidos(client, admin_headers, parametros):
    assert client.get(USERS_URL, headers=admin_headers, params=parametros).status_code == 422


@pytest.mark.parametrize(
    ("ruta", "actor"),
    [("/users", "admin"), ("/academic-spaces", "admin"), ("/cases", "creador"), ("/sessions", "creador")],
)
def test_desplazamiento_fuera_de_rango_es_error_de_validacion(client, admin_headers, creador_headers, ruta, actor):
    # Regresión DEF-02: un `skip` que no cabe en bigint llegaba a PostgreSQL y terminaba en HTTP 500.
    cabeceras = admin_headers if actor == "admin" else creador_headers

    assert client.get(API + ruta, headers=cabeceras, params={"skip": 2**63}).status_code == 422
    assert client.get(API + ruta, headers=cabeceras, params={"skip": 2**63 - 1}).status_code == 200


@pytest.fixture
def cliente_sin_excepciones():
    with TestClient(app, raise_server_exceptions=False) as cliente:
        yield cliente


def _dsl_con_nul_en_nivel() -> dict:
    documento = dsl_valido()
    documento["rubrica"]["criterios"][0]["niveles"][0]["nombre"] = "a\x00b"
    return documento


@pytest.mark.parametrize(
    ("ruta", "actor", "cuerpo"),
    [
        ("/users/students", "admin", {"nombre_completo": "a\x00b", "correo": "nul@universidad.edu.co"}),
        ("/academic-spaces", "admin", {"nombre": "a\x00b", "periodo": "2026-1"}),
        ("/cases", "creador", {"nombre": "a\x00b"}),
        ("/cases/full", "creador", _dsl_con_nul_en_nivel()),
    ],
    ids=["usuario", "espacio", "caso", "nivel-jsonb"],
)
def test_texto_con_byte_nul_es_error_de_validacion(cliente_sin_excepciones, admin_headers, creador_headers, ruta, actor, cuerpo):
    # Regresión DEF-03: PostgreSQL rechaza el byte NUL y la petición terminaba en HTTP 500.
    cabeceras = admin_headers if actor == "admin" else creador_headers
    filas_antes = contar_filas()

    respuesta = cliente_sin_excepciones.post(API + ruta, headers=cabeceras, json=cuerpo)

    assert respuesta.status_code == 422
    assert list(respuesta.json()) == ["detail"]
    assert "a\x00b" not in respuesta.text  # la respuesta no repite el valor ni detalles de la consulta
    assert contar_filas() == filas_antes


def test_la_conexion_sigue_usable_tras_rechazar_un_byte_nul(cliente_sin_excepciones, admin_headers):
    cuerpo = {"nombre": "a\x00b", "periodo": "2026-1"}
    assert cliente_sin_excepciones.post(f"{API}/academic-spaces", headers=admin_headers, json=cuerpo).status_code == 422

    respuesta = cliente_sin_excepciones.post(f"{API}/academic-spaces", headers=admin_headers, json=cuerpo | {"nombre": "Válido"})

    assert respuesta.status_code == 201


# --- Script de administrador contra la base real ------------------------------------------------


def test_script_crea_un_administrador_que_puede_iniciar_sesion(client, monkeypatch):
    monkeypatch.setenv(create_admin.PASSWORD_ENV_VAR, DEFAULT_PASSWORD)

    assert create_admin.main(["--nombre", "Raíz", "--correo", "RAIZ@universidad.edu.co", "--codigo", "ADM-1"]) == 0

    perfil = client.get(f"{API}/auth/me", headers=bearer(login(client, "raiz@universidad.edu.co")))
    assert (perfil.json()["rol"], perfil.json()["codigo_institucional"]) == ("ADMINISTRADOR", "ADM-1")


def test_script_no_duplica_ni_modifica_un_correo_existente(admin, monkeypatch, capsys):
    monkeypatch.setenv(create_admin.PASSWORD_ENV_VAR, "ClaveDistinta#2026")
    original = _fila_usuario(admin.id).clave_hash

    assert create_admin.main(["--nombre", "Otro", "--correo", admin.correo]) == 1

    assert "Ya existe" in capsys.readouterr().err
    assert _fila_usuario(admin.id).clave_hash == original


def test_hash_antiguo_se_actualiza_en_el_login(client, docente):
    debil = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(DEFAULT_PASSWORD)
    with SessionLocal() as sesion:
        sesion.execute(text("UPDATE usuario SET clave_hash = :h WHERE id = :id"), {"h": debil, "id": docente.id})
        sesion.commit()

    assert login(client, docente.correo)

    assert not security.password_needs_rehash(_fila_usuario(docente.id).clave_hash)


# --- Espacios académicos ------------------------------------------------------------------------


def _crear_espacio(client, headers, nombre="Psicología Social", periodo="2026-2"):
    respuesta = client.post(SPACES_URL, headers=headers, json={"nombre": nombre, "periodo": periodo})
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def test_docente_solo_ve_espacios_activos(client, admin_headers, docente_headers):
    activo = _crear_espacio(client, admin_headers)
    inactivo = _crear_espacio(client, admin_headers, nombre="Cerrado")
    client.patch(f"{SPACES_URL}/{inactivo['id']}/status", headers=admin_headers, json={"activo": False})

    lista = client.get(SPACES_URL, headers=docente_headers, params={"activo": False}).json()

    assert [e["id"] for e in lista["items"]] == [activo["id"]]
    assert client.get(f"{SPACES_URL}/{inactivo['id']}", headers=docente_headers).status_code == 404
    assert client.get(f"{SPACES_URL}/{inactivo['id']}", headers=admin_headers).status_code == 200


def test_administrador_filtra_y_pagina_espacios(client, admin_headers):
    _crear_espacio(client, admin_headers, nombre="Uno", periodo="2026-1")
    segundo = _crear_espacio(client, admin_headers, nombre="Dos", periodo="2026-2")
    client.patch(f"{SPACES_URL}/{segundo['id']}/status", headers=admin_headers, json={"activo": False})

    inactivos = client.get(SPACES_URL, headers=admin_headers, params={"activo": False}).json()
    pagina = client.get(SPACES_URL, headers=admin_headers, params={"limit": 1}).json()

    assert [e["nombre"] for e in inactivos["items"]] == ["Dos"]
    assert (pagina["total"], len(pagina["items"]), pagina["items"][0]["periodo"]) == (2, 1, "2026-2")


def test_unicidad_de_espacio_ignora_mayusculas_y_usa_el_indice_real(client, admin_headers):
    _crear_espacio(client, admin_headers)

    respuesta = client.post(SPACES_URL, headers=admin_headers, json={"nombre": "PSICOLOGÍA SOCIAL", "periodo": "2026-2"})

    assert respuesta.status_code == 409


def test_vaciar_datos_del_espacio_se_rechaza(client, admin_headers):
    espacio = _crear_espacio(client, admin_headers)

    assert client.patch(f"{SPACES_URL}/{espacio['id']}", headers=admin_headers, json={"nombre": None}).status_code == 400


@pytest.mark.parametrize("actor", ["anonimo", "estudiante"])
def test_lectura_de_espacios_exige_rol_institucional(client, estudiante_headers, actor):
    cabeceras = {} if actor == "anonimo" else estudiante_headers

    assert client.get(SPACES_URL, headers=cabeceras).status_code == (401 if actor == "anonimo" else 403)


def test_docente_no_crea_espacios(client, docente_headers):
    assert client.post(SPACES_URL, headers=docente_headers, json={"nombre": "X", "periodo": "2026-2"}).status_code == 403
