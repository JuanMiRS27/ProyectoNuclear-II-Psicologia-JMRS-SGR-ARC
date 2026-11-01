"""Última revisión del módulo 3: revocación lógica, unicidad atómica de códigos y reingreso (módulo 4)."""

import ast
import threading
import uuid
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.main import app
from app.modules.sessions.dependencies import get_politica_reingreso
from app.modules.sessions.repositories import ConvocatoriaRepository
from app.modules.sessions.security import descifrar_codigo
from app.modules.sessions.services import AccesoEstudiantilService, ConvocatoriaService, sesion_service
from app.modules.sessions.states import IngresoNoPermitidoError
from tests.conftest import PROJECT_ROOT, alembic_config, bearer
from tests.sessions_helpers import SESSIONS_URL, codigo_de, convocatorias, iniciar, ingresar, programar


@pytest.fixture
def en_curso(client, creador_headers, reloj, espacio, caso_publicado, estudiantes):
    sesion = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, estudiantes)
    lista = {c["estudiante"]["correo"]: c for c in convocatorias(client, creador_headers, sesion["id"])}
    codigos = {correo: codigo_de(client, creador_headers, sesion["id"], c["id"]) for correo, c in lista.items()}
    assert iniciar(client, creador_headers, sesion["id"], reloj).status_code == 200
    return {"sesion": sesion, "convocatorias": lista, "codigos": codigos, "estudiantes": estudiantes}


def _url(en_curso, correo, sufijo=""):
    return f"{SESSIONS_URL}/{en_curso['sesion']['id']}/convocations/{en_curso['convocatorias'][correo]['id']}{sufijo}"


# --- 1. Revocación lógica -----------------------------------------------------------------------


def test_revocation_keeps_the_row_and_blocks_access(client, creador_headers, en_curso):
    estudiante = en_curso["estudiantes"][0]
    conv_id = en_curso["convocatorias"][estudiante.correo]["id"]
    token = ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo]).json()["access_token"]

    assert client.delete(_url(en_curso, estudiante.correo), headers=creador_headers).status_code == 204
    with engine.connect() as conn:
        fila = conn.execute(text("SELECT revocada_en FROM convocatoria WHERE id = :id"), {"id": conv_id}).one()
    assert fila.revocada_en is not None  # la convocatoria sigue existiendo

    assert client.get(f"/api/v1/student/sessions/{en_curso['sesion']['id']}", headers=bearer(token)).status_code == 401
    assert ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo]).status_code == 403


def test_revoked_convocations_in_listings_and_counts(client, creador_headers, en_curso):
    estudiante = en_curso["estudiantes"][0]
    client.delete(_url(en_curso, estudiante.correo), headers=creador_headers)
    base = f"{SESSIONS_URL}/{en_curso['sesion']['id']}"

    activas = client.get(f"{base}/convocations", headers=creador_headers).json()
    todas = client.get(f"{base}/convocations", headers=creador_headers, params={"incluir_revocadas": True}).json()
    assert len(activas) == 2 and len(todas) == 3
    assert [c["activa"] for c in todas if c["estudiante"]["correo"] == estudiante.correo] == [False]
    assert client.get(base, headers=creador_headers).json()["total_convocatorias"] == 2


def test_operations_on_revoked_convocation_are_rejected(client, creador_headers, en_curso):
    correo = en_curso["estudiantes"][0].correo
    client.delete(_url(en_curso, correo), headers=creador_headers)
    for metodo, sufijo, cuerpo in (
        ("delete", "", None),
        ("patch", "/attempts", {"intentos_autorizados": 2, "motivo": "Intento sobre revocada"}),
        ("post", "/regenerate-code", {"motivo": "Intento sobre revocada"}),
        ("get", "/code", None),
    ):
        kwargs = {"headers": creador_headers} | ({"json": cuerpo} if cuerpo else {})
        response = getattr(client, metodo)(_url(en_curso, correo, sufijo), **kwargs)
        assert response.status_code == 409 and "revocada" in response.json()["detail"], (metodo, sufijo)


def test_reconvocating_reactivates_same_convocation_with_new_code(client, creador_headers, en_curso):
    estudiante = en_curso["estudiantes"][0]
    original = en_curso["convocatorias"][estudiante.correo]
    codigo_anterior = en_curso["codigos"][estudiante.correo]
    client.patch(_url(en_curso, estudiante.correo, "/attempts"), headers=creador_headers, json={"intentos_autorizados": 2, "motivo": "Antes de revocar"})
    client.delete(_url(en_curso, estudiante.correo), headers=creador_headers)

    response = client.post(f"{SESSIONS_URL}/{en_curso['sesion']['id']}/convocations", headers=creador_headers, json={"estudiantes": [estudiante.correo]})
    assert response.status_code == 201
    reactivada = response.json()[0]
    assert reactivada["id"] == original["id"] and reactivada["activa"] is True and reactivada["revocada_en"] is None
    assert reactivada["intentos_autorizados"] == 2  # el historial de la convocatoria se conserva

    nuevo = codigo_de(client, creador_headers, en_curso["sesion"]["id"], original["id"])
    assert nuevo != codigo_anterior
    assert ingresar(client, estudiante.correo, codigo_anterior).status_code == 401
    assert ingresar(client, estudiante.correo, nuevo).status_code == 200


