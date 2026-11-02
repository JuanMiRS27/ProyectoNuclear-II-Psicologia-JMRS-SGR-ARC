"""Módulo 2 por HTTP: autoría paso a paso, ciclo de vida, aislamiento entre docentes y persistencia."""

import uuid

import pytest
from sqlalchemy import text

from app.core.database import engine
from tests.cases_helpers import CASES_URL, NIVELES_API, codigos, contar_filas, crear_caso_completo, dsl_valido, publicar


def _crear(client, headers, url, cuerpo, esperado=201):
    respuesta = client.post(url, headers=headers, json=cuerpo)
    assert respuesta.status_code == esperado, respuesta.text
    return respuesta.json()


def _caso(client, headers, nombre="Caso de prueba", **extra):
    return _crear(client, headers, CASES_URL, {"nombre": nombre} | extra)


def _ordenes(elementos):
    return [e["orden"] for e in elementos]


@pytest.fixture
def borrador(client, creador_headers):
    return crear_caso_completo(client, creador_headers)


@pytest.fixture
def urls(borrador):
    base = f"{CASES_URL}/{borrador['id']}"
    escenario = borrador["escenarios"][0]
    pregunta = escenario["preguntas"][0]
    return {
        "base": base,
        "escenario": f"{base}/scenarios/{escenario['id']}",
        "pregunta": f"{base}/questions/{pregunta['id']}",
        "opcion": f"{base}/questions/{pregunta['id']}/options/{pregunta['opciones'][0]['id']}",
        "criterio": f"{base}/rubric/criteria/{borrador['rubrica']['criterios'][0]['id']}",
    }


# --- Autoría completa -----------------------------------------------------------------------------


def test_caso_construido_por_endpoints_individuales_se_publica(client, creador_headers):
    caso = _caso(client, creador_headers, desarrollo_situacional="Situación", historia="Historia", tiempo_estimado_min=30)
    base = f"{CASES_URL}/{caso['id']}"

    criterio_a = _crear(client, creador_headers, f"{base}/rubric", {"escala_max": "5", "criterios": []})
    assert criterio_a["escala_max"] == "5.00"
    criterio = _crear(
        client, creador_headers, f"{base}/rubric/criteria", {"descripcion": "Análisis", "peso": 100, "niveles": NIVELES_API}
    )
    escenario = _crear(client, creador_headers, f"{base}/scenarios", {"titulo": "Inicio", "contexto": "Contexto"})
    pregunta = _crear(
        client, creador_headers, f"{base}/scenarios/{escenario['id']}/questions", {"enunciado": "¿Qué ocurre?", "criterio_id": criterio["id"]}
    )
    for texto, correcta in (("Sí", True), ("No", False)):
        _crear(
            client,
            creador_headers,
            f"{base}/questions/{pregunta['id']}/options",
            {"texto": texto, "es_correcta": correcta, "justificacion": "Porque sí."},
        )

    assert client.post(f"{base}/validate", headers=creador_headers).json() == {"valido": True, "errores": []}
    assert publicar(client, creador_headers, caso["id"]).json()["estado"] == "PUBLICADO"


def test_consulta_individual_refleja_lo_guardado(client, creador_headers, borrador, urls):
    escenario = client.get(urls["escenario"], headers=creador_headers).json()
    pregunta = client.get(urls["pregunta"], headers=creador_headers).json()
    opciones = client.get(f"{urls['pregunta']}/options", headers=creador_headers).json()
    criterios = client.get(f"{urls['base']}/rubric/criteria", headers=creador_headers).json()
    rubrica = client.get(f"{urls['base']}/rubric", headers=creador_headers).json()

    assert escenario["titulo"] == "La primera votación"
    assert pregunta["escenario_id"] == escenario["id"]
    assert _ordenes(opciones) == [1, 2, 3]
    assert {c["peso"] for c in criterios} == {"60.00", "40.00"}
    assert rubrica["id"] == borrador["rubrica"]["id"]


