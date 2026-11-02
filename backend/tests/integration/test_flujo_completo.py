"""Flujos de extremo a extremo que atraviesan los cuatro módulos por la API real.

Cada actor usa únicamente la API, como lo haría el cliente: el administrador da de alta a las
personas, el docente construye y programa el caso y el estudiante lo resuelve.
"""

import uuid

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.modules.auth.models import RolUsuario
from app.modules.participations.services import ConsultaEvaluacionService
from app.workers.participation_expiration import ejecutar_una_vez as expirar_intentos
from tests.cases_helpers import CASES_URL, dsl_valido, publicar
from tests.conftest import DEFAULT_PASSWORD, bearer, login
from tests.participations_helpers import PART_URL, guardar, iniciar_participacion
from tests.sessions_helpers import SESSIONS_URL, SPACES_URL, codigo_de, convocatorias, datos_sesion, ingresar

USERS_URL = "/api/v1/users"


def _ok(respuesta, esperado=200):
    assert respuesta.status_code == esperado, respuesta.text
    return respuesta.json()


@pytest.fixture
def escenario(client, admin_headers, reloj):
    """Prepara, solo por API, un docente autorizado, tres estudiantes, un espacio y un caso publicado."""
    _ok(
        client.post(
            f"{USERS_URL}/teachers",
            headers=admin_headers,
            json={"nombre_completo": "Docente Titular", "correo": "titular@universidad.edu.co", "clave": DEFAULT_PASSWORD},
        ),
        201,
    )
    usuarios = _ok(client.get(USERS_URL, headers=admin_headers, params={"rol": "DOCENTE"}))["items"]
    _ok(client.patch(f"{USERS_URL}/{usuarios[0]['id']}/case-permission", headers=admin_headers, json={"puede_crear_casos": True}))
    docente = bearer(login(client, "titular@universidad.edu.co"))

    correos = [f"estudiante{i}@universidad.edu.co" for i in range(3)]
    for i, correo in enumerate(correos):
        _ok(client.post(f"{USERS_URL}/students", headers=admin_headers, json={"nombre_completo": f"Estudiante {i}", "correo": correo}), 201)

    espacio = _ok(client.post(SPACES_URL, headers=admin_headers, json={"nombre": "Psicología Social", "periodo": "2026-2"}), 201)
    caso = _ok(client.post(f"{CASES_URL}/full", headers=docente, json=dsl_valido()), 201)
    _ok(publicar(client, docente, caso["id"]))
    return {"docente": docente, "correos": correos, "espacio": espacio, "caso": caso, "reloj": reloj}


def _programar(client, escenario, correos=None):
    reloj = escenario["reloj"]
    datos = datos_sesion(reloj, escenario["caso"]["id"], escenario["espacio"]["id"]) | {"estudiantes": correos or escenario["correos"]}
    sesion = _ok(client.post(SESSIONS_URL, headers=escenario["docente"], json=datos), 201)
    convs = {c["estudiante"]["correo"]: c for c in convocatorias(client, escenario["docente"], sesion["id"])}
    codigos = {correo: codigo_de(client, escenario["docente"], sesion["id"], c["id"]) for correo, c in convs.items()}
    return sesion, convs, codigos


def _iniciar_sesion(client, escenario, sesion):
    escenario["reloj"].avanzar(hours=1)
    _ok(client.post(f"{SESSIONS_URL}/{sesion['id']}/start", headers=escenario["docente"]))


def _token(client, correo, codigo):
    return bearer(_ok(ingresar(client, correo, codigo))["access_token"])


def test_de_la_creacion_del_caso_al_dato_para_evaluar(client, escenario):
    docente, correos = escenario["docente"], escenario["correos"]
    sesion, _, codigos = _programar(client, escenario)

    # Antes de iniciar la sesión nadie puede entrar.
    assert ingresar(client, correos[0], codigos[correos[0]]).status_code == 403
    _iniciar_sesion(client, escenario, sesion)

    # El estudiante 0 resuelve todo y finaliza.
    estudiante = _token(client, correos[0], codigos[correos[0]])
    intento = _ok(iniciar_participacion(client, estudiante, sesion["id"]), 201)
    cuestionario = _ok(client.get(f"{PART_URL}/{intento['id']}/questionnaire", headers=estudiante))
    assert "esCorrecta" not in str(cuestionario) and "es_correcta" not in str(cuestionario)
    for escenario_caso in cuestionario["escenarios"]:
        for pregunta in escenario_caso["preguntas"]:
            _ok(guardar(client, estudiante, intento["id"], pregunta["id"], pregunta["opciones"][0]["id"]))
    final = _ok(client.post(f"{PART_URL}/{intento['id']}/finish", headers=estudiante))
    assert final["progreso"]["porcentaje"] == 100

    # El estudiante 1 empieza y no termina; el estudiante 2 no se presenta.
    incompleto = _token(client, correos[1], codigos[correos[1]])
    abierto = _ok(iniciar_participacion(client, incompleto, sesion["id"]), 201)

    # El docente cierra la sesión.
    assert _ok(client.post(f"{SESSIONS_URL}/{sesion['id']}/finish", headers=docente))["estado"] == "FINALIZADO"

    with SessionLocal() as bd:
        consulta = ConsultaEvaluacionService(bd)
        completa = consulta.obtener(uuid.UUID(intento["id"]))
        cortada = consulta.obtener(uuid.UUID(abierto["id"]))
        cerradas = consulta.cerradas_de_sesion(uuid.UUID(sesion["id"]))
        ausentes = [c.estudiante.correo for c in consulta.ausencias(uuid.UUID(sesion["id"]))]

    assert (completa.completa_voluntariamente, completa.preguntas_sin_responder) == (True, [])
    assert len(completa.respuestas) == 3
    assert (cortada.completa_voluntariamente, cortada.motivo_cierre.value) == (False, "SESION_FINALIZADA")
    assert len(cortada.preguntas_sin_responder) == 3
    assert len(cerradas) == 2
    assert ausentes == [correos[2]]