def test_revocation_is_compatible_with_restrict_foreign_keys(client, creador_headers, en_curso):
    """Simula la futura tabla `participacion` (FK con RESTRICT): revocar funciona y la referencia se mantiene."""
    estudiante = en_curso["estudiantes"][0]
    conv_id = en_curso["convocatorias"][estudiante.correo]["id"]
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE participacion_simulada (id serial PRIMARY KEY, "
            "convocatoria_id uuid NOT NULL REFERENCES convocatoria(id) ON DELETE RESTRICT)"
        ))
        conn.execute(text("INSERT INTO participacion_simulada (convocatoria_id) VALUES (:c)"), {"c": conv_id})
    try:
        assert client.delete(_url(en_curso, estudiante.correo), headers=creador_headers).status_code == 204
        with engine.connect() as conn:
            referencia = conn.execute(
                text("SELECT c.revocada_en FROM participacion_simulada p JOIN convocatoria c ON c.id = p.convocatoria_id")
            ).one()
        assert referencia.revocada_en is not None
        # El borrado físico (comportamiento anterior) habría violado la FK.
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("DELETE FROM convocatoria WHERE id = :id"), {"id": conv_id})
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE participacion_simulada"))


def test_migration_0007_downgrade_removes_revoked_and_keeps_active(client, creador_headers, en_curso):
    revocado, activo = en_curso["estudiantes"][:2]
    client.delete(_url(en_curso, revocado.correo), headers=creador_headers)
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "0006_cifrado_codigos")
    try:
        with engine.connect() as conn:
            ids = set(conn.execute(text("SELECT id::text FROM convocatoria")).scalars())
        assert en_curso["convocatorias"][revocado.correo]["id"] not in ids  # en 0006 retirar = no existir
        assert en_curso["convocatorias"][activo.correo]["id"] in ids
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_migration_0007_is_self_contained():
    arbol = ast.parse((PROJECT_ROOT / "alembic" / "versions" / "0007_revocacion_logica_convocatoria.py").read_text(encoding="utf-8"))
    importados = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module} | {
        a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any(m == "app" or m.startswith("app.") for m in importados)


# --- 2. Unicidad atómica de códigos por estudiante -------------------------------------------------


def _hilos(*tareas):
    resultados = [None] * len(tareas)

    def ejecutar(i, tarea):
        try:
            resultados[i] = tarea()
        except Exception as exc:  # noqa: BLE001
            resultados[i] = exc

    hilos = [threading.Thread(target=ejecutar, args=(i, t)) for i, t in enumerate(tareas)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=30)
        assert not h.is_alive()
    return resultados


@pytest.fixture
def dos_sesiones(client, creador_headers, reloj, espacio, caso_publicado, make_user):
    from app.modules.auth.models import RolUsuario

    estudiante = make_user(RolUsuario.ESTUDIANTE, clave=None)
    a = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    b = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id)
    return estudiante, uuid.UUID(a["id"]), uuid.UUID(b["id"])


@pytest.fixture
def generador_colisionador(monkeypatch):
    """Cada hilo genera primero el MISMO código; después, códigos aleatorios."""
    original = sesion_service.generar_codigo
    local = threading.local()

    def generar():
        if not getattr(local, "usado", False):
            local.usado = True
            return "XXXXXXXXXXXX"
        return original()

    monkeypatch.setattr(sesion_service, "generar_codigo", generar)


def _codigos_del_estudiante(estudiante_id) -> list[str]:
    with engine.connect() as conn:
        filas = conn.execute(
            text("SELECT id, codigo_autorizacion FROM convocatoria WHERE estudiante_id = :e"), {"e": estudiante_id}
        ).all()
    return [descifrar_codigo(cifrado, convocatoria_id) for convocatoria_id, cifrado in filas]


def _convocar_en(creador, reloj, sesion_id, correo):
    def tarea():
        with SessionLocal() as s:
            return ConvocatoriaService(s, reloj).agregar(creador, sesion_id, [correo])

    return tarea


def test_concurrent_convocations_of_same_student_get_distinct_codes(
    creador, reloj, dos_sesiones, generador_colisionador, monkeypatch
):
    estudiante, sesion_a, sesion_b = dos_sesiones
    barrera = threading.Barrier(2, timeout=10)
    original = ConvocatoriaRepository.bloquear_codigos_de_estudiantes

    def bloquear_a_la_vez(self, ids):
        barrera.wait()  # ambas transacciones piden el bloqueo del mismo estudiante simultáneamente
        return original(self, ids)

    monkeypatch.setattr(ConvocatoriaRepository, "bloquear_codigos_de_estudiantes", bloquear_a_la_vez)
    resultados = _hilos(_convocar_en(creador, reloj, sesion_a, estudiante.correo), _convocar_en(creador, reloj, sesion_b, estudiante.correo))

    assert all(isinstance(r, list) for r in resultados), resultados
    codigos = _codigos_del_estudiante(estudiante.id)
    assert len(codigos) == 2 and len(set(codigos)) == 2
    assert "XXXXXXXXXXXX" in codigos  # uno lo obtuvo; el otro, al ver la colisión, generó otro