def test_listados_de_contenido(client, creador_headers, borrador, urls):
    escenarios = client.get(f"{urls['base']}/scenarios", headers=creador_headers).json()
    preguntas = client.get(f"{urls['escenario']}/questions", headers=creador_headers).json()

    assert _ordenes(escenarios) == [1, 2]
    assert [p["id"] for p in preguntas] == [p["id"] for p in borrador["escenarios"][0]["preguntas"]]


# --- Órdenes --------------------------------------------------------------------------------------


@pytest.mark.parametrize("nivel", ["escenarios", "preguntas", "opciones"])
def test_reordenar_invierte_y_persiste(client, creador_headers, borrador, urls, nivel):
    url_lista = {
        "escenarios": f"{urls['base']}/scenarios",
        "preguntas": f"{urls['escenario']}/questions",
        "opciones": f"{urls['pregunta']}/options",
    }[nivel]
    actuales = [e["id"] for e in client.get(url_lista, headers=creador_headers).json()]

    respuesta = client.put(f"{url_lista}/order", headers=creador_headers, json={"ids": actuales[::-1]})

    assert respuesta.status_code == 200, respuesta.text
    releidos = client.get(url_lista, headers=creador_headers).json()
    assert [e["id"] for e in releidos] == actuales[::-1]
    assert _ordenes(releidos) == list(range(1, len(actuales) + 1))


@pytest.mark.parametrize("nivel", ["escenarios", "preguntas", "opciones"])
def test_reordenar_con_lista_incompleta_no_cambia_nada(client, creador_headers, urls, nivel):
    url_lista = {
        "escenarios": f"{urls['base']}/scenarios",
        "preguntas": f"{urls['escenario']}/questions",
        "opciones": f"{urls['pregunta']}/options",
    }[nivel]
    antes = client.get(url_lista, headers=creador_headers).json()

    respuesta = client.put(f"{url_lista}/order", headers=creador_headers, json={"ids": [antes[0]["id"]]})

    assert respuesta.status_code == 400
    assert client.get(url_lista, headers=creador_headers).json() == antes


def test_ids_repetidos_al_reordenar(client, creador_headers, urls):
    uno = str(uuid.uuid4())

    assert client.put(f"{urls['base']}/scenarios/order", headers=creador_headers, json={"ids": [uno, uno]}).status_code == 422


def test_orden_ocupado_es_conflicto_real_de_la_base(client, creador_headers, urls):
    respuesta = client.post(f"{urls['base']}/scenarios", headers=creador_headers, json={"titulo": "Otro", "orden": 1})

    assert respuesta.status_code == 409
    assert "orden" in respuesta.json()["detail"]


def test_eliminar_compacta_los_ordenes(client, creador_headers, urls):
    client.delete(urls["opcion"], headers=creador_headers)
    opciones = client.get(f"{urls['pregunta']}/options", headers=creador_headers).json()
    assert _ordenes(opciones) == [1, 2]

    client.delete(urls["escenario"], headers=creador_headers)
    escenarios = client.get(f"{urls['base']}/scenarios", headers=creador_headers).json()
    assert _ordenes(escenarios) == [1]


def test_eliminar_pregunta_compacta_y_arrastra_sus_opciones(client, creador_headers, urls):
    antes = contar_filas()

    assert client.delete(urls["pregunta"], headers=creador_headers).status_code == 204

    despues = contar_filas()
    assert despues["pregunta"] == antes["pregunta"] - 1
    assert despues["opcion"] == antes["opcion"] - 3
    assert _ordenes(client.get(f"{urls['escenario']}/questions", headers=creador_headers).json()) == [1]


# --- Restricciones de edición por estado ----------------------------------------------------------


OPERACIONES_DE_EDICION = [
    ("patch", "base", {"nombre": "Otro"}),
    ("delete", "base", None),
    ("post", "base", None),
    ("patch", "escenario", {"titulo": "Otro"}),
    ("delete", "escenario", None),
    ("patch", "pregunta", {"enunciado": "Otro"}),
    ("delete", "pregunta", None),
    ("patch", "opcion", {"texto": "Otro"}),
    ("delete", "opcion", None),
    ("patch", "criterio", {"peso": 10}),
    ("delete", "criterio", None),
]


