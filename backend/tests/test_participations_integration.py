"""Integración del módulo 4 con revocaciones, cierres de sesión, reingreso, módulo 5 y PostgreSQL."""

import ast
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, engine
from app.modules.participations.models import EstadoParticipacionEnum, MotivoCierreEnum
from app.modules.participations.services import ConsultaEvaluacionService
from app.modules.sessions.models import Convocatoria
from app.modules.sessions.security import crear_token_estudiante, huella_codigo
from tests.conftest import PROJECT_ROOT, alembic_config, bearer
from tests.participations_helpers import PART_URL, contar, entorno, fila, guardar, iniciar_participacion  # noqa: F401
from tests.sessions_helpers import SESSIONS_URL, ingresar

T8 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


def _estado(pid):
    return fila("SELECT estado::text AS estado, motivo_cierre::text AS motivo, fin FROM participacion WHERE id = :id", id=pid)


# --- Revocación ------------------------------------------------------------------------------


def test_revocation_closes_active_attempt_and_keeps_answers(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    q = entorno.preguntas[0]
    guardar(entorno.client, headers, p["id"], q["id"], q["opciones"][1]["id"])
    entorno.reloj.avanzar(minutes=7)

    assert entorno.client.delete(entorno.conv_url(0), headers=entorno.docente_headers).status_code == 204
    estado = _estado(p["id"])
    assert (estado.estado, estado.motivo) == ("EXPIRADA", "CONVOCATORIA_REVOCADA")
    assert estado.fin == T8 + timedelta(minutes=7)
    assert contar("SELECT count(*) FROM respuesta WHERE participacion_id = :p", p=p["id"]) == 1
    assert entorno.client.get(f"{PART_URL}/{p['id']}", headers=headers).status_code == 401  # acceso perdido


def test_reactivation_does_not_reopen_attempts_and_keeps_count(entorno):
    p = entorno.iniciar(entorno.headers())
    estudiante = entorno.estudiantes[0]
    entorno.client.delete(entorno.conv_url(0), headers=entorno.docente_headers)
    entorno.client.post(f"{SESSIONS_URL}/{entorno.sesion_id}/convocations", headers=entorno.docente_headers, json={"estudiantes": [estudiante.correo]})
    codigo = entorno.client.get(entorno.conv_url(0, "/code"), headers=entorno.docente_headers).json()["codigo_autorizacion"]
    headers = bearer(ingresar(entorno.client, estudiante.correo, codigo).json()["access_token"])

    assert _estado(p["id"]).estado == "EXPIRADA"  # la reactivación no la reabre
    assert iniciar_participacion(entorno.client, headers, entorno.sesion_id).status_code == 409  # 1 de 1 usado
    entorno.client.patch(entorno.conv_url(0, "/attempts"), headers=entorno.docente_headers, json={"intentos_autorizados": 2, "motivo": "Reactivación"})
    nuevo = iniciar_participacion(entorno.client, headers, entorno.sesion_id)
    assert nuevo.status_code == 201 and nuevo.json()["numero_intento"] == 2
    assert [x["numero_intento"] for x in entorno.client.get(PART_URL, headers=headers).json()] == [1, 2]


# --- Finalización de la sesión ----------------------------------------------------------------


def test_manual_session_finish_closes_active_attempts_only(entorno):
    activa = entorno.iniciar(entorno.headers(0))
    terminada_headers = entorno.headers(1)
    terminada = entorno.iniciar(terminada_headers)
    entorno.client.post(f"{PART_URL}/{terminada['id']}/finish", headers=terminada_headers)
    fin_previo = _estado(terminada["id"]).fin
    entorno.reloj.avanzar(minutes=12)

    assert entorno.client.post(f"{SESSIONS_URL}/{entorno.sesion_id}/finish", headers=entorno.docente_headers).status_code == 200
    a = _estado(activa["id"])
    assert (a.estado, a.motivo, a.fin) == ("EXPIRADA", "SESION_FINALIZADA", T8 + timedelta(minutes=12))
    t = _estado(terminada["id"])
    assert (t.estado, t.motivo, t.fin) == ("FINALIZADA", "VOLUNTARIO", fin_previo)


def test_automatic_close_after_deadline_keeps_time_cause(entorno, monkeypatch):
    """Si el intento ya había vencido cuando corre el cierre de la sesión, prevalece TIEMPO_AGOTADO."""
    from app.core.config import get_settings
    from app.workers.session_expiration import ejecutar_una_vez as cerrar_sesiones

    monkeypatch.setattr(get_settings(), "SESSION_AUTO_CLOSE_HOURS", 0)
    entorno.reloj.avanzar(minutes=50)  # 08:50: el intento vence a las 09:30
    p = entorno.iniciar(entorno.headers())
    entorno.reloj.actual = T8 + timedelta(hours=2)  # 10:00 = fin de la sesión, cierre inmediato (plazo 0)
    assert len(cerrar_sesiones(reloj=entorno.reloj)) == 1
    estado = _estado(p["id"])
    assert (estado.estado, estado.motivo, estado.fin) == ("EXPIRADA", "TIEMPO_AGOTADO", T8 + timedelta(minutes=90))


def test_automatic_close_with_time_left_marks_session_closure(entorno, monkeypatch):
    """Cierre automático mientras el intento aún tiene tiempo: EXPIRADA por SESION_FINALIZADA, fin = ahora."""
    from app.core.config import get_settings
    from app.workers.session_expiration import ejecutar_una_vez as cerrar_sesiones

    monkeypatch.setattr(get_settings(), "SESSION_AUTO_CLOSE_HOURS", 0)
    p = entorno.iniciar(entorno.headers())  # 08:00, vence a las 08:40
    with engine.begin() as conn:  # sesión que termina a las 08:20 (antes de que venza el intento)
        conn.execute(text("UPDATE sesion SET fin = :f WHERE id = :id"), {"f": T8 + timedelta(minutes=20), "id": entorno.sesion_id})
    entorno.reloj.actual = T8 + timedelta(minutes=20)
    assert len(cerrar_sesiones(reloj=entorno.reloj)) == 1
    estado = _estado(p["id"])
    assert (estado.estado, estado.motivo, estado.fin) == ("EXPIRADA", "SESION_FINALIZADA", T8 + timedelta(minutes=20))


def test_recovery_after_jwt_expiry_returns_same_attempt(entorno):
    """El JWT vence a mitad del intento: un ingreso nuevo recupera el MISMO intento con su tiempo real."""
    estudiante = entorno.estudiantes[0]
    p = entorno.iniciar(entorno.headers())
    conv_id = uuid.UUID(entorno.convocatorias[estudiante.correo]["id"])
    with SessionLocal() as s:
        cifrado = s.get(Convocatoria, conv_id).codigo_autorizacion
    vencido = crear_token_estudiante(
        estudiante_id=estudiante.id, convocatoria_id=conv_id, sesion_id=uuid.UUID(entorno.sesion_id),
        version_usuario=0, huella_codigo=huella_codigo(cifrado), duracion=timedelta(seconds=-1),
    )
    assert entorno.client.get(f"{PART_URL}/{p['id']}", headers=bearer(vencido)).status_code == 401

    entorno.reloj.avanzar(minutes=25)
    recuperada = iniciar_participacion(entorno.client, entorno.headers(), entorno.sesion_id)
    assert recuperada.status_code == 200 and recuperada.json()["id"] == p["id"]
    assert recuperada.json()["inicio"] == p["inicio"] and recuperada.json()["tiempo_restante_segundos"] == 15 * 60


def test_recovery_after_window_never_creates_a_new_attempt(entorno):
    estudiante = entorno.estudiantes[0]
    entorno.reloj.avanzar(minutes=50)  # 08:50
    p = entorno.iniciar(entorno.headers())  # vence a las 09:30
    entorno.reloj.avanzar(minutes=20)  # 09:10: ventana cerrada, intento con 20 min

    response = ingresar(entorno.client, estudiante.correo, entorno.codigos[estudiante.correo])
    assert response.status_code == 200  # política de reingreso del módulo 4
    headers = bearer(response.json()["access_token"])
    recuperada = iniciar_participacion(entorno.client, headers, entorno.sesion_id)
    assert recuperada.status_code == 200 and recuperada.json()["id"] == p["id"]
    assert recuperada.json()["tiempo_restante_segundos"] == 20 * 60

    # Al finalizar, ya no hay reingreso ni intento nuevo fuera de la ventana, aunque haya más autorizados.
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    entorno.client.patch(entorno.conv_url(0, "/attempts"), headers=entorno.docente_headers, json={"intentos_autorizados": 3, "motivo": "Prueba"})
    assert iniciar_participacion(entorno.client, headers, entorno.sesion_id).status_code == 403
    assert ingresar(entorno.client, estudiante.correo, entorno.codigos[estudiante.correo]).status_code == 403
    assert contar("SELECT count(*) FROM participacion") == 1


def test_recovery_after_session_fin_while_in_progress(entorno):
    """Después de `sesion.fin`, con la sesión aún EN_CURSO y el intento con tiempo, se puede reingresar y responder."""
    estudiante = entorno.estudiantes[0]
    entorno.reloj.avanzar(minutes=59)  # 08:59, dentro de la ventana (límite 09:00): el intento vence a las 09:39
    p = entorno.iniciar(entorno.headers())
    with engine.begin() as conn:  # la sesión termina a las 09:20, antes de que venza el intento
        conn.execute(text("UPDATE sesion SET fin = :f WHERE id = :id"), {"f": T8 + timedelta(hours=1, minutes=20), "id": entorno.sesion_id})
    entorno.reloj.actual = T8 + timedelta(hours=1, minutes=30)  # 09:30: pasado sesion.fin, sesión EN_CURSO

    response = ingresar(entorno.client, estudiante.correo, entorno.codigos[estudiante.correo])
    assert response.status_code == 200
    headers = bearer(response.json()["access_token"])
    assert entorno.client.get(f"{PART_URL}/{p['id']}", headers=headers).json()["estado"] == "EN_PROGRESO"
    q = entorno.preguntas[0]
    assert guardar(entorno.client, headers, p["id"], q["id"], q["opciones"][0]["id"]).status_code == 200


def test_no_recovery_for_closed_or_revoked(entorno):
    estudiante = entorno.estudiantes[0]
    entorno.reloj.avanzar(minutes=50)
    entorno.iniciar(entorno.headers())
    entorno.reloj.avanzar(minutes=15)  # 09:05: ventana cerrada, intento vigente
    entorno.client.delete(entorno.conv_url(0), headers=entorno.docente_headers)
    assert ingresar(entorno.client, estudiante.correo, entorno.codigos[estudiante.correo]).status_code == 403


# --- Consulta para el módulo 5 ------------------------------------------------------------------


def test_evaluation_query_service(entorno):
    headers = entorno.headers(0)
    p = entorno.iniciar(headers)
    q1, q2, q3 = entorno.preguntas
    guardar(entorno.client, headers, p["id"], q1["id"], q1["opciones"][0]["id"])
    guardar(entorno.client, headers, p["id"], q3["id"], q3["opciones"][2]["id"])
    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)

    with SessionLocal() as s:
        consulta = ConsultaEvaluacionService(s)
        datos = consulta.obtener(uuid.UUID(p["id"]))
        assert datos.estado == EstadoParticipacionEnum.FINALIZADA and datos.motivo_cierre == MotivoCierreEnum.VOLUNTARIO
        assert datos.completa_voluntariamente
        assert datos.respuestas == {uuid.UUID(q1["id"]): uuid.UUID(q1["opciones"][0]["id"]), uuid.UUID(q3["id"]): uuid.UUID(q3["opciones"][2]["id"])}
        assert datos.preguntas_sin_responder == [uuid.UUID(q2["id"])]
        assert datos.caso.id == uuid.UUID(entorno.caso["id"]) and datos.rubrica is not None and len(datos.rubrica.criterios) == 2
        assert datos.estudiante_id == entorno.estudiantes[0].id

        sesion_id = uuid.UUID(entorno.sesion_id)
        assert consulta.cerradas_de_sesion(sesion_id) == [uuid.UUID(p["id"])]
        # Los otros dos convocados nunca participaron: ausencias, sin participaciones ficticias.
        assert {c.estudiante_id for c in consulta.ausencias(sesion_id)} == {e.id for e in entorno.estudiantes[1:]}
    assert contar("SELECT count(*) FROM participacion") == 1