def test_el_caso_queda_congelado_mientras_lo_usan_las_sesiones(client, escenario):
    docente = escenario["docente"]
    sesion, _, _ = _programar(client, escenario)
    base = f"{CASES_URL}/{escenario['caso']['id']}"

    assert client.patch(base, headers=docente, json={"nombre": "Cambio"}).status_code == 409
    assert client.delete(base, headers=docente).status_code == 409
    _ok(client.post(f"{base}/archive", headers=docente))

    # Archivar no invalida la sesión ya programada, pero impide programar otra.
    assert _ok(client.get(f"{SESSIONS_URL}/{sesion['id']}", headers=docente))["estado"] == "SIN_INICIAR"
    datos = datos_sesion(escenario["reloj"], escenario["caso"]["id"], escenario["espacio"]["id"])
    assert client.post(SESSIONS_URL, headers=docente, json=datos).status_code == 409


def test_desactivar_la_cuenta_corta_el_acceso_del_estudiante_en_todos_los_modulos(client, admin_headers, escenario):
    correo = escenario["correos"][0]
    sesion, _, codigos = _programar(client, escenario)
    _iniciar_sesion(client, escenario, sesion)
    estudiante = _token(client, correo, codigos[correo])
    intento = _ok(iniciar_participacion(client, estudiante, sesion["id"]), 201)
    usuarios = _ok(client.get(USERS_URL, headers=admin_headers, params={"rol": "ESTUDIANTE"}))["items"]
    objetivo = next(u for u in usuarios if u["correo"] == correo)

    _ok(client.patch(f"{USERS_URL}/{objetivo['id']}/status", headers=admin_headers, json={"activo": False}))

    assert client.get(f"{PART_URL}/{intento['id']}", headers=estudiante).status_code == 401
    assert ingresar(client, correo, codigos[correo]).status_code == 403


def test_retirar_y_reconvocar_conserva_el_conteo_de_intentos(client, escenario):
    docente, correo = escenario["docente"], escenario["correos"][0]
    sesion, convs, codigos = _programar(client, escenario)
    _iniciar_sesion(client, escenario, sesion)
    estudiante = _token(client, correo, codigos[correo])
    intento = _ok(iniciar_participacion(client, estudiante, sesion["id"]), 201)
    conv_url = f"{SESSIONS_URL}/{sesion['id']}/convocations"

    assert client.delete(f"{conv_url}/{convs[correo]['id']}", headers=docente).status_code == 204
    _ok(client.post(conv_url, headers=docente, json={"estudiantes": [correo]}), 201)
    nuevo_codigo = codigo_de(client, docente, sesion["id"], convs[correo]["id"])

    assert nuevo_codigo != codigos[correo]
    assert ingresar(client, correo, codigos[correo]).status_code == 401
    reingreso = _token(client, correo, nuevo_codigo)
    assert iniciar_participacion(client, reingreso, sesion["id"]).status_code == 409  # su único intento ya se usó
    historial = _ok(client.get(PART_URL, headers=reingreso))
    assert [(p["id"], p["motivo_cierre"]) for p in historial] == [(intento["id"], "CONVOCATORIA_REVOCADA")]


def test_tiempo_agotado_por_worker_mantiene_consistencia_entre_tablas(client, escenario):
    correo = escenario["correos"][0]
    sesion, _, codigos = _programar(client, escenario)
    _iniciar_sesion(client, escenario, sesion)
    estudiante = _token(client, correo, codigos[correo])
    intento = _ok(iniciar_participacion(client, estudiante, sesion["id"]), 201)
    pregunta = _ok(client.get(f"{PART_URL}/{intento['id']}/questionnaire", headers=estudiante))["escenarios"][0]["preguntas"][0]
    _ok(guardar(client, estudiante, intento["id"], pregunta["id"], pregunta["opciones"][0]["id"]))
    escenario["reloj"].avanzar(minutes=45)

    assert len(expirar_intentos(reloj=escenario["reloj"])) == 1

    vista = _ok(client.get(f"{PART_URL}/{intento['id']}", headers=estudiante))
    assert (vista["estado"], vista["motivo_cierre"]) == ("EXPIRADA", "TIEMPO_AGOTADO")
    assert vista["progreso"]["respondidas"] == 1  # lo respondido a tiempo se conserva
    with engine.connect() as conn:
        huerfanas = conn.execute(
            text(
                "SELECT count(*) FROM respuesta r LEFT JOIN opcion o ON o.id = r.opcion_id AND o.pregunta_id = r.pregunta_id "
                "WHERE o.id IS NULL"
            )
        ).scalar_one()
    assert huerfanas == 0


def test_dos_docentes_trabajan_aislados_en_todo_el_flujo(client, admin_headers, escenario, make_user):
    otro = make_user(RolUsuario.DOCENTE, puede_crear_casos=True)
    otro_headers = bearer(login(client, otro.correo))
    sesion, convs, _ = _programar(client, escenario)
    conv = next(iter(convs.values()))

    assert _ok(client.get(CASES_URL, headers=otro_headers))["total"] == 0
    assert _ok(client.get(SESSIONS_URL, headers=otro_headers))["total"] == 0
    assert client.get(f"{CASES_URL}/{escenario['caso']['id']}", headers=otro_headers).status_code == 404
    assert client.get(f"{SESSIONS_URL}/{sesion['id']}/convocations/{conv['id']}/code", headers=otro_headers).status_code == 404