@pytest.mark.parametrize("estado", ["PUBLICADO", "ARCHIVADO"])
@pytest.mark.parametrize(("metodo", "destino", "cuerpo"), OPERACIONES_DE_EDICION)
def test_caso_no_borrador_rechaza_toda_edicion(client, creador_headers, borrador, urls, estado, metodo, destino, cuerpo):
    assert publicar(client, creador_headers, borrador["id"]).status_code == 200
    if estado == "ARCHIVADO":
        client.post(f"{urls['base']}/archive", headers=creador_headers)
    url = f"{urls['base']}/scenarios" if metodo == "post" else urls[destino]
    cuerpo = {"titulo": "Nuevo"} if metodo == "post" else cuerpo
    antes = contar_filas()

    respuesta = client.request(metodo, url, headers=creador_headers, json=cuerpo)

    assert respuesta.status_code == 409
    assert contar_filas() == antes
    with engine.connect() as conn:
        assert conn.execute(text("SELECT estado::text FROM caso_estudio WHERE id = :id"), {"id": borrador["id"]}).scalar_one() == estado


def test_caso_publicado_sigue_siendo_legible_y_exportable(client, creador_headers, borrador, urls):
    publicar(client, creador_headers, borrador["id"])

    assert client.get(urls["base"], headers=creador_headers).status_code == 200
    assert client.get(f"{urls['base']}/dsl", headers=creador_headers).json()["caso"]["estado"] == "PUBLICADO"


# --- Aislamiento entre docentes y roles -----------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "destino"),
    [("get", "base"), ("get", "escenario"), ("get", "pregunta"), ("patch", "pregunta"), ("delete", "opcion"), ("patch", "criterio")],
)
def test_contenido_de_otro_docente_es_invisible(client, otro_creador_headers, urls, metodo, destino):
    cuerpo = {} if metodo != "delete" else None

    respuesta = client.request(metodo, urls[destino], headers=otro_creador_headers, json=cuerpo)

    assert respuesta.status_code == 404


def test_docente_con_permiso_revocado_conserva_solo_la_lectura(client, admin_headers, creador, creador_headers, borrador, urls):
    client.patch(f"/api/v1/users/{creador.id}/case-permission", headers=admin_headers, json={"puede_crear_casos": False})

    assert client.get(urls["base"], headers=creador_headers).status_code == 200
    assert client.get(f"{urls['base']}/dsl", headers=creador_headers).status_code == 200
    assert client.patch(urls["escenario"], headers=creador_headers, json={"titulo": "Otro"}).status_code == 403


@pytest.mark.parametrize(
    "ruta",
    ["/scenarios", "/rubric", "/rubric/criteria"],
)
def test_lectura_de_caso_inexistente_en_rutas_anidadas(client, creador_headers, ruta):
    assert client.get(f"{CASES_URL}/{uuid.uuid4()}{ruta}", headers=creador_headers).status_code == 404


@pytest.mark.parametrize(
    ("destino", "cuerpo"),
    [("escenario", {"titulo": "Otro"}), ("pregunta", {"enunciado": "Otro"}), ("criterio", {"peso": 10})],
)
def test_componente_inexistente_en_caso_propio(client, creador_headers, urls, destino, cuerpo):
    url = urls[destino].rsplit("/", 1)[0] + f"/{uuid.uuid4()}"

    assert client.patch(url, headers=creador_headers, json=cuerpo).status_code == 404


def test_identificador_mal_formado_es_error_de_validacion(client, creador_headers):
    assert client.get(f"{CASES_URL}/no-es-uuid", headers=creador_headers).status_code == 422


def test_componentes_nulos_se_rechazan_por_api(client, creador_headers, urls):
    assert client.patch(urls["escenario"], headers=creador_headers, json={"titulo": None}).status_code == 400
    assert client.patch(urls["pregunta"], headers=creador_headers, json={"enunciado": None}).status_code == 400
    assert client.patch(urls["opcion"], headers=creador_headers, json={"es_correcta": None}).status_code == 400
    assert client.patch(urls["criterio"], headers=creador_headers, json={"niveles": None}).status_code == 400


# --- Rúbrica --------------------------------------------------------------------------------------


def test_segunda_rubrica_es_conflicto(client, creador_headers, urls):
    respuesta = client.post(f"{urls['base']}/rubric", headers=creador_headers, json={"escala_max": 5})

    assert respuesta.status_code == 409


