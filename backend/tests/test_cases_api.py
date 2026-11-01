"""Casos, permisos y edición individual de componentes (módulo 2)."""

import uuid

import pytest

from app.modules.auth.models import RolUsuario
from tests.cases_helpers import CASES_URL, NIVELES_API, contar_filas, crear_caso_completo, publicar
from tests.conftest import bearer, login

BORRADOR = {"nombre": "Caso de prueba", "desarrollo_situacional": "Desarrollo", "tiempo_estimado_min": 30}


def crear_borrador(client, headers, **datos) -> dict:
    response = client.post(CASES_URL, headers=headers, json=BORRADOR | datos)
    assert response.status_code == 201, response.text
    return response.json()


# --- Creación y permisos --------------------------------------------------------


def test_authorized_teacher_creates_draft(client, creador, creador_headers):
    caso = crear_borrador(client, creador_headers)
    assert caso["estado"] == "BORRADOR"
    assert caso["autor_id"] == str(creador.id)
    assert caso["historia"] is None
    assert caso["escenarios"] == [] and caso["rubrica"] is None


def test_author_id_comes_from_token_not_body(client, creador_headers, docente):
    response = client.post(CASES_URL, headers=creador_headers, json=BORRADOR | {"autor_id": str(docente.id)})
    assert response.status_code == 422  # el campo no forma parte del esquema


def test_teacher_without_permission_cannot_create(client, docente_headers):
    assert client.post(CASES_URL, headers=docente_headers, json=BORRADOR).status_code == 403
    assert client.post(f"{CASES_URL}/full", headers=docente_headers, json={}).status_code == 403


@pytest.mark.parametrize("headers_fixture", ["admin_headers", "estudiante_headers"])
def test_admin_and_student_cannot_create_or_read(client, request, headers_fixture, creador_headers):
    headers = request.getfixturevalue(headers_fixture)
    caso = crear_borrador(client, creador_headers)
    assert client.post(CASES_URL, headers=headers, json=BORRADOR).status_code == 403
    assert client.get(CASES_URL, headers=headers).status_code == 403
    assert client.get(f"{CASES_URL}/{caso['id']}", headers=headers).status_code == 403
    assert client.get(f"{CASES_URL}/{caso['id']}/dsl", headers=headers).status_code == 403


def test_unauthenticated_requests_rejected(client):
    assert client.post(CASES_URL, json=BORRADOR).status_code == 401
    assert client.get(f"{CASES_URL}/{uuid.uuid4()}").status_code == 401


def test_create_validation_errors(client, creador_headers):
    assert client.post(CASES_URL, headers=creador_headers, json={"nombre": " "}).status_code == 422
    assert client.post(CASES_URL, headers=creador_headers, json={"nombre": "x", "tiempo_estimado_min": 0}).status_code == 422
    assert client.post(CASES_URL, headers=creador_headers, json={"nombre": "x" * 201}).status_code == 422


# --- Consulta y aislamiento entre docentes ----------------------------------------


def test_list_only_own_cases_with_filter_and_pagination(client, creador_headers, otro_creador_headers):
    crear_borrador(client, creador_headers, nombre="Mío 1")
    publicable = crear_caso_completo(client, creador_headers)
    assert publicar(client, creador_headers, publicable["id"]).status_code == 200
    crear_borrador(client, otro_creador_headers, nombre="Ajeno")

    todos = client.get(CASES_URL, headers=creador_headers).json()
    assert todos["total"] == 2
    assert {c["nombre"] for c in todos["items"]} == {"Mío 1", "Conformidad en el aula"}

    publicados = client.get(CASES_URL, headers=creador_headers, params={"estado": "PUBLICADO"}).json()
    assert publicados["total"] == 1 and publicados["items"][0]["estado"] == "PUBLICADO"

    pagina = client.get(CASES_URL, headers=creador_headers, params={"limit": 1, "skip": 1}).json()
    assert len(pagina["items"]) == 1 and pagina["total"] == 2