def test_control_without_lock_the_collision_happens(creador, reloj, dos_sesiones, generador_colisionador, monkeypatch):
    """Control: sin el bloqueo consultivo, ambas transacciones leen 'sin códigos' y asignan el mismo."""
    estudiante, sesion_a, sesion_b = dos_sesiones
    barrera = threading.Barrier(2, timeout=10)
    original_leer = ConvocatoriaRepository.cifrados_de_estudiante

    def leer_y_esperar(self, estudiante_id):
        resultado = original_leer(self, estudiante_id)
        barrera.wait()  # ambas ya leyeron antes de que la otra escriba
        return resultado

    monkeypatch.setattr(ConvocatoriaRepository, "bloquear_codigos_de_estudiantes", lambda self, ids: None)
    monkeypatch.setattr(ConvocatoriaRepository, "cifrados_de_estudiante", leer_y_esperar)
    _hilos(_convocar_en(creador, reloj, sesion_a, estudiante.correo), _convocar_en(creador, reloj, sesion_b, estudiante.correo))

    assert _codigos_del_estudiante(estudiante.id) == ["XXXXXXXXXXXX", "XXXXXXXXXXXX"]


def test_regeneration_also_takes_the_student_lock(client, creador_headers, en_curso, monkeypatch):
    llamadas = []
    original = ConvocatoriaRepository.bloquear_codigos_de_estudiantes
    monkeypatch.setattr(
        ConvocatoriaRepository, "bloquear_codigos_de_estudiantes", lambda self, ids: (llamadas.append(list(ids)), original(self, ids))
    )
    estudiante = en_curso["estudiantes"][0]
    client.post(_url(en_curso, estudiante.correo, "/regenerate-code"), headers=creador_headers, json={"motivo": "Código expuesto"})
    assert llamadas == [[estudiante.id]]


# --- 3. Reingreso para el módulo 4 ----------------------------------------------------------------


class ReingresoPermitido:
    def permite_reingreso(self, convocatoria, ahora) -> bool:
        return True


@pytest.fixture
def con_politica_de_reingreso():
    app.dependency_overrides[get_politica_reingreso] = lambda: ReingresoPermitido()
    yield
    app.dependency_overrides.pop(get_politica_reingreso, None)


def test_default_policy_rejects_login_outside_window(client, reloj, en_curso):
    estudiante = en_curso["estudiantes"][0]
    reloj.avanzar(hours=1, minutes=30)
    assert ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo]).status_code == 403


def test_reentry_policy_recovers_access_without_allowing_new_attempts(client, reloj, en_curso, con_politica_de_reingreso):
    estudiante = en_curso["estudiantes"][0]
    reloj.avanzar(hours=1, minutes=30)  # 09:30: ventana de ingreso cerrada, sesión EN_CURSO
    response = ingresar(client, estudiante.correo, en_curso["codigos"][estudiante.correo])
    assert response.status_code == 200
    token = response.json()["access_token"]

    datos = client.get(f"/api/v1/student/sessions/{en_curso['sesion']['id']}", headers=bearer(token)).json()
    assert datos["puede_iniciar_participacion"] is False
    with SessionLocal() as s:
        acceso = AccesoEstudiantilService(s, reloj).resolver(token, uuid.UUID(en_curso["sesion"]["id"]))
        with pytest.raises(IngresoNoPermitidoError):
            acceso.asegurar_inicio_participacion(reloj.ahora())


@pytest.mark.parametrize("situacion", ["finalizada", "revocada", "sin_iniciar"])
def test_reentry_policy_never_overrides_other_rules(
    client, creador_headers, reloj, espacio, caso_publicado, estudiantes, en_curso, con_politica_de_reingreso, situacion
):
    estudiante = en_curso["estudiantes"][0]
    codigo = en_curso["codigos"][estudiante.correo]
    if situacion == "finalizada":
        client.post(f"{SESSIONS_URL}/{en_curso['sesion']['id']}/finish", headers=creador_headers)
    elif situacion == "revocada":
        client.delete(_url(en_curso, estudiante.correo), headers=creador_headers)
    else:
        futura = programar(client, creador_headers, reloj, caso_publicado["id"], espacio.id, [estudiante], **{
            "inicio": (reloj.actual + timedelta(days=1)).isoformat(), "fin": (reloj.actual + timedelta(days=1, hours=2)).isoformat()
        })
        conv = convocatorias(client, creador_headers, futura["id"])[0]
        codigo = codigo_de(client, creador_headers, futura["id"], conv["id"])
    assert ingresar(client, estudiante.correo, codigo).status_code == 403
