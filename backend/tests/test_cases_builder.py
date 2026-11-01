"""Patrón Builder: construcción completa, relaciones, estado inicial y transaccionalidad."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import insert, select

from app.core.exceptions import ConflictError
from app.modules.cases.builders import (
    CasoAcademico,
    ConstructorCaso,
    ConstructorCasoAcademico,
    ErrorConstruccionCaso,
    ServicioConstruccionCaso,
)
from app.modules.cases.dsl import CaseDSLDocument
from app.modules.cases.models import CasoEstudio, Escenario, EstadoCasoEnum
from app.modules.cases.repositories import CasoRepository
from app.modules.cases.services import CasoService
from tests.cases_helpers import CASES_URL, contar_filas, crear_caso_completo, dsl_valido

# --- Construcción completa vía API ------------------------------------------------


def test_full_case_is_built_with_all_relations(client, creador, creador_headers):
    caso = crear_caso_completo(client, creador_headers)

    assert caso["estado"] == "BORRADOR"
    assert caso["autor_id"] == str(creador.id)
    assert caso["tiempo_estimado_min"] == 45

    escenarios = caso["escenarios"]
    assert [(e["orden"], e["titulo"]) for e in escenarios] == [(1, "La primera votación"), (2, "La discusión posterior")]
    assert [len(e["preguntas"]) for e in escenarios] == [2, 1]
    assert all(e["caso_id"] == caso["id"] for e in escenarios)

    criterios = {c["descripcion"]: c for c in caso["rubrica"]["criterios"]}
    analisis = criterios["Análisis del fenómeno social"]["id"]
    decision = criterios["Toma de decisiones éticas"]["id"]
    assert [p["criterio_id"] for p in escenarios[0]["preguntas"]] == [analisis, decision]
    assert escenarios[1]["preguntas"][0]["criterio_id"] == analisis

    for escenario in escenarios:
        assert [p["orden"] for p in escenario["preguntas"]] == list(range(1, len(escenario["preguntas"]) + 1))
        for pregunta in escenario["preguntas"]:
            assert pregunta["escenario_id"] == escenario["id"]
            assert [o["orden"] for o in pregunta["opciones"]] == [1, 2, 3]
            assert [o["es_correcta"] for o in pregunta["opciones"]] == [True, False, False]
            assert all(o["justificacion"] for o in pregunta["opciones"])

    assert caso["rubrica"]["escala_max"] == "5.00"
    assert {c["peso"] for c in caso["rubrica"]["criterios"]} == {"60.00", "40.00"}
    assert contar_filas() == {
        "caso_estudio": 1,
        "escenario": 2,
        "pregunta": 3,
        "opcion": 9,
        "rubrica": 1,
        "criterio_rubrica": 2,
    }


def test_full_case_always_starts_as_draft(client, creador_headers):
    documento = dsl_valido()
    documento["caso"]["estado"] = "PUBLICADO"
    assert crear_caso_completo(client, creador_headers, documento)["estado"] == "BORRADOR"


def test_partial_structure_is_accepted_as_draft(client, creador_headers):
    documento = {"dslVersion": "1.0", "caso": {"nombre": "Solo el nombre"}, "escenarios": [{"titulo": "Sin preguntas"}]}
    caso = crear_caso_completo(client, creador_headers, documento)
    assert caso["rubrica"] is None and caso["escenarios"][0]["preguntas"] == []


@pytest.mark.parametrize(
    "mutar",
    [
        lambda d: d.update(dslVersion="2.0"),
        lambda d: d.update(campoDesconocido=1),
        lambda d: d["escenarios"][0]["preguntas"][0].update(criterio="NO_EXISTE"),
        lambda d: d["rubrica"]["criterios"][1].update(clave="ANALISIS"),
        lambda d: d["rubrica"]["criterios"][0].update(peso="12.345"),
        lambda d: d["caso"].update(tiempoEstimadoMin=-5),
        lambda d: d.pop("caso"),
    ],
    ids=["version", "campo-extra", "criterio-inexistente", "clave-repetida", "peso-3-decimales", "tiempo-negativo", "sin-caso"],
)
def test_structurally_invalid_dsl_is_rejected_without_persisting(client, creador_headers, mutar):
    documento = dsl_valido()
    mutar(documento)
    assert client.post(f"{CASES_URL}/full", headers=creador_headers, json=documento).status_code == 422
    assert set(contar_filas().values()) == {0}


# --- Builder sin base de datos --------------------------------------------------------


def _documento() -> CaseDSLDocument:
    return CaseDSLDocument.model_validate(dsl_valido())


def test_builder_produces_transient_aggregate_without_session():
    autor = uuid.uuid4()
    producto = ServicioConstruccionCaso(ConstructorCasoAcademico()).construir_desde_dsl(_documento(), autor)

    assert isinstance(producto, CasoAcademico)
    assert producto.caso.autor_id == autor
    assert producto.caso.estado == EstadoCasoEnum.BORRADOR
    assert len(producto.escenarios) == 2 and len(producto.preguntas) == 3 and len(producto.opciones) == 9
    assert producto.rubrica is not None and len(producto.rubrica.criterios) == 2
    # Las preguntas quedan enlazadas a los objetos criterio (aún sin id).
    assert all(p.criterio in producto.rubrica.criterios for p in producto.preguntas)
    assert producto.rubrica.criterios[0].niveles[1]["valor"] == Decimal("3.00")


def test_concrete_builder_implements_abstraction():
    assert issubclass(ConstructorCasoAcademico, ConstructorCaso)
    with pytest.raises(TypeError):
        ConstructorCaso()  # type: ignore[abstract]


def test_builder_enforces_construction_sequence():
    c = ConstructorCasoAcademico()
    with pytest.raises(ErrorConstruccionCaso):
        c.establecer_datos_generales("Sin autor", None, None, None)

    c.reiniciar(uuid.uuid4())
    with pytest.raises(ErrorConstruccionCaso, match="datos generales"):
        c.agregar_escenario("Antes de tiempo", None)

    c.establecer_datos_generales("Caso", None, None, None)
    with pytest.raises(ErrorConstruccionCaso, match="ya fueron establecidos"):
        c.establecer_datos_generales("Otra vez", None, None, None)
    with pytest.raises(ErrorConstruccionCaso, match="rúbrica antes"):
        c.agregar_criterio("C1", "Criterio", Decimal("100"), [])

    c.establecer_rubrica(Decimal("5"))
    with pytest.raises(ErrorConstruccionCaso, match="una rúbrica"):
        c.establecer_rubrica(Decimal("5"))


def test_builder_resolves_criteria_declared_after_questions():
    c = ConstructorCasoAcademico()
    c.reiniciar(uuid.uuid4())
    c.establecer_datos_generales("Caso", None, None, None)
    escenario = c.agregar_escenario("E1", None)
    pregunta = c.agregar_pregunta(escenario, "¿P1?", "TARDIO")
    c.agregar_opcion(pregunta, "A", True, "J")
    c.establecer_rubrica(Decimal("5"))
    c.agregar_criterio("TARDIO", "Declarado después", Decimal("100"), [])

    producto = c.construir()
    assert producto.preguntas[0].criterio is producto.rubrica.criterios[0]


def test_builder_rejects_unknown_criterion_key_and_bad_references():
    c = ConstructorCasoAcademico()
    c.reiniciar(uuid.uuid4())
    c.establecer_datos_generales("Caso", None, None, None)
    escenario = c.agregar_escenario("E1", None)
    c.agregar_pregunta(escenario, "¿P1?", "FANTASMA")
    with pytest.raises(ErrorConstruccionCaso, match="FANTASMA"):
        c.construir()

    from app.modules.cases.builders import ReferenciaEscenario

    c.reiniciar(uuid.uuid4())
    c.establecer_datos_generales("Caso", None, None, None)
    with pytest.raises(ErrorConstruccionCaso, match="escenario no es válida"):
        c.agregar_pregunta(ReferenciaEscenario(5), "¿?")


def test_builder_is_reset_after_build():
    c = ConstructorCasoAcademico()
    c.reiniciar(uuid.uuid4())
    c.establecer_datos_generales("Primero", None, None, None)
    primero = c.construir()
    with pytest.raises(ErrorConstruccionCaso):
        c.agregar_escenario("No debe afectar al producto anterior", None)
    assert primero.escenarios == []


# --- Transaccionalidad ----------------------------------------------------------------


def test_build_failure_rolls_back_everything(db, creador, monkeypatch):
    original_add = CasoRepository.add

    def add_then_fail(self, caso):
        original_add(self, caso)  # el flush ya insertó caso, escenarios, preguntas, opciones y rúbrica
        assert contar_filas_en_sesion(self._session) > 0
        raise RuntimeError("fallo simulado después de insertar")

    monkeypatch.setattr(CasoRepository, "add", add_then_fail)

    with pytest.raises(RuntimeError):
        CasoService(db).crear_completo(creador, _documento())

    assert set(contar_filas().values()) == {0}


def test_database_error_during_build_rolls_back_and_returns_conflict(db, creador, monkeypatch):
    original_add = CasoRepository.add

    def add_then_violate(self, caso):
        original_add(self, caso)
        # INSERT real que viola uq_escenario_caso_orden a mitad de la construcción.
        self._session.execute(insert(Escenario).values(caso_id=caso.id, orden=1, titulo="Duplicado"))

    monkeypatch.setattr(CasoRepository, "add", add_then_violate)

    with pytest.raises(ConflictError):
        CasoService(db).crear_completo(creador, _documento())

    assert set(contar_filas().values()) == {0}
    # La sesión sigue utilizable tras el rollback.
    monkeypatch.setattr(CasoRepository, "add", original_add)
    assert CasoService(db).crear_completo(creador, _documento()).id is not None


def test_no_orphans_after_successful_build(db, client, creador_headers):
    crear_caso_completo(client, creador_headers)
    huerfanos = db.execute(
        select(Escenario.id).outerjoin(CasoEstudio, Escenario.caso_id == CasoEstudio.id).where(CasoEstudio.id.is_(None))
    ).all()
    assert huerfanos == []


def contar_filas_en_sesion(session) -> int:
    return session.scalar(select(CasoEstudio.id).limit(1)) is not None
