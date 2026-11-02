"""Módulo 1: usuarios, autenticación, tokens, permisos y espacios académicos (pruebas unitarias).

Los espacios académicos viven en `app.modules.sessions`, pero funcionalmente pertenecen al
catálogo institucional del módulo 1; por eso se prueban aquí.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
import pytest
from argon2 import PasswordHasher
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.core import security
from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.modules.auth.dependencies.auth import get_current_user, require_case_creator, require_roles
from app.modules.auth.models import RolUsuario
from app.modules.auth.schemas import (
    DocenteCreate,
    EstudianteCreate,
    LoginRequest,
    PasswordChangeRequest,
    UsuarioRead,
    UsuarioStatusUpdate,
    UsuarioUpdate,
)
from app.modules.auth.schemas.usuario import normalize_email
from app.modules.auth.services import AuthService, UsuarioService
from app.modules.auth.services.auth_service import INVALID_CREDENTIALS_MESSAGE, INVALID_TOKEN_MESSAGE
from app.modules.sessions.models import EspacioAcademico
from app.modules.sessions.schemas import EspacioCreate, EspacioUpdate
from app.modules.sessions.services import EspacioService
from app.scripts import create_admin
from tests.unit.fabricas import CLAVE, error_integridad, hash_clave, sesion_bd, usuario

CORREO = "ana@universidad.edu.co"


# --- Contraseñas (Argon2) -------------------------------------------------------------------


def test_hash_no_contiene_la_clave_y_usa_sal_aleatoria():
    primero, segundo = security.hash_password(CLAVE), security.hash_password(CLAVE)

    assert CLAVE not in primero
    assert primero.startswith("$argon2")
    assert primero != segundo


@pytest.mark.parametrize(
    ("clave", "hash_guardado", "esperado"),
    [
        (CLAVE, "valido", True),
        ("Otra#Clave2026", "valido", False),
        ("", "valido", False),
        (CLAVE, None, False),
        (CLAVE, "no-es-un-hash", False),
    ],
)
def test_verificacion_de_contrasena(clave, hash_guardado, esperado):
    guardado = hash_clave() if hash_guardado == "valido" else hash_guardado

    assert security.verify_password(clave, guardado) is esperado


def test_hash_con_parametros_antiguos_requiere_rehash():
    debil = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(CLAVE)

    assert security.password_needs_rehash(debil) is True
    assert security.password_needs_rehash(hash_clave()) is False


# --- Tokens JWT -----------------------------------------------------------------------------


def test_token_de_acceso_conserva_usuario_y_version():
    usuario_id = uuid.uuid4()

    claims = security.decode_access_token(security.create_access_token(usuario_id, "DOCENTE", 7))

    assert (claims.user_id, claims.token_version) == (usuario_id, 7)


def test_token_incluye_tipo_y_expiracion_configurada():
    token = security.create_access_token(uuid.uuid4(), "DOCENTE", 0)

    payload = jwt.decode(token, options={"verify_signature": False})

    assert payload["type"] == "access"
    assert payload["exp"] - payload["iat"] == get_settings().ACCESS_TOKEN_EXPIRE_MINUTES * 60


def _token_crudo(**cambios) -> str:
    settings = get_settings()
    base = {"sub": str(uuid.uuid4()), "ver": 0, "type": "access", "iat": 1, "exp": 4_000_000_000}
    payload = {k: v for k, v in (base | cambios).items() if v is not ...}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


@pytest.mark.parametrize(
    "cambios",
    [
        {"type": "student"},
        {"type": ...},
        {"sub": ...},
        {"ver": ...},
        {"exp": ...},
        {"iat": ...},
        {"sub": "no-es-uuid"},
        {"ver": "0"},
        {"ver": True},
        {"ver": 1.5},
        {"exp": 2},
    ],
    ids=lambda c: "-".join(f"{k}={v!r}" for k, v in c.items()),
)
def test_token_invalido_se_rechaza(cambios):
    with pytest.raises(security.InvalidTokenError):
        security.decode_access_token(_token_crudo(**cambios))


def test_token_expirado_se_rechaza():
    token = security.create_access_token(uuid.uuid4(), "DOCENTE", 0, timedelta(seconds=-1))

    with pytest.raises(security.InvalidTokenError):
        security.decode_access_token(token)


def test_token_firmado_con_otra_clave_o_algoritmo_none_se_rechaza():
    payload = {"sub": str(uuid.uuid4()), "ver": 0, "type": "access", "iat": 1, "exp": 4_000_000_000}
    otra_clave = jwt.encode(payload, "x" * 48, algorithm="HS256")
    sin_firma = jwt.encode(payload, "", algorithm="none")

    for token in (otra_clave, sin_firma, "basura", ""):
        with pytest.raises(security.InvalidTokenError):
            security.decode_access_token(token)


@pytest.mark.parametrize("valor", [0, 3, -1])
def test_entero_estricto_acepta_enteros(valor):
    assert security.entero_estricto(valor) == valor


@pytest.mark.parametrize("valor", [True, False, "1", 1.0, None, [1]])
def test_entero_estricto_rechaza_lo_demas(valor):
    with pytest.raises(security.InvalidTokenError):
        security.entero_estricto(valor)


# --- Esquemas de entrada --------------------------------------------------------------------


def _docente(**cambios) -> dict:
    return {"nombre_completo": "Ana Pérez", "correo": CORREO, "clave": CLAVE} | cambios


def test_normalizacion_de_correo():
    assert normalize_email("  ANA@Universidad.EDU.co ") == CORREO
    assert DocenteCreate(**_docente(correo="ANA@Universidad.edu.co")).correo == CORREO


@pytest.mark.parametrize(
    ("campo", "valor", "acepta"),
    [
        ("clave", "x" * 7, False),
        ("clave", "x" * 8, True),
        ("clave", "x" * 128, True),
        ("clave", "x" * 129, False),
        ("nombre_completo", "   ", False),
        ("nombre_completo", "x" * 150, True),
        ("nombre_completo", "x" * 151, False),
        ("correo", "sin-arroba", False),
        ("correo", "a@b", False),
        ("correo", "a" * 64 + "@" + "b" * 40 + "." + "c" * 41 + ".co", True),  # 150 caracteres
        ("correo", "a" * 64 + "@" + "b" * 40 + "." + "c" * 42 + ".co", False),  # 151 caracteres
        ("codigo_institucional", "A.b_1-2", True),
        ("codigo_institucional", "x" * 30, True),
        ("codigo_institucional", "x" * 31, False),
        ("codigo_institucional", "con espacio", False),
        ("codigo_institucional", "ñandú", False),
    ],
)
def test_limites_de_registro_de_docente(campo, valor, acepta):
    if acepta:
        assert getattr(DocenteCreate(**_docente(**{campo: valor})), campo) == valor
    else:
        with pytest.raises(ValidationError):
            DocenteCreate(**_docente(**{campo: valor}))


def test_nombre_se_guarda_sin_espacios_externos():
    assert DocenteCreate(**_docente(nombre_completo="  Ana  ")).nombre_completo == "Ana"


@pytest.mark.parametrize("extra", ["rol", "activo", "puede_crear_casos", "clave_hash"])
def test_registro_no_acepta_campos_privilegiados(extra):
    with pytest.raises(ValidationError):
        DocenteCreate(**_docente(**{extra: True}))
    with pytest.raises(ValidationError):
        UsuarioUpdate(**{extra: True})


def test_estudiante_no_tiene_campo_de_contrasena():
    with pytest.raises(ValidationError):
        EstudianteCreate(nombre_completo="Eva", correo=CORREO, clave=CLAVE)


@pytest.mark.parametrize("clave", ["", "x" * 129])
def test_login_limita_longitud_de_clave(clave):
    with pytest.raises(ValidationError):
        LoginRequest(correo=CORREO, clave=clave)


def test_login_admite_clave_corta_y_normaliza_correo():
    assert LoginRequest(correo="ANA@universidad.edu.co", clave="x").correo == CORREO


def test_cambio_de_clave_exige_longitud_minima_solo_a_la_nueva():
    assert PasswordChangeRequest(clave_actual="x", clave_nueva="y" * 8)
    with pytest.raises(ValidationError):
        PasswordChangeRequest(clave_actual="x" * 8, clave_nueva="y" * 7)


def test_estado_de_usuario_es_booleano_estricto_ante_texto_invalido():
    with pytest.raises(ValidationError):
        UsuarioStatusUpdate(activo="tal vez")


def test_representacion_publica_nunca_incluye_hash():
    assert "clave_hash" not in UsuarioRead.model_fields
    assert "version_token" not in UsuarioRead.model_fields


@pytest.mark.parametrize(
    ("rol", "es_admin", "es_docente"),
    [
        (RolUsuario.ADMINISTRADOR, True, False),
        (RolUsuario.DOCENTE, False, True),
        (RolUsuario.ESTUDIANTE, False, False),
    ],
)
def test_propiedades_de_rol(rol, es_admin, es_docente):
    u = usuario(rol)

    assert (u.es_administrador, u.es_docente) == (es_admin, es_docente)


# --- AuthService ----------------------------------------------------------------------------


@pytest.fixture
def auth():
    servicio = AuthService(sesion_bd())
    servicio._usuarios = MagicMock()
    return servicio


def test_login_correcto_devuelve_token_del_usuario(auth):
    docente = usuario(clave_hash=hash_clave(), version_token=2)
    auth._usuarios.get_by_correo.return_value = docente

    respuesta = auth.login(CORREO, CLAVE)

    claims = security.decode_access_token(respuesta.access_token)
    assert (claims.user_id, claims.token_version) == (docente.id, 2)
    assert respuesta.expires_in == get_settings().ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert respuesta.token_type == "bearer"


def test_login_busca_el_correo_normalizado(auth):
    auth._usuarios.get_by_correo.return_value = None

    with pytest.raises(AuthenticationError):
        auth.login("  ANA@universidad.edu.co ", CLAVE)

    auth._usuarios.get_by_correo.assert_called_once_with(CORREO)


@pytest.mark.parametrize(
    "cuenta",
    [
        None,
        {"rol": RolUsuario.DOCENTE, "clave": "OtraClave#2026"},
        {"rol": RolUsuario.ESTUDIANTE, "clave": CLAVE},
    ],
    ids=["correo-inexistente", "clave-incorrecta", "estudiante-con-clave"],
)
def test_login_fallido_usa_siempre_el_mismo_mensaje(auth, cuenta):
    auth._usuarios.get_by_correo.return_value = (
        usuario(cuenta["rol"], clave_hash=hash_clave(cuenta["clave"])) if cuenta else None
    )

    with pytest.raises(AuthenticationError) as error:
        auth.login(CORREO, CLAVE)

    assert error.value.detail == INVALID_CREDENTIALS_MESSAGE


def test_login_de_cuenta_inactiva_solo_se_revela_con_clave_correcta(auth):
    auth._usuarios.get_by_correo.return_value = usuario(clave_hash=hash_clave(), activo=False)

    with pytest.raises(PermissionDeniedError):
        auth.login(CORREO, CLAVE)
    with pytest.raises(AuthenticationError):
        auth.login(CORREO, "OtraClave#2026")


def test_login_actualiza_hash_antiguo_y_confirma(auth):
    debil = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(CLAVE)
    docente = usuario(clave_hash=debil)
    auth._usuarios.get_by_correo.return_value = docente

    auth.login(CORREO, CLAVE)

    assert docente.clave_hash != debil
    assert security.verify_password(CLAVE, docente.clave_hash)
    auth._session.commit.assert_called_once()


def test_login_no_escribe_si_el_hash_esta_vigente(auth):
    auth._usuarios.get_by_correo.return_value = usuario(clave_hash=hash_clave())

    auth.login(CORREO, CLAVE)

    auth._session.commit.assert_not_called()


def test_token_resuelve_el_usuario_vigente(auth):
    docente = usuario(version_token=1)
    auth._usuarios.get_by_id.return_value = docente

    assert auth.get_user_from_token(security.create_access_token(docente.id, "DOCENTE", 1)) is docente


@pytest.mark.parametrize(
    "estado",
    [None, {"activo": False}, {"version_token": 2}],
    ids=["usuario-borrado", "cuenta-inactiva", "version-antigua"],
)
def test_token_deja_de_valer_si_cambia_el_usuario(auth, estado):
    docente = usuario(version_token=1)
    auth._usuarios.get_by_id.return_value = None
    if estado is not None:
        auth._usuarios.get_by_id.return_value = usuario(id=docente.id, **({"version_token": 1} | estado))

    with pytest.raises(AuthenticationError) as error:
        auth.get_user_from_token(security.create_access_token(docente.id, "DOCENTE", 1))

    assert error.value.detail == INVALID_TOKEN_MESSAGE


def test_token_malformado_se_traduce_a_error_de_autenticacion(auth):
    with pytest.raises(AuthenticationError):
        auth.get_user_from_token("basura")
    auth._usuarios.get_by_id.assert_not_called()


@pytest.mark.parametrize(
    ("actual", "nueva"),
    [("Incorrecta#1", "NuevaClave#2026"), (CLAVE, CLAVE)],
    ids=["clave-actual-incorrecta", "misma-clave"],
)
def test_cambio_de_clave_rechazado_no_escribe(auth, actual, nueva):
    docente = usuario(clave_hash=hash_clave())
    original = docente.clave_hash

    with pytest.raises(BusinessRuleError):
        auth.change_password(docente, actual, nueva)

    assert docente.clave_hash == original
    auth._session.commit.assert_not_called()


def test_cambio_de_clave_reemplaza_hash_e_incrementa_version(auth):
    docente = usuario(clave_hash=hash_clave())

    auth.change_password(docente, CLAVE, "NuevaClave#2026")

    assert security.verify_password("NuevaClave#2026", docente.clave_hash)
    assert "version_token" in str(docente.version_token)  # incremento atómico en SQL, no en Python
    auth._session.commit.assert_called_once()


# --- UsuarioService -------------------------------------------------------------------------


@pytest.fixture
def usuarios():
    servicio = UsuarioService(sesion_bd())
    servicio._usuarios = MagicMock()
    servicio._usuarios.exists_by_correo.return_value = False
    servicio._usuarios.exists_by_codigo_institucional.return_value = False
    return servicio


def test_crear_docente_fija_rol_y_guarda_hash(usuarios):
    creado = usuarios.create_docente(DocenteCreate(**_docente(codigo_institucional="D-1")))

    assert creado.rol == RolUsuario.DOCENTE
    assert creado.puede_crear_casos is False
    assert creado.activo is True
    assert creado.clave_hash != CLAVE and security.verify_password(CLAVE, creado.clave_hash)
    usuarios._usuarios.add.assert_called_once_with(creado)
    usuarios._session.commit.assert_called_once()


def test_crear_estudiante_queda_sin_contrasena(usuarios):
    creado = usuarios.create_estudiante(EstudianteCreate(nombre_completo="Eva", correo=CORREO))

    assert (creado.rol, creado.clave_hash) == (RolUsuario.ESTUDIANTE, None)


def test_crear_administrador_solo_por_operacion_interna(usuarios):
    creado = usuarios.create_admin(nombre_completo="Root", correo=CORREO, clave=CLAVE)

    assert creado.rol == RolUsuario.ADMINISTRADOR


@pytest.mark.parametrize(
    ("repetido", "mensaje"), [("exists_by_correo", "correo"), ("exists_by_codigo_institucional", "código institucional")]
)
def test_duplicados_se_detectan_antes_de_insertar(usuarios, repetido, mensaje):
    getattr(usuarios._usuarios, repetido).return_value = True

    with pytest.raises(ConflictError, match=mensaje):
        usuarios.create_docente(DocenteCreate(**_docente(codigo_institucional="D-1")))

    usuarios._usuarios.add.assert_not_called()


@pytest.mark.parametrize(
    ("restriccion", "tipo"),
    [
        ("uq_usuario_correo", ConflictError),
        ("uq_usuario_codigo_institucional", ConflictError),
        ("ck_usuario_correo_normalizado", BusinessRuleError),
        (None, BusinessRuleError),
    ],
)
def test_violacion_de_integridad_se_traduce_y_revierte(usuarios, restriccion, tipo):
    usuarios._usuarios.add.side_effect = error_integridad(restriccion)

    with pytest.raises(tipo) as error:
        usuarios.create_docente(DocenteCreate(**_docente()))

    assert "ck_usuario" not in error.value.detail  # no se exponen detalles de la base
    usuarios._session.rollback.assert_called_once()
    usuarios._session.commit.assert_not_called()


def test_error_inesperado_revierte_y_se_propaga(usuarios):
    usuarios._usuarios.add.side_effect = RuntimeError("fallo")

    with pytest.raises(RuntimeError):
        usuarios.create_docente(DocenteCreate(**_docente()))

    usuarios._session.rollback.assert_called_once()


def test_consultar_usuario_inexistente(usuarios):
    usuarios._usuarios.get_by_id.return_value = None

    with pytest.raises(NotFoundError):
        usuarios.get(uuid.uuid4())


def test_administrador_no_modifica_a_otro_administrador(usuarios):
    usuarios._usuarios.get_by_id.return_value = usuario(RolUsuario.ADMINISTRADOR)

    with pytest.raises(PermissionDeniedError):
        usuarios.update(usuario(RolUsuario.ADMINISTRADOR), uuid.uuid4(), UsuarioUpdate(nombre_completo="Otro"))


def test_administrador_modifica_sus_propios_datos(usuarios):
    admin = usuario(RolUsuario.ADMINISTRADOR)
    usuarios._usuarios.get_by_id.return_value = admin

    usuarios.update(admin, admin.id, UsuarioUpdate(nombre_completo="Nuevo"))

    assert admin.nombre_completo == "Nuevo"


@pytest.mark.parametrize("cambios", [{"nombre_completo": None}, {"correo": None}])
def test_actualizar_rechaza_vaciar_datos_obligatorios(usuarios, cambios):
    usuarios._usuarios.get_by_id.return_value = usuario()

    with pytest.raises(BusinessRuleError):
        usuarios.update(usuario(RolUsuario.ADMINISTRADOR), uuid.uuid4(), UsuarioUpdate(**cambios))


def test_actualizar_excluye_al_propio_usuario_de_la_unicidad(usuarios):
    objetivo = usuario()
    usuarios._usuarios.get_by_id.return_value = objetivo

    usuarios.update(usuario(RolUsuario.ADMINISTRADOR), objetivo.id, UsuarioUpdate(correo=CORREO, codigo_institucional="C-1"))

    usuarios._usuarios.exists_by_correo.assert_called_once_with(CORREO, objetivo.id)
    usuarios._usuarios.exists_by_codigo_institucional.assert_called_once_with("C-1", objetivo.id)


def test_actualizar_permite_retirar_el_codigo_institucional(usuarios):
    objetivo = usuario(codigo_institucional="C-1")
    usuarios._usuarios.get_by_id.return_value = objetivo

    usuarios.update(usuario(RolUsuario.ADMINISTRADOR), objetivo.id, UsuarioUpdate(codigo_institucional=None))

    assert objetivo.codigo_institucional is None


def test_actualizar_solo_cambia_los_campos_enviados(usuarios):
    objetivo = usuario(nombre_completo="Original", codigo_institucional="C-1")
    usuarios._usuarios.get_by_id.return_value = objetivo

    usuarios.update(usuario(RolUsuario.ADMINISTRADOR), objetivo.id, UsuarioUpdate(correo="nuevo@u.edu.co"))

    assert (objetivo.nombre_completo, objetivo.codigo_institucional) == ("Original", "C-1")


def test_cambiar_al_mismo_estado_no_abre_transaccion(usuarios):
    objetivo = usuario(activo=True)
    usuarios._usuarios.get_by_id.return_value = objetivo

    assert usuarios.set_status(usuario(RolUsuario.ADMINISTRADOR), objetivo.id, True) is objetivo
    usuarios._session.commit.assert_not_called()


def test_nadie_se_desactiva_a_si_mismo(usuarios):
    admin = usuario(RolUsuario.ADMINISTRADOR)
    usuarios._usuarios.get_by_id.return_value = admin

    with pytest.raises(BusinessRuleError, match="propia"):
        usuarios.set_status(admin, admin.id, False)


def test_no_se_desactiva_al_ultimo_administrador(usuarios):
    objetivo = usuario(RolUsuario.ADMINISTRADOR)
    usuarios._usuarios.get_by_id.return_value = objetivo
    usuarios._usuarios.count_active_admins.return_value = 1

    with pytest.raises(BusinessRuleError, match="al menos un administrador"):
        usuarios.set_status(usuario(RolUsuario.ADMINISTRADOR), objetivo.id, False)

    assert objetivo.activo is True
    usuarios._usuarios.lock_active_admins.assert_called_once()
    usuarios._session.rollback.assert_called_once()


def test_desactivar_administrador_con_otros_activos(usuarios):
    objetivo = usuario(RolUsuario.ADMINISTRADOR)
    usuarios._usuarios.get_by_id.return_value = objetivo
    usuarios._usuarios.count_active_admins.return_value = 2

    usuarios.set_status(usuario(RolUsuario.ADMINISTRADOR), objetivo.id, False)

    assert objetivo.activo is False


@pytest.mark.parametrize(("rol", "activo"), [(RolUsuario.DOCENTE, False), (RolUsuario.ADMINISTRADOR, True)])
def test_cambios_de_estado_que_no_reducen_administradores_no_bloquean(usuarios, rol, activo):
    objetivo = usuario(rol, activo=not activo)
    usuarios._usuarios.get_by_id.return_value = objetivo

    usuarios.set_status(usuario(RolUsuario.ADMINISTRADOR), objetivo.id, activo)

    assert objetivo.activo is activo
    usuarios._usuarios.lock_active_admins.assert_not_called()


@pytest.mark.parametrize("rol", [RolUsuario.ADMINISTRADOR, RolUsuario.ESTUDIANTE])
def test_permiso_de_casos_solo_para_docentes(usuarios, rol):
    usuarios._usuarios.get_by_id.return_value = usuario(rol)

    with pytest.raises(BusinessRuleError):
        usuarios.set_case_permission(uuid.uuid4(), True)


@pytest.mark.parametrize("valor", [True, False])
def test_conceder_o_revocar_permiso_de_casos(usuarios, valor):
    docente = usuario(puede_crear_casos=not valor)
    usuarios._usuarios.get_by_id.return_value = docente

    assert usuarios.set_case_permission(docente.id, valor).puede_crear_casos is valor


# --- Dependencias de autorización -----------------------------------------------------------


@pytest.mark.parametrize(
    "credenciales",
    [None, HTTPAuthorizationCredentials(scheme="Basic", credentials="x")],
    ids=["sin-cabecera", "esquema-basic"],
)
def test_usuario_actual_exige_bearer(credenciales):
    servicio = MagicMock()

    with pytest.raises(AuthenticationError):
        get_current_user(credenciales, servicio)

    servicio.get_user_from_token.assert_not_called()


def test_usuario_actual_delega_el_token_en_el_servicio():
    servicio = MagicMock()

    resultado = get_current_user(HTTPAuthorizationCredentials(scheme="bearer", credentials="tok"), servicio)

    servicio.get_user_from_token.assert_called_once_with("tok")
    assert resultado is servicio.get_user_from_token.return_value


@pytest.mark.parametrize("rol", list(RolUsuario))
def test_require_roles_solo_admite_los_indicados(rol):
    permitidos = (RolUsuario.ADMINISTRADOR, RolUsuario.DOCENTE)
    dependencia = require_roles(*permitidos)
    actor = usuario(rol)

    if rol in permitidos:
        assert dependencia(actor) is actor
    else:
        with pytest.raises(PermissionDeniedError):
            dependencia(actor)


def test_crear_casos_exige_el_permiso_explicito():
    with pytest.raises(PermissionDeniedError, match="crear casos"):
        require_case_creator(usuario(puede_crear_casos=False))

    autorizado = usuario(puede_crear_casos=True)
    assert require_case_creator(autorizado) is autorizado


# --- Script de creación del administrador inicial -------------------------------------------


ARGUMENTOS = ["--nombre", "Admin Inicial", "--correo", "ADMIN@universidad.edu.co"]


@pytest.fixture
def script(monkeypatch):
    """Sustituye la sesión y el servicio: el script no debe tocar la base en estas pruebas."""
    monkeypatch.delenv(create_admin.PASSWORD_ENV_VAR, raising=False)
    servicio = MagicMock()
    servicio.create_admin.return_value = SimpleNamespace(correo="admin@universidad.edu.co", id=uuid.uuid4())
    with patch.object(create_admin, "SessionLocal", MagicMock()), patch.object(create_admin, "UsuarioService", return_value=servicio):
        yield servicio


def test_script_lee_la_clave_del_entorno_y_normaliza(script, monkeypatch, capsys):
    monkeypatch.setenv(create_admin.PASSWORD_ENV_VAR, CLAVE)

    assert create_admin.main(ARGUMENTOS) == 0

    datos = script.create_admin.call_args.kwargs
    assert datos["correo"] == "admin@universidad.edu.co"
    assert datos["clave"] == CLAVE
    salida = capsys.readouterr().out
    assert "Administrador creado" in salida and CLAVE not in salida


def test_script_no_acepta_la_clave_como_argumento(script):
    with pytest.raises(SystemExit):
        create_admin.main([*ARGUMENTOS, "--clave", CLAVE])


def test_script_sin_terminal_ni_variable_termina_con_error(script, monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))

    with pytest.raises(SystemExit, match=create_admin.PASSWORD_ENV_VAR):
        create_admin.main(ARGUMENTOS)
    script.create_admin.assert_not_called()


def test_script_interactivo_confirma_la_clave(script, monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(create_admin.getpass, "getpass", lambda _: CLAVE)

    assert create_admin.main(ARGUMENTOS) == 0


def test_script_interactivo_rechaza_claves_distintas(script, monkeypatch):
    respuestas = iter([CLAVE, "Distinta#2026"])
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(create_admin.getpass, "getpass", lambda _: next(respuestas))

    with pytest.raises(SystemExit, match="no coinciden"):
        create_admin.main(ARGUMENTOS)


def test_script_informa_datos_invalidos(script, monkeypatch, capsys):
    monkeypatch.setenv(create_admin.PASSWORD_ENV_VAR, "corta")

    assert create_admin.main(ARGUMENTOS) == 1

    assert "clave" in capsys.readouterr().err
    script.create_admin.assert_not_called()


def test_script_informa_errores_de_negocio(script, monkeypatch, capsys):
    monkeypatch.setenv(create_admin.PASSWORD_ENV_VAR, CLAVE)
    script.create_admin.side_effect = ConflictError("Ya existe.")

    assert create_admin.main(ARGUMENTOS) == 1

    assert "Ya existe." in capsys.readouterr().err


# --- Espacios académicos --------------------------------------------------------------------


@pytest.fixture
def espacios():
    servicio = EspacioService(sesion_bd())
    servicio._espacios = MagicMock()
    servicio._espacios.exists_nombre_periodo.return_value = False
    return servicio


def _espacio(activo: bool = True) -> EspacioAcademico:
    return EspacioAcademico(id=uuid.uuid4(), nombre="Psicología Social", periodo="2026-2", activo=activo)


@pytest.mark.parametrize(
    ("datos", "acepta"),
    [
        ({"nombre": "Social", "periodo": "2026-2"}, True),
        ({"nombre": "x" * 150, "periodo": "x" * 10}, True),
        ({"nombre": "x" * 151, "periodo": "2026-2"}, False),
        ({"nombre": "   ", "periodo": "2026-2"}, False),
        ({"nombre": "Social", "periodo": "x" * 11}, False),
        ({"nombre": "Social", "periodo": "2026 2"}, False),
        ({"nombre": "Social", "periodo": ""}, False),
        ({"nombre": "Social", "periodo": "2026-2", "activo": True}, False),
    ],
)
def test_validacion_de_espacio(datos, acepta):
    if acepta:
        assert EspacioCreate(**datos).nombre == datos["nombre"]
    else:
        with pytest.raises(ValidationError):
            EspacioCreate(**datos)


def test_crear_espacio_duplicado_no_inserta(espacios):
    espacios._espacios.exists_nombre_periodo.return_value = True

    with pytest.raises(ConflictError):
        espacios.crear(EspacioCreate(nombre="Social", periodo="2026-2"))

    espacios._espacios.add.assert_not_called()


def test_crear_espacio_queda_activo(espacios):
    creado = espacios.crear(EspacioCreate(nombre="Social", periodo="2026-2"))

    assert creado.activo is True
    espacios._espacios.add.assert_called_once_with(creado)


def test_indice_unico_de_espacio_se_traduce_a_conflicto(espacios):
    espacios._espacios.add.side_effect = error_integridad("uq_espacio_academico_nombre_periodo")

    with pytest.raises(ConflictError):
        espacios.crear(EspacioCreate(nombre="Social", periodo="2026-2"))


@pytest.mark.parametrize(
    ("rol", "filtro", "esperado"),
    [
        (RolUsuario.DOCENTE, False, True),
        (RolUsuario.DOCENTE, None, True),
        (RolUsuario.ADMINISTRADOR, False, False),
        (RolUsuario.ADMINISTRADOR, None, None),
    ],
)
def test_docente_solo_lista_espacios_activos(espacios, rol, filtro, esperado):
    espacios.listar(usuario(rol), activo=filtro, skip=0, limit=10)

    espacios._espacios.list.assert_called_once_with(activo=esperado, skip=0, limit=10)


def test_espacio_inactivo_es_invisible_para_docente_pero_no_para_admin(espacios):
    inactivo = _espacio(activo=False)
    espacios._espacios.get.return_value = inactivo

    assert espacios.obtener(usuario(RolUsuario.ADMINISTRADOR), inactivo.id) is inactivo
    with pytest.raises(NotFoundError):
        espacios.obtener(usuario(RolUsuario.DOCENTE), inactivo.id)


def test_espacio_inexistente(espacios):
    espacios._espacios.get.return_value = None

    with pytest.raises(NotFoundError):
        espacios.obtener(usuario(RolUsuario.ADMINISTRADOR), uuid.uuid4())
    with pytest.raises(NotFoundError):
        espacios.cambiar_estado(uuid.uuid4(), False)


@pytest.mark.parametrize("cambios", [{"nombre": None}, {"periodo": None}])
def test_actualizar_espacio_no_permite_vaciar_campos(espacios, cambios):
    with pytest.raises(BusinessRuleError):
        espacios.actualizar(uuid.uuid4(), EspacioUpdate(**cambios))


def test_actualizar_espacio_compara_el_par_resultante_excluyendose(espacios):
    espacio = _espacio()
    espacios._espacios.get.return_value = espacio

    espacios.actualizar(espacio.id, EspacioUpdate(periodo="2027-1"))

    espacios._espacios.exists_nombre_periodo.assert_called_once_with("Psicología Social", "2027-1", exclude_id=espacio.id)
    assert (espacio.nombre, espacio.periodo) == ("Psicología Social", "2027-1")


def test_actualizar_espacio_a_un_par_ocupado(espacios):
    espacios._espacios.get.return_value = _espacio()
    espacios._espacios.exists_nombre_periodo.return_value = True

    with pytest.raises(ConflictError):
        espacios.actualizar(uuid.uuid4(), EspacioUpdate(nombre="Otro"))


def test_cambiar_estado_de_espacio(espacios):
    espacio = _espacio()
    espacios._espacios.get.return_value = espacio

    assert espacios.cambiar_estado(espacio.id, False).activo is False
    espacios._session.commit.assert_called_once()