# --- PostgreSQL ------------------------------------------------------------------------------------


def test_database_rules_for_participations(entorno):
    headers = entorno.headers()
    p = entorno.iniciar(headers)
    conv_id = entorno.convocatorias[entorno.estudiantes[0].correo]["id"]

    def falla(sql, nombre, **params):
        with pytest.raises(IntegrityError) as info, engine.begin() as conn:
            conn.execute(text(sql), params)
        if nombre:
            assert info.value.orig.diag.constraint_name == nombre, sql

    # El disparador de intentos se evalúa antes que el índice: con 1 autorizado, el intento 2 lo rechaza él.
    falla("INSERT INTO participacion (convocatoria_id, numero_intento, inicio) VALUES (:c, 2, now())", "ck_participacion_intentos_autorizados", c=conv_id)
    with engine.begin() as conn:
        conn.execute(text("UPDATE convocatoria SET intentos_autorizados = 2 WHERE id = :c"), {"c": conv_id})
    # Con 2 autorizados, el índice parcial impide un segundo intento EN_PROGRESO.
    falla("INSERT INTO participacion (convocatoria_id, numero_intento, inicio) VALUES (:c, 2, now())", "uq_participacion_activa_por_convocatoria", c=conv_id)
    falla("UPDATE participacion SET inicio = inicio - interval '1 hour'", "ck_participacion_datos_inmutables")
    falla("UPDATE participacion SET estado = 'FINALIZADA'", "ck_participacion_fin_segun_estado")
    falla("DELETE FROM participacion", "ck_participacion_sin_borrado")

    entorno.client.post(f"{PART_URL}/{p['id']}/finish", headers=headers)
    falla("UPDATE participacion SET estado = 'EN_PROGRESO', fin = NULL, motivo_cierre = NULL", "ck_participacion_cerrada_inmutable")
    falla("INSERT INTO participacion (convocatoria_id, numero_intento, inicio) VALUES (:c, 3, now())", "ck_participacion_intentos_autorizados", c=conv_id)
    falla("INSERT INTO participacion (convocatoria_id, numero_intento, inicio) VALUES (:c, 1, now())", "uq_participacion_convocatoria_intento", c=conv_id)
    # El módulo 5 sí puede registrar la nota de una participación cerrada.
    with engine.begin() as conn:
        conn.execute(text("UPDATE participacion SET nota_final = 4.5, retroalimentacion_general = 'Bien' WHERE id = :id"), {"id": p["id"]})