def test_teacher_can_read_own_case_after_permission_revoked(client, make_user):
    docente = make_user(RolUsuario.DOCENTE, puede_crear_casos=True)
    headers = bearer(login(client, docente.correo))
    caso = crear_borrador(client, headers)

    admin = make_user(RolUsuario.ADMINISTRADOR)
    admin_headers = bearer(login(client, admin.correo))
    client.patch(f"/api/v1/users/{docente.id}/case-permission", headers=admin_headers, json={"puede_crear_casos": False})

    assert client.get(f"{CASES_URL}/{caso['id']}", headers=headers).status_code == 200
    assert client.patch(f"{CASES_URL}/{caso['id']}", headers=headers, json={"nombre": "Otro"}).status_code == 403


def test_other_teacher_case_is_hidden(client, creador_headers, otro_creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    caso_id = caso["id"]
    escenario_id = caso["escenarios"][0]["id"]
    pregunta_id = caso["escenarios"][0]["preguntas"][0]["id"]

    peticiones = [
        ("get", f"{CASES_URL}/{caso_id}", None),
        ("patch", f"{CASES_URL}/{caso_id}", {"nombre": "Robado"}),
        ("delete", f"{CASES_URL}/{caso_id}", None),
        ("post", f"{CASES_URL}/{caso_id}/publish", None),
        ("post", f"{CASES_URL}/{caso_id}/validate", None),
        ("get", f"{CASES_URL}/{caso_id}/dsl", None),
        ("post", f"{CASES_URL}/{caso_id}/scenarios", {"titulo": "Intruso"}),
        ("patch", f"{CASES_URL}/{caso_id}/scenarios/{escenario_id}", {"titulo": "Intruso"}),
        ("patch", f"{CASES_URL}/{caso_id}/questions/{pregunta_id}", {"enunciado": "Intruso"}),
        ("get", f"{CASES_URL}/{caso_id}/rubric", None),
    ]
    for metodo, url, cuerpo in peticiones:
        kwargs = {"headers": otro_creador_headers}
        if cuerpo is not None:
            kwargs["json"] = cuerpo
        response = getattr(client, metodo)(url, **kwargs)
        assert response.status_code == 404, (metodo, url, response.text)
        assert response.json() == {"detail": "Caso no encontrado."}

    # Nada cambió.
    assert client.get(f"{CASES_URL}/{caso_id}", headers=creador_headers).json()["nombre"] == "Conformidad en el aula"


def test_case_not_found(client, creador_headers):
    assert client.get(f"{CASES_URL}/{uuid.uuid4()}", headers=creador_headers).status_code == 404
    assert client.get(f"{CASES_URL}/no-es-uuid", headers=creador_headers).status_code == 422


# --- Actualización y eliminación de borradores -------------------------------------


def test_update_draft_general_information(client, creador_headers):
    caso = crear_borrador(client, creador_headers)
    response = client.patch(
        f"{CASES_URL}/{caso['id']}",
        headers=creador_headers,
        json={"nombre": "Renombrado", "historia": "Nueva historia", "desarrollo_situacional": None},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["nombre"] == "Renombrado"
    assert body["historia"] == "Nueva historia"
    assert body["desarrollo_situacional"] is None
    assert body["actualizado_en"] >= caso["actualizado_en"]


def test_update_rejects_null_name_and_state_field(client, creador_headers):
    caso = crear_borrador(client, creador_headers)
    assert client.patch(f"{CASES_URL}/{caso['id']}", headers=creador_headers, json={"nombre": None}).status_code == 400
    assert client.patch(f"{CASES_URL}/{caso['id']}", headers=creador_headers, json={"estado": "PUBLICADO"}).status_code == 422


def test_delete_draft_removes_all_content(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    assert contar_filas()["opcion"] > 0
    assert client.delete(f"{CASES_URL}/{caso['id']}", headers=creador_headers).status_code == 204
    assert client.get(f"{CASES_URL}/{caso['id']}", headers=creador_headers).status_code == 404
    assert set(contar_filas().values()) == {0}


@pytest.mark.parametrize("estado_final", ["PUBLICADO", "ARCHIVADO"])
def test_published_and_archived_cases_are_read_only(client, creador_headers, estado_final):
    caso = crear_caso_completo(client, creador_headers)
    cid = caso["id"]
    escenario = caso["escenarios"][0]
    pregunta = escenario["preguntas"][0]
    opcion = pregunta["opciones"][0]
    criterio = caso["rubrica"]["criterios"][0]
    assert publicar(client, creador_headers, cid).status_code == 200
    if estado_final == "ARCHIVADO":
        assert client.post(f"{CASES_URL}/{cid}/archive", headers=creador_headers).status_code == 200

    intentos = [
        ("patch", f"{CASES_URL}/{cid}", {"nombre": "Cambio"}),
        ("delete", f"{CASES_URL}/{cid}", None),
        ("post", f"{CASES_URL}/{cid}/scenarios", {"titulo": "Nuevo"}),
        ("patch", f"{CASES_URL}/{cid}/scenarios/{escenario['id']}", {"titulo": "Cambio"}),
        ("delete", f"{CASES_URL}/{cid}/scenarios/{escenario['id']}", None),
        ("post", f"{CASES_URL}/{cid}/scenarios/{escenario['id']}/questions", {"enunciado": "Nueva"}),
        ("patch", f"{CASES_URL}/{cid}/questions/{pregunta['id']}", {"enunciado": "Cambio"}),
        ("patch", f"{CASES_URL}/{cid}/questions/{pregunta['id']}/options/{opcion['id']}", {"es_correcta": False}),
        ("post", f"{CASES_URL}/{cid}/questions/{pregunta['id']}/options", {"texto": "Otra"}),
        ("patch", f"{CASES_URL}/{cid}/rubric", {"escala_max": "10.00"}),
        ("patch", f"{CASES_URL}/{cid}/rubric/criteria/{criterio['id']}", {"peso": "50.00"}),
        ("delete", f"{CASES_URL}/{cid}/rubric/criteria/{criterio['id']}", None),
    ]
    for metodo, url, cuerpo in intentos:
        kwargs = {"headers": creador_headers}
        if cuerpo is not None:
            kwargs["json"] = cuerpo
        response = getattr(client, metodo)(url, **kwargs)
        assert response.status_code == 409, (metodo, url, response.text)

    actual = client.get(f"{CASES_URL}/{cid}", headers=creador_headers).json()
    assert actual["estado"] == estado_final
    assert actual["escenarios"] == caso["escenarios"]
    assert actual["rubrica"] == caso["rubrica"]


# --- Escenarios ---------------------------------------------------------------------


def test_scenario_crud_and_ordering(client, creador_headers):
    cid = crear_borrador(client, creador_headers)["id"]
    url = f"{CASES_URL}/{cid}/scenarios"

    ids = []
    for titulo in ("A", "B", "C"):
        response = client.post(url, headers=creador_headers, json={"titulo": titulo})
        assert response.status_code == 201
        ids.append(response.json()["id"])
    assert [e["orden"] for e in client.get(url, headers=creador_headers).json()] == [1, 2, 3]

    # Orden explícito ocupado: conflicto.
    assert client.post(url, headers=creador_headers, json={"titulo": "D", "orden": 2}).status_code == 409

    # Reordenar (intercambios que exigen la restricción DEFERRABLE).
    response = client.put(f"{url}/order", headers=creador_headers, json={"ids": [ids[2], ids[0], ids[1]]})
    assert response.status_code == 200
    assert [(e["titulo"], e["orden"]) for e in response.json()] == [("C", 1), ("A", 2), ("B", 3)]

    # Reorden incompleto o con ajenos: 400.
    assert client.put(f"{url}/order", headers=creador_headers, json={"ids": ids[:2]}).status_code == 400
    assert client.put(f"{url}/order", headers=creador_headers, json={"ids": [ids[0], ids[0], ids[1]]}).status_code == 422

    # Modificar y eliminar: los órdenes se compactan.
    response = client.patch(f"{url}/{ids[0]}", headers=creador_headers, json={"contexto": "Contexto A"})
    assert response.status_code == 200 and response.json()["contexto"] == "Contexto A"
    assert client.delete(f"{url}/{ids[2]}", headers=creador_headers).status_code == 204
    assert [(e["titulo"], e["orden"]) for e in client.get(url, headers=creador_headers).json()] == [("A", 1), ("B", 2)]
    assert client.get(f"{url}/{ids[2]}", headers=creador_headers).status_code == 404


def test_scenario_of_other_case_is_not_found(client, creador_headers):
    caso_a = crear_caso_completo(client, creador_headers)
    caso_b = crear_borrador(client, creador_headers)
    escenario_a = caso_a["escenarios"][0]["id"]
    response = client.patch(f"{CASES_URL}/{caso_b['id']}/scenarios/{escenario_a}", headers=creador_headers, json={"titulo": "X"})
    assert response.status_code == 404


# --- Preguntas y opciones -------------------------------------------------------------


def test_question_and_option_crud(client, creador_headers):
    cid = crear_borrador(client, creador_headers)["id"]
    escenario = client.post(f"{CASES_URL}/{cid}/scenarios", headers=creador_headers, json={"titulo": "E"}).json()
    q_url = f"{CASES_URL}/{cid}/scenarios/{escenario['id']}/questions"

    p1 = client.post(q_url, headers=creador_headers, json={"enunciado": "¿Primera?"}).json()
    p2 = client.post(q_url, headers=creador_headers, json={"enunciado": "¿Segunda?"}).json()
    assert (p1["orden"], p2["orden"], p1["criterio_id"]) == (1, 2, None)

    reordenadas = client.put(f"{q_url}/order", headers=creador_headers, json={"ids": [p2["id"], p1["id"]]}).json()
    assert [p["enunciado"] for p in reordenadas] == ["¿Segunda?", "¿Primera?"]

    o_url = f"{CASES_URL}/{cid}/questions/{p1['id']}/options"
    o1 = client.post(o_url, headers=creador_headers, json={"texto": "Sí", "es_correcta": True}).json()
    o2 = client.post(o_url, headers=creador_headers, json={"texto": "No", "justificacion": "Porque no"}).json()
    assert (o1["orden"], o2["orden"], o1["justificacion"]) == (1, 2, None)

    response = client.patch(f"{o_url}/{o1['id']}", headers=creador_headers, json={"justificacion": "Porque sí"})
    assert response.status_code == 200 and response.json()["justificacion"] == "Porque sí"

    reordenadas = client.put(f"{o_url}/order", headers=creador_headers, json={"ids": [o2["id"], o1["id"]]}).json()
    assert [o["texto"] for o in reordenadas] == ["No", "Sí"]

    assert client.delete(f"{o_url}/{o2['id']}", headers=creador_headers).status_code == 204
    restantes = client.get(o_url, headers=creador_headers).json()
    assert [(o["texto"], o["orden"]) for o in restantes] == [("Sí", 1)]

    response = client.patch(f"{CASES_URL}/{cid}/questions/{p1['id']}", headers=creador_headers, json={"enunciado": "¿Editada?"})
    assert response.status_code == 200 and response.json()["enunciado"] == "¿Editada?"

    assert client.delete(f"{CASES_URL}/{cid}/questions/{p2['id']}", headers=creador_headers).status_code == 204
    assert [(p["enunciado"], p["orden"]) for p in client.get(q_url, headers=creador_headers).json()] == [("¿Editada?", 1)]


def test_option_of_other_question_is_not_found(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    p1, p2 = caso["escenarios"][0]["preguntas"][:2]
    opcion_p2 = p2["opciones"][0]["id"]
    url = f"{CASES_URL}/{caso['id']}/questions/{p1['id']}/options/{opcion_p2}"
    assert client.patch(url, headers=creador_headers, json={"texto": "X"}).status_code == 404


def test_question_criterion_must_belong_to_same_case(client, creador_headers):
    caso_a = crear_caso_completo(client, creador_headers)
    caso_b = crear_caso_completo(client, creador_headers)
    pregunta_a = caso_a["escenarios"][0]["preguntas"][0]["id"]
    criterio_b = caso_b["rubrica"]["criterios"][0]["id"]
    criterio_a = caso_a["rubrica"]["criterios"][1]["id"]

    url = f"{CASES_URL}/{caso_a['id']}/questions/{pregunta_a}"
    response = client.patch(url, headers=creador_headers, json={"criterio_id": criterio_b})
    assert response.status_code == 400
    assert response.json() == {"detail": "El criterio no pertenece a la rúbrica de este caso."}

    assert client.patch(url, headers=creador_headers, json={"criterio_id": criterio_a}).json()["criterio_id"] == criterio_a
    # null deja la asociación pendiente en el borrador.
    assert client.patch(url, headers=creador_headers, json={"criterio_id": None}).json()["criterio_id"] is None


# --- Rúbrica ----------------------------------------------------------------------


def test_rubric_and_criteria_crud(client, creador_headers):
    cid = crear_borrador(client, creador_headers)["id"]
    url = f"{CASES_URL}/{cid}/rubric"

    assert client.get(url, headers=creador_headers).status_code == 404
    response = client.post(
        url,
        headers=creador_headers,
        json={"escala_max": "5", "criterios": [{"descripcion": "Análisis", "peso": "60.5", "niveles": NIVELES_API[:2]}]},
    )
    assert response.status_code == 201
    rubrica = response.json()
    assert rubrica["escala_max"] == "5.00"
    assert rubrica["criterios"][0]["peso"] == "60.50"
    assert rubrica["criterios"][0]["niveles"][1] == {"nombre": "Aceptable", "min_aciertos_pct": "50.00", "valor": "3.00"}

    assert client.post(url, headers=creador_headers, json={"escala_max": "5"}).status_code == 409

    assert client.patch(url, headers=creador_headers, json={"escala_max": "10"}).json()["escala_max"] == "10.00"

    nuevo = client.post(f"{url}/criteria", headers=creador_headers, json={"descripcion": "Ética", "peso": "39.5"})
    assert nuevo.status_code == 201
    criterio_id = nuevo.json()["id"]
    assert nuevo.json()["niveles"] == []

    response = client.patch(f"{url}/criteria/{criterio_id}", headers=creador_headers, json={"niveles": NIVELES_API})
    assert response.status_code == 200 and len(response.json()["niveles"]) == 3
    assert len(client.get(f"{url}/criteria", headers=creador_headers).json()) == 2

    assert client.delete(f"{url}/criteria/{criterio_id}", headers=creador_headers).status_code == 204
    assert client.get(url, headers=creador_headers).json()["criterios"][0]["descripcion"] == "Análisis"


@pytest.mark.parametrize(
    "criterio",
    [
        {"descripcion": "X", "peso": "0"},
        {"descripcion": "X", "peso": "100.01"},
        {"descripcion": "X", "peso": "33.333"},
        {"descripcion": "", "peso": "10"},
        {"descripcion": "X", "peso": "10", "niveles": [{"nombre": "A", "min_aciertos_pct": 120, "valor": 1}]},
        {"descripcion": "X", "peso": "10", "niveles": [{"nombre": "A", "min_aciertos_pct": 10, "valor": 1, "extra": 1}]},
    ],
)
def test_invalid_criterion_payloads(client, creador_headers, criterio):
    cid = crear_borrador(client, creador_headers)["id"]
    response = client.post(f"{CASES_URL}/{cid}/rubric", headers=creador_headers, json={"escala_max": "5", "criterios": [criterio]})
    assert response.status_code == 422


def test_deleting_criterion_leaves_question_pending(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    criterio_id = caso["rubrica"]["criterios"][0]["id"]
    asociadas = [p["id"] for e in caso["escenarios"] for p in e["preguntas"] if p["criterio_id"] == criterio_id]
    assert asociadas

    assert client.delete(f"{CASES_URL}/{caso['id']}/rubric/criteria/{criterio_id}", headers=creador_headers).status_code == 204
    for pregunta_id in asociadas:
        pregunta = client.get(f"{CASES_URL}/{caso['id']}/questions/{pregunta_id}", headers=creador_headers).json()
        assert pregunta["criterio_id"] is None