def test_caso_sin_rubrica_responde_404_al_consultarla_y_editarla(client, creador_headers):
    caso = _caso(client, creador_headers)
    url = f"{CASES_URL}/{caso['id']}/rubric"

    assert client.get(url, headers=creador_headers).status_code == 404
    assert client.patch(url, headers=creador_headers, json={"escala_max": 5}).status_code == 404
    assert client.post(f"{url}/criteria", headers=creador_headers, json={"descripcion": "x", "peso": 1}).status_code == 404


def test_actualizar_escala_y_criterio_persiste_decimales(client, creador_headers, urls):
    assert client.patch(f"{urls['base']}/rubric", headers=creador_headers, json={"escala_max": "4.50"}).json()["escala_max"] == "4.50"

    criterio = client.patch(urls["criterio"], headers=creador_headers, json={"descripcion": "Nueva", "niveles": NIVELES_API}).json()

    assert criterio["descripcion"] == "Nueva"
    assert criterio["niveles"][1] == {"nombre": "Aceptable", "valor": "3.00", "min_aciertos_pct": "50.00"}


def test_pesos_modificados_impiden_publicar_con_detalle(client, creador_headers, borrador, urls):
    client.patch(urls["criterio"], headers=creador_headers, json={"peso": 10})

    respuesta = publicar(client, creador_headers, borrador["id"])

    assert respuesta.status_code == 422
    assert "RUBRICA_PESOS_NO_SUMAN_100" in codigos(respuesta.json())
    assert client.get(urls["base"], headers=creador_headers).json()["estado"] == "BORRADOR"


def test_quitar_criterio_de_pregunta_la_deja_pendiente(client, creador_headers, borrador, urls):
    pregunta = client.patch(urls["pregunta"], headers=creador_headers, json={"criterio_id": None}).json()

    assert pregunta["criterio_id"] is None
    assert "PREGUNTA_SIN_CRITERIO" in codigos(client.post(f"{urls['base']}/validate", headers=creador_headers).json())


# --- CaseDSL ----------------------------------------------------------------------------------------


def test_exportar_e_importar_duplica_el_caso_para_otro_autor(client, creador_headers, otro_creador_headers, borrador):
    exportado = client.get(f"{CASES_URL}/{borrador['id']}/dsl", headers=creador_headers).json()

    copia = crear_caso_completo(client, otro_creador_headers, exportado)

    assert copia["id"] != borrador["id"]
    assert copia["autor_id"] != borrador["autor_id"]
    assert copia["estado"] == "BORRADOR"
    assert client.post(f"{CASES_URL}/{copia['id']}/validate", headers=otro_creador_headers).json()["valido"] is True


def test_dsl_invalido_no_deja_filas(client, creador_headers):
    documento = dsl_valido()
    documento["escenarios"][0]["preguntas"][0]["criterio"] = "INEXISTENTE"
    antes = contar_filas()

    respuesta = client.post(f"{CASES_URL}/full", headers=creador_headers, json=documento)

    assert respuesta.status_code == 422
    assert contar_filas() == antes


# --- Listados -----------------------------------------------------------------------------------------


def test_filtro_por_estado_y_paginacion(client, creador_headers):
    publicado = crear_caso_completo(client, creador_headers)
    publicar(client, creador_headers, publicado["id"])
    _caso(client, creador_headers, "Borrador 1")
    _caso(client, creador_headers, "Borrador 2")

    borradores = client.get(CASES_URL, headers=creador_headers, params={"estado": "BORRADOR", "limit": 1}).json()
    publicados = client.get(CASES_URL, headers=creador_headers, params={"estado": "PUBLICADO"}).json()

    assert (borradores["total"], len(borradores["items"])) == (2, 1)
    assert [c["id"] for c in publicados["items"]] == [publicado["id"]]
    assert "escenarios" not in publicados["items"][0]  # el listado es un resumen


def test_estado_inexistente_en_el_filtro(client, creador_headers):
    assert client.get(CASES_URL, headers=creador_headers, params={"estado": "OTRO"}).status_code == 422