def test_migrations_0008_0009_schema_and_independence():
    inspector = inspect(engine)
    assert inspector.has_table("participacion") and inspector.has_table("respuesta")
    indices = {i["name"]: i for i in inspector.get_indexes("participacion")}
    assert indices["uq_participacion_activa_por_convocatoria"]["unique"]
    assert ("id", "pregunta_id") in {tuple(u["column_names"]) for u in inspector.get_unique_constraints("opcion")}
    fks = {fk["name"]: fk["options"].get("ondelete") for fk in inspector.get_foreign_keys("respuesta")}
    assert fks["fk_respuesta_opcion_de_la_pregunta"] == "RESTRICT"
    for archivo in ("0008_opcion_unicidad_pregunta.py", "0009_participaciones_respuestas.py"):
        arbol = ast.parse((PROJECT_ROOT / "alembic" / "versions" / archivo).read_text(encoding="utf-8"))
        importados = {n.module for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom) and n.module} | {
            a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
        }
        assert not any(m == "app" or m.startswith("app.") for m in importados), archivo


def test_downgrade_to_0007_removes_module_4(entorno):
    entorno.iniciar(entorno.headers())
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "0007_revocacion_convocatoria")
    try:
        inspector = inspect(engine)
        assert not inspector.has_table("participacion") and not inspector.has_table("respuesta")
        assert ("id", "pregunta_id") not in {tuple(u["column_names"]) for u in inspector.get_unique_constraints("opcion")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regtype('estado_participacion')")).scalar() is None
    finally:
        engine.dispose()
        command.upgrade(config, "head")


