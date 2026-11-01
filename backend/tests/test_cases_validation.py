"""Patrón Chain of Responsibility: validación previa a la publicación."""

import uuid
from decimal import Decimal

import pytest

from app.modules.cases.models import CasoEstudio, CriterioRubrica, Escenario, Opcion, Pregunta, Rubrica
from app.modules.cases.validators import (
    ValidadorDatosCaso,
    ValidadorEscenariosYPreguntas,
    ValidadorOpciones,
    ValidadorRubrica,
    construir_cadena_publicacion,
    validar_caso,
)
from tests.cases_helpers import CASES_URL, codigos, crear_caso_completo, dsl_valido, publicar


def validar_dsl(client, headers, documento) -> dict:
    caso = crear_caso_completo(client, headers, documento)
    response = client.post(f"{CASES_URL}/{caso['id']}/validate", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_valid_case_passes_and_can_be_published(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    validacion = client.post(f"{CASES_URL}/{caso['id']}/validate", headers=creador_headers).json()
    assert validacion == {"valido": True, "errores": []}
    response = publicar(client, creador_headers, caso["id"])
    assert response.status_code == 200 and response.json()["estado"] == "PUBLICADO"


def test_validate_does_not_change_state(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    client.post(f"{CASES_URL}/{caso['id']}/validate", headers=creador_headers)
    assert client.get(f"{CASES_URL}/{caso['id']}", headers=creador_headers).json()["estado"] == "BORRADOR"


def test_case_without_scenarios(client, creador_headers):
    documento = dsl_valido()
    documento["escenarios"] = []
    assert "CASO_SIN_ESCENARIOS" in codigos(validar_dsl(client, creador_headers, documento))


def test_scenario_without_questions(client, creador_headers):
    documento = dsl_valido()
    documento["escenarios"][1]["preguntas"] = []
    validacion = validar_dsl(client, creador_headers, documento)
    error = next(e for e in validacion["errores"] if e["codigo"] == "ESCENARIO_SIN_PREGUNTAS")
    assert error["componente"] == "escenario" and error["ruta"] == "escenarios[1].preguntas"
    assert error["componente_id"] is not None


def test_question_with_insufficient_options(client, creador_headers):
    documento = dsl_valido()
    documento["escenarios"][0]["preguntas"][0]["opciones"] = documento["escenarios"][0]["preguntas"][0]["opciones"][:1]
    assert "PREGUNTA_OPCIONES_INSUFICIENTES" in codigos(validar_dsl(client, creador_headers, documento))


def test_question_without_correct_option(client, creador_headers):
    documento = dsl_valido()
    for opcion in documento["escenarios"][0]["preguntas"][1]["opciones"]:
        opcion["esCorrecta"] = False
    validacion = validar_dsl(client, creador_headers, documento)
    error = next(e for e in validacion["errores"] if e["codigo"] == "PREGUNTA_SIN_OPCION_CORRECTA")
    assert error["ruta"] == "escenarios[0].preguntas[1].opciones"


def test_question_with_all_options_correct_is_allowed(client, creador_headers):
    """Regla retirada (PREGUNTA_SIN_OPCION_INCORRECTA): no se exige una opción incorrecta."""
    documento = dsl_valido()
    for opcion in documento["escenarios"][0]["preguntas"][0]["opciones"]:
        opcion["esCorrecta"] = True
    caso = crear_caso_completo(client, creador_headers, documento)
    assert client.post(f"{CASES_URL}/{caso['id']}/validate", headers=creador_headers).json()["valido"] is True
    assert publicar(client, creador_headers, caso["id"]).status_code == 200


def test_several_correct_options_are_allowed(client, creador_headers):
    documento = dsl_valido()
    documento["escenarios"][0]["preguntas"][0]["opciones"][1]["esCorrecta"] = True
    assert validar_dsl(client, creador_headers, documento)["valido"] is True


def test_missing_justifications_are_reported_individually(client, creador_headers):
    documento = dsl_valido()
    opciones = documento["escenarios"][1]["preguntas"][0]["opciones"]
    opciones[0]["justificacion"] = None
    opciones[2]["justificacion"] = "   "
    validacion = validar_dsl(client, creador_headers, documento)
    errores = [e for e in validacion["errores"] if e["codigo"] == "OPCION_SIN_JUSTIFICACION"]
    assert [e["ruta"] for e in errores] == [
        "escenarios[1].preguntas[0].opciones[0].justificacion",
        "escenarios[1].preguntas[0].opciones[2].justificacion",
    ]
    assert all(e["componente"] == "opcion" and e["campo"] == "justificacion" for e in errores)


def test_duplicate_option_text_is_not_a_publication_error(client, creador_headers):
    """Regla retirada (OPCION_TEXTO_DUPLICADO): no figuraba en los requisitos."""
    documento = dsl_valido()
    opciones = documento["escenarios"][0]["preguntas"][0]["opciones"]
    opciones[2]["texto"] = opciones[1]["texto"]
    assert validar_dsl(client, creador_headers, documento)["valido"] is True


def test_minimum_two_options_rule_is_kept(client, creador_headers):
    documento = dsl_valido()
    documento["escenarios"][0]["preguntas"][0]["opciones"] = [
        {"texto": "Única", "esCorrecta": True, "justificacion": "J"}
    ]
    found = codigos(validar_dsl(client, creador_headers, documento))
    assert found == ["PREGUNTA_OPCIONES_INSUFICIENTES"]


def test_missing_rubric(client, creador_headers):
    documento = dsl_valido()
    documento["rubrica"] = None
    for escenario in documento["escenarios"]:
        for pregunta in escenario["preguntas"]:
            pregunta["criterio"] = None
    validacion = validar_dsl(client, creador_headers, documento)
    error = next(e for e in validacion["errores"] if e["codigo"] == "CASO_SIN_RUBRICA")
    assert error == {
        "codigo": "CASO_SIN_RUBRICA",
        "componente": "caso",
        "componente_id": error["componente_id"],
        "campo": "rubrica",
        "ruta": "rubrica",
        "mensaje": "El caso debe tener una rúbrica antes de publicarse.",
    }
    assert codigos(validacion).count("PREGUNTA_SIN_CRITERIO") == 3


def test_rubric_without_criteria(client, creador_headers):
    documento = dsl_valido()
    documento["rubrica"]["criterios"] = []
    for escenario in documento["escenarios"]:
        for pregunta in escenario["preguntas"]:
            pregunta["criterio"] = None
    assert "RUBRICA_SIN_CRITERIOS" in codigos(validar_dsl(client, creador_headers, documento))


@pytest.mark.parametrize(("pesos", "suma"), [(("50", "40"), "90.00"), (("60.01", "40"), "100.01")])
def test_weights_must_sum_100(client, creador_headers, pesos, suma):
    documento = dsl_valido()
    for criterio, peso in zip(documento["rubrica"]["criterios"], pesos):
        criterio["peso"] = peso
    validacion = validar_dsl(client, creador_headers, documento)
    error = next(e for e in validacion["errores"] if e["codigo"] == "RUBRICA_PESOS_NO_SUMAN_100")
    assert suma in error["mensaje"]


def test_decimal_weights_summing_exactly_100_are_valid(client, creador_headers):
    documento = dsl_valido()
    documento["rubrica"]["criterios"].append(
        {"clave": "EXTRA", "descripcion": "Comunicación", "peso": "33.34", "niveles": documento["rubrica"]["criterios"][0]["niveles"]}
    )
    documento["rubrica"]["criterios"][0]["peso"] = "33.33"
    documento["rubrica"]["criterios"][1]["peso"] = "33.33"
    documento["escenarios"][1]["preguntas"][0]["criterio"] = "EXTRA"
    # 33.33 + 33.33 + 33.34 en float sería 99.99999999999999; con Decimal es exactamente 100.
    assert validar_dsl(client, creador_headers, documento)["valido"] is True


def test_structural_level_rules_are_kept(client, creador_headers):
    documento = dsl_valido()
    documento["rubrica"]["criterios"][0]["niveles"] = [
        {"nombre": "Bajo", "minAciertosPct": 0, "valor": 1},
        {"nombre": "bajo", "minAciertosPct": 60, "valor": 3},
    ]
    documento["rubrica"]["criterios"][1]["niveles"] = []
    errores = validar_dsl(client, creador_headers, documento)["errores"]
    assert [(e["codigo"], e["ruta"]) for e in errores] == [
        ("NIVEL_NOMBRE_DUPLICADO", "rubrica.criterios[0].niveles"),
        ("CRITERIO_NIVELES_INSUFICIENTES", "rubrica.criterios[1].niveles"),
    ]


def test_publication_requires_at_least_two_levels(client, creador_headers):
    """Mínimo restablecido según el JSON Schema de CaseDSL del proyecto."""
    documento = dsl_valido()
    documento["rubrica"]["criterios"][1]["niveles"] = documento["rubrica"]["criterios"][1]["niveles"][:1]
    caso = crear_caso_completo(client, creador_headers, documento)  # el borrador se guarda con un solo nivel

    response = publicar(client, creador_headers, caso["id"])
    assert response.status_code == 422
    error = response.json()["errores"][0]
    assert response.json()["errores"] == [error]
    assert error["codigo"] == "CRITERIO_NIVELES_INSUFICIENTES"
    assert error["componente"] == "criterio" and error["campo"] == "niveles"
    assert "(tiene 1)" in error["mensaje"]
    assert client.get(f"{CASES_URL}/{caso['id']}", headers=creador_headers).json()["estado"] == "BORRADOR"

    # Al completar el segundo nivel en el borrador, se publica.
    criterio = next(c for c in caso["rubrica"]["criterios"] if len(c["niveles"]) == 1)
    niveles = criterio["niveles"] + [{"nombre": "Logrado", "valor": "5.00"}]
    url = f"{CASES_URL}/{caso['id']}/rubric/criteria/{criterio['id']}"
    assert client.patch(url, headers=creador_headers, json={"niveles": niveles}).status_code == 200
    assert publicar(client, creador_headers, caso["id"]).status_code == 200


def test_drafts_can_keep_fewer_than_two_levels(client, creador_headers):
    caso = client.post(CASES_URL, headers=creador_headers, json={"nombre": "Borrador"}).json()
    rubrica = client.post(
        f"{CASES_URL}/{caso['id']}/rubric",
        headers=creador_headers,
        json={"escala_max": "5", "criterios": [
            {"descripcion": "Sin niveles", "peso": "50"},
            {"descripcion": "Un nivel", "peso": "50", "niveles": [{"nombre": "Único", "valor": "5"}]},
        ]},
    )
    assert rubrica.status_code == 201
    assert sorted(len(c["niveles"]) for c in rubrica.json()["criterios"]) == [0, 1]


def test_grading_level_rules_are_postponed_to_module_5(client, creador_headers):
    """Porcentajes repetidos, sin nivel 0, valores sobre la escala o no monótonos:
    son decisiones de calificación (módulo 5) y no impiden publicar."""
    documento = dsl_valido()
    documento["rubrica"]["criterios"][0]["niveles"] = [
        {"nombre": "Bajo", "minAciertosPct": 10, "valor": 4},
        {"nombre": "Medio", "minAciertosPct": 60, "valor": 2},
        {"nombre": "Alto", "minAciertosPct": 60, "valor": 9},
    ]
    caso = crear_caso_completo(client, creador_headers, documento)
    assert client.post(f"{CASES_URL}/{caso['id']}/validate", headers=creador_headers).json() == {"valido": True, "errores": []}
    assert publicar(client, creador_headers, caso["id"]).status_code == 200


def test_min_aciertos_pct_is_optional(client, creador_headers):
    """Solo `nombre` y `valor` son obligatorios; `minAciertosPct` puede omitirse."""
    documento = dsl_valido()
    for criterio in documento["rubrica"]["criterios"]:
        criterio["niveles"] = [{"nombre": "Inicial", "valor": 2}, {"nombre": "Logrado", "valor": 5}]
    caso = crear_caso_completo(client, creador_headers, documento)

    niveles = caso["rubrica"]["criterios"][0]["niveles"]
    assert niveles == [
        {"nombre": "Inicial", "valor": "2.00", "min_aciertos_pct": None},
        {"nombre": "Logrado", "valor": "5.00", "min_aciertos_pct": None},
    ]
    assert publicar(client, creador_headers, caso["id"]).status_code == 200

    dsl = client.get(f"{CASES_URL}/{caso['id']}/dsl", headers=creador_headers).json()
    assert dsl["rubrica"]["criterios"][0]["niveles"][0] == {"nombre": "Inicial", "valor": "2.00", "minAciertosPct": None}


@pytest.mark.parametrize(
    "nivel",
    [
        {"valor": 1},  # falta nombre
        {"nombre": "Sin valor"},  # falta valor
        {"nombre": "", "valor": 1},
        {"nombre": "A", "valor": -1},
        {"nombre": "A", "valor": "1.234"},
        {"nombre": "A", "valor": 1, "minAciertosPct": 101},
        {"nombre": "A", "valor": 1, "umbralAciertos": 50},  # nombre de campo anterior: ya no existe
    ],
    ids=["sin-nombre", "sin-valor", "nombre-vacio", "valor-negativo", "valor-3-decimales", "pct-mayor-100", "campo-desconocido"],
)
def test_level_structure_is_enforced_by_schema(client, creador_headers, nivel):
    documento = dsl_valido()
    documento["rubrica"]["criterios"][0]["niveles"][0] = nivel
    assert client.post(f"{CASES_URL}/full", headers=creador_headers, json=documento).status_code == 422


def test_dsl_json_schema_level_required_fields():
    """El JSON Schema generado para CaseDSL exige exactamente `nombre` y `valor` en cada nivel."""
    from app.modules.cases.dsl import CaseDSLDocument

    esquema = CaseDSLDocument.model_json_schema(by_alias=True)
    nivel = esquema["$defs"]["NivelDsl"]
    assert sorted(nivel["required"]) == ["nombre", "valor"]
    assert set(nivel["properties"]) == {"nombre", "valor", "minAciertosPct"}
    assert nivel["additionalProperties"] is False


def test_question_without_criterion(client, creador_headers):
    documento = dsl_valido()
    documento["escenarios"][0]["preguntas"][1]["criterio"] = None  # era la única de DECISION
    errores = validar_dsl(client, creador_headers, documento)["errores"]
    # Solo se informa la pregunta; el criterio DECISION sin preguntas ya no es un error.
    assert [(e["codigo"], e["ruta"]) for e in errores] == [
        ("PREGUNTA_SIN_CRITERIO", "escenarios[0].preguntas[1].criterio_id")
    ]


def test_case_with_criterion_without_questions_can_be_published(client, creador_headers):
    """Regla retirada (CRITERIO_SIN_PREGUNTAS)."""
    documento = dsl_valido()
    documento["rubrica"]["criterios"].append(
        {"clave": "SIN_PREGUNTAS", "descripcion": "Comunicación asertiva", "peso": 20, "niveles": documento["rubrica"]["criterios"][0]["niveles"]}
    )
    documento["rubrica"]["criterios"][0]["peso"] = 50
    documento["rubrica"]["criterios"][1]["peso"] = 30
    caso = crear_caso_completo(client, creador_headers, documento)

    assert client.post(f"{CASES_URL}/{caso['id']}/validate", headers=creador_headers).json() == {"valido": True, "errores": []}
    response = publicar(client, creador_headers, caso["id"])
    assert response.status_code == 200 and response.json()["estado"] == "PUBLICADO"

    criterios = {c["descripcion"]: c["id"] for c in response.json()["rubrica"]["criterios"]}
    usados = {p["criterio_id"] for e in response.json()["escenarios"] for p in e["preguntas"]}
    assert criterios["Comunicación asertiva"] not in usados


def test_general_data_rules(client, creador_headers):
    documento = dsl_valido()
    documento["caso"].update(desarrolloSituacional=None, historia="", tiempoEstimadoMin=None)
    found = codigos(validar_dsl(client, creador_headers, documento))
    assert {"CASO_DESARROLLO_SITUACIONAL_VACIO", "CASO_HISTORIA_VACIA", "CASO_TIEMPO_REQUERIDO"} <= set(found)


@pytest.mark.parametrize("tiempo", [1, 240])
def test_time_limits_are_publishable(client, creador_headers, tiempo):
    """1 minuto con 3 preguntas ya no es error: se retiró CASO_TIEMPO_INSUFICIENTE."""
    documento = dsl_valido()
    documento["caso"]["tiempoEstimadoMin"] = tiempo
    caso = crear_caso_completo(client, creador_headers, documento)
    assert publicar(client, creador_headers, caso["id"]).status_code == 200


@pytest.mark.parametrize("tiempo", [0, 241, 480])
def test_time_out_of_range_is_rejected_on_input(client, creador_headers, tiempo):
    documento = dsl_valido()
    documento["caso"]["tiempoEstimadoMin"] = tiempo
    assert client.post(f"{CASES_URL}/full", headers=creador_headers, json=documento).status_code == 422
    assert client.post(CASES_URL, headers=creador_headers, json={"nombre": "X", "tiempo_estimado_min": tiempo}).status_code == 422
    caso = client.post(CASES_URL, headers=creador_headers, json={"nombre": "X"}).json()
    assert client.patch(f"{CASES_URL}/{caso['id']}", headers=creador_headers, json={"tiempo_estimado_min": tiempo}).status_code == 422


def test_validator_detects_time_out_of_range_on_aggregate():
    """La cadena no depende de que la entrada ya se haya validado."""
    caso = CasoEstudio(id=uuid.uuid4(), nombre="X", desarrollo_situacional="D", historia="H", tiempo_estimado_min=300)
    assert "CASO_TIEMPO_FUERA_DE_RANGO" in validar_caso(caso).codigos
    caso.tiempo_estimado_min = 240
    assert "CASO_TIEMPO_FUERA_DE_RANGO" not in validar_caso(caso).codigos


def test_errors_from_all_validators_are_accumulated(client, creador_headers):
    documento = dsl_valido()
    documento["caso"]["historia"] = None  # ValidadorDatosCaso
    documento["escenarios"][1]["contexto"] = None  # ValidadorEscenariosYPreguntas
    documento["escenarios"][0]["preguntas"][0]["opciones"][1]["justificacion"] = None  # ValidadorOpciones
    documento["rubrica"]["criterios"][0]["peso"] = 10  # ValidadorRubrica
    validacion = validar_dsl(client, creador_headers, documento)

    assert validacion["valido"] is False
    assert codigos(validacion) == [
        "CASO_HISTORIA_VACIA",
        "ESCENARIO_CONTEXTO_VACIO",
        "OPCION_SIN_JUSTIFICACION",
        "RUBRICA_PESOS_NO_SUMAN_100",
    ]


def test_order_gaps_are_detected(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    # Un orden explícito deja un hueco (1, 2, 5).
    client.post(f"{CASES_URL}/{caso['id']}/scenarios", headers=creador_headers, json={"titulo": "Hueco", "orden": 5})
    validacion = client.post(f"{CASES_URL}/{caso['id']}/validate", headers=creador_headers).json()
    assert "ESCENARIO_ORDEN_INVALIDO" in codigos(validacion)


# --- Cadena sin base de datos -----------------------------------------------------------


def test_chain_is_linked_in_expected_order():
    eslabon = construir_cadena_publicacion()
    tipos = []
    while eslabon is not None:
        tipos.append(type(eslabon))
        eslabon = eslabon._siguiente
    assert tipos == [ValidadorDatosCaso, ValidadorEscenariosYPreguntas, ValidadorOpciones, ValidadorRubrica]


def test_foreign_criterion_is_detected_on_transient_aggregate():
    """La BD lo impide con un disparador; la cadena también lo detecta por sí misma."""
    criterio = CriterioRubrica(id=uuid.uuid4(), descripcion="Propio", peso=Decimal("100"), niveles=[])
    pregunta = Pregunta(
        id=uuid.uuid4(),
        orden=1,
        enunciado="¿?",
        criterio_id=uuid.uuid4(),  # criterio de otra rúbrica
        opciones=[
            Opcion(id=uuid.uuid4(), orden=1, texto="A", es_correcta=True, justificacion="J"),
            Opcion(id=uuid.uuid4(), orden=2, texto="B", es_correcta=False, justificacion="J"),
        ],
    )
    caso = CasoEstudio(
        id=uuid.uuid4(),
        nombre="Transitorio",
        desarrollo_situacional="D",
        historia="H",
        tiempo_estimado_min=10,
        escenarios=[Escenario(id=uuid.uuid4(), orden=1, titulo="E", contexto="C", preguntas=[pregunta])],
        rubrica=Rubrica(id=uuid.uuid4(), escala_max=Decimal("5"), criterios=[criterio]),
    )
    resultado = validar_caso(caso)
    assert "PREGUNTA_CRITERIO_AJENO" in resultado.codigos
    error = next(e for e in resultado.errores if e.codigo == "PREGUNTA_CRITERIO_AJENO")
    assert error.componente_id == pregunta.id and error.campo == "criterio_id"
