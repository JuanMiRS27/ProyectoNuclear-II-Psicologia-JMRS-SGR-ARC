"""Integridad en PostgreSQL, migración 0003 y CaseDSL."""

import ast
import uuid
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.core.database import engine
from app.modules.cases.models import CriterioRubrica
from tests.cases_helpers import CASES_URL, TABLAS_MODULO_2, contar_filas, crear_caso_completo, dsl_valido
from tests.conftest import PROJECT_ROOT, alembic_config


def ejecutar(sql: str, **params):
    with engine.begin() as conn:
        return conn.execute(text(sql), params)


@pytest.fixture
def caso_bd(client, creador_headers) -> dict:
    """Caso completo creado por la API, para manipular la base directamente."""
    return crear_caso_completo(client, creador_headers)


# --- Migración ----------------------------------------------------------------------


def test_migration_creates_module_2_schema():
    inspector = inspect(engine)
    for tabla in TABLAS_MODULO_2:
        assert inspector.has_table(tabla), tabla

    columnas = {c["name"]: c for c in inspector.get_columns("criterio_rubrica")}
    assert str(columnas["peso"]["type"]) == "NUMERIC(5, 2)"
    assert columnas["niveles"]["type"].__class__.__name__ == "JSONB"
    assert str({c["name"]: c for c in inspector.get_columns("rubrica")}["escala_max"]["type"]) == "NUMERIC(4, 2)"

    with engine.connect() as conn:
        estados = conn.execute(text("SELECT unnest(enum_range(NULL::estado_caso))::text")).scalars().all()
        disparador = conn.execute(
            text("SELECT count(*) FROM pg_trigger WHERE tgname = 'trg_pregunta_criterio_mismo_caso'")
        ).scalar_one()
        diferibles = conn.execute(
            text("SELECT conname FROM pg_constraint WHERE condeferrable ORDER BY conname")
        ).scalars().all()
    assert estados == ["BORRADOR", "PUBLICADO", "ARCHIVADO"]
    assert disparador == 1
    assert diferibles == ["uq_escenario_caso_orden", "uq_opcion_pregunta_orden", "uq_pregunta_escenario_orden"]

    fks = {fk["referred_table"]: fk["options"].get("ondelete") for fk in inspector.get_foreign_keys("pregunta")}
    assert fks == {"escenario": "CASCADE", "criterio_rubrica": "SET NULL"}
    assert inspector.get_foreign_keys("caso_estudio")[0]["options"].get("ondelete") == "RESTRICT"


def test_time_constraint_accepts_domain_limits(caso_bd):
    ejecutar("UPDATE caso_estudio SET tiempo_estimado_min = 240")
    ejecutar("UPDATE caso_estudio SET tiempo_estimado_min = 1")


def test_migration_0004_refuses_to_run_with_cases_over_240(creador):
    """La 0004 no altera datos: si hay casos con más de 240 min, se detiene y lo indica."""
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "0003_casos")
    try:
        caso_id = ejecutar(
            "INSERT INTO caso_estudio (autor_id, nombre, tiempo_estimado_min) VALUES (:a, 'Largo', 300) RETURNING id",
            a=creador.id,
        ).scalar_one()
        engine.dispose()
        with pytest.raises(RuntimeError, match="tiempo_estimado_min > 240"):
            command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003_casos"
            assert conn.execute(text("SELECT tiempo_estimado_min FROM caso_estudio WHERE id = :id"), {"id": caso_id}).scalar_one() == 300
        ejecutar("UPDATE caso_estudio SET tiempo_estimado_min = 240 WHERE id = :id", id=caso_id)
    finally:
        engine.dispose()
        command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0004_tiempo_max_240"


def test_downgrade_to_0002_removes_module_2_and_keeps_users(make_user):
    usuario = make_user()
    config = alembic_config()
    engine.dispose()
    command.downgrade(config, "0002_version_token")
    try:
        inspector = inspect(engine)
        assert not any(inspector.has_table(t) for t in TABLAS_MODULO_2)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT to_regtype('estado_caso')")).scalar() is None
            assert conn.execute(text("SELECT count(*) FROM usuario WHERE id = :id"), {"id": usuario.id}).scalar_one() == 1
    finally:
        command.upgrade(config, "head")
    assert inspect(engine).has_table("caso_estudio")


# --- Integridad referencial y restricciones -------------------------------------------


def test_referential_integrity(caso_bd, creador):
    with pytest.raises(IntegrityError):
        ejecutar("INSERT INTO escenario (caso_id, orden, titulo) VALUES (:c, 1, 'X')", c=uuid.uuid4())
    with pytest.raises(IntegrityError):
        ejecutar("INSERT INTO caso_estudio (autor_id, nombre) VALUES (:a, 'X')", a=uuid.uuid4())
    # RESTRICT: no se puede borrar un usuario que es autor de casos.
    with pytest.raises(IntegrityError):
        ejecutar("DELETE FROM usuario WHERE id = :id", id=creador.id)


def test_unique_order_constraints(caso_bd):
    escenario = caso_bd["escenarios"][0]
    pregunta = escenario["preguntas"][0]
    with pytest.raises(IntegrityError, match="uq_escenario_caso_orden"):
        ejecutar("INSERT INTO escenario (caso_id, orden, titulo) VALUES (:c, 1, 'Dup')", c=caso_bd["id"])
    with pytest.raises(IntegrityError, match="uq_pregunta_escenario_orden"):
        ejecutar("INSERT INTO pregunta (escenario_id, orden, enunciado) VALUES (:e, 1, 'Dup')", e=escenario["id"])
    with pytest.raises(IntegrityError, match="uq_opcion_pregunta_orden"):
        ejecutar("INSERT INTO opcion (pregunta_id, orden, texto) VALUES (:p, 1, 'Dup')", p=pregunta["id"])
    # El mismo orden sí puede repetirse en otro escenario.
    ejecutar("INSERT INTO pregunta (escenario_id, orden, enunciado) VALUES (:e, 1, 'Ok')", e=_nuevo_escenario(caso_bd["id"]))


def _nuevo_escenario(caso_id) -> uuid.UUID:
    return ejecutar(
        "INSERT INTO escenario (caso_id, orden, titulo) VALUES (:c, 99, 'Nuevo') RETURNING id", c=caso_id
    ).scalar_one()


def test_one_rubric_per_case(caso_bd):
    with pytest.raises(IntegrityError, match="uq_rubrica_caso_id"):
        ejecutar("INSERT INTO rubrica (caso_id, escala_max) VALUES (:c, 5)", c=caso_bd["id"])


@pytest.mark.parametrize(
    ("sql", "restriccion"),
    [
        ("UPDATE criterio_rubrica SET peso = 0", "ck_criterio_rubrica_peso_en_rango"),
        ("UPDATE criterio_rubrica SET peso = 100.01", "ck_criterio_rubrica_peso_en_rango"),
        ("UPDATE criterio_rubrica SET niveles = '{}'::jsonb", "ck_criterio_rubrica_niveles_es_arreglo"),
        ("UPDATE rubrica SET escala_max = 0", "ck_rubrica_escala_positiva"),
        ("UPDATE caso_estudio SET tiempo_estimado_min = 0", "ck_caso_estudio_tiempo_en_rango"),
        ("UPDATE caso_estudio SET tiempo_estimado_min = 241", "ck_caso_estudio_tiempo_en_rango"),
        ("UPDATE escenario SET orden = 0", "ck_escenario_orden_positivo"),
        ("UPDATE opcion SET texto = '  '", "ck_opcion_texto_no_vacio"),
    ],
)
def test_check_constraints(caso_bd, sql, restriccion):
    with pytest.raises(IntegrityError, match=restriccion):
        ejecutar(sql)


def test_weight_out_of_numeric_precision_is_rejected(caso_bd):
    # NUMERIC(5,2) redondea a 2 decimales y no admite 1000.
    ejecutar("UPDATE criterio_rubrica SET peso = 33.335 WHERE descripcion LIKE 'Análisis%'")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT peso FROM criterio_rubrica WHERE descripcion LIKE 'Análisis%'")).scalar_one() == Decimal("33.34")
    with pytest.raises(Exception):
        ejecutar("UPDATE criterio_rubrica SET peso = 1000")


def test_trigger_blocks_criterion_from_another_case(client, creador_headers):
    caso_a = crear_caso_completo(client, creador_headers)
    caso_b = crear_caso_completo(client, creador_headers)
    with pytest.raises(IntegrityError) as info:
        ejecutar(
            "UPDATE pregunta SET criterio_id = :criterio WHERE id = :pregunta",
            criterio=caso_b["rubrica"]["criterios"][0]["id"],
            pregunta=caso_a["escenarios"][0]["preguntas"][0]["id"],
        )
    # El servicio traduce este nombre de restricción a un 400 legible.
    assert info.value.orig.diag.constraint_name == "ck_pregunta_criterio_mismo_caso"


def test_deleting_case_cascades_to_all_content(caso_bd):
    assert contar_filas()["opcion"] == 9
    ejecutar("DELETE FROM caso_estudio WHERE id = :id", id=caso_bd["id"])
    assert set(contar_filas().values()) == {0}


def test_draft_allows_incomplete_rows(creador):
    """Las restricciones no impiden guardar un borrador incompleto."""
    caso_id = ejecutar("INSERT INTO caso_estudio (autor_id, nombre) VALUES (:a, 'Mínimo') RETURNING id", a=creador.id).scalar_one()
    escenario_id = ejecutar("INSERT INTO escenario (caso_id, orden, titulo) VALUES (:c, 1, 'E') RETURNING id", c=caso_id).scalar_one()
    pregunta_id = ejecutar(
        "INSERT INTO pregunta (escenario_id, orden, enunciado) VALUES (:e, 1, '¿?') RETURNING id", e=escenario_id
    ).scalar_one()
    ejecutar("INSERT INTO opcion (pregunta_id, orden, texto) VALUES (:p, 1, 'Sin justificación')", p=pregunta_id)
    rubrica_id = ejecutar("INSERT INTO rubrica (caso_id, escala_max) VALUES (:c, 5) RETURNING id", c=caso_id).scalar_one()
    ejecutar("INSERT INTO criterio_rubrica (rubrica_id, descripcion, peso) VALUES (:r, 'Sin niveles', 10)", r=rubrica_id)


# --- Decimales y JSONB ----------------------------------------------------------------


def test_decimal_precision_and_jsonb_levels_roundtrip(db, client, creador_headers):
    documento = dsl_valido()
    documento["rubrica"]["escalaMax"] = "9.75"
    documento["rubrica"]["criterios"][0]["peso"] = "66.67"
    documento["rubrica"]["criterios"][1]["peso"] = "33.33"
    documento["rubrica"]["criterios"][0]["niveles"] = [
        {"nombre": "Bajo", "minAciertosPct": "0", "valor": "0.10"},
        {"nombre": "Medio", "minAciertosPct": "33.33", "valor": "4.85"},
        {"nombre": "Alto", "minAciertosPct": "66.67", "valor": "9.75"},
    ]
    caso = crear_caso_completo(client, creador_headers, documento)

    criterio = db.scalars(select(CriterioRubrica).where(CriterioRubrica.descripcion.like("Análisis%"))).one()
    assert criterio.peso == Decimal("66.67") and isinstance(criterio.peso, Decimal)
    assert [n["valor"] for n in criterio.niveles] == [Decimal("0.1"), Decimal("4.85"), Decimal("9.75")]
    assert all(isinstance(n["min_aciertos_pct"], Decimal) for n in criterio.niveles)

    # En JSONB se guardan como números consultables desde SQL.
    with engine.connect() as conn:
        tipo, valor = conn.execute(
            text("SELECT jsonb_typeof(niveles->1->'valor'), (niveles->1->>'valor')::numeric FROM criterio_rubrica WHERE id = :id"),
            {"id": criterio.id},
        ).one()
    assert tipo == "number" and valor == Decimal("4.85")

    # La API devuelve los decimales como texto con dos decimales.
    analisis = next(c for c in caso["rubrica"]["criterios"] if c["descripcion"].startswith("Análisis"))
    assert analisis["peso"] == "66.67"
    assert analisis["niveles"][0] == {"nombre": "Bajo", "min_aciertos_pct": "0.00", "valor": "0.10"}
    assert caso["rubrica"]["escala_max"] == "9.75"


# --- CaseDSL ---------------------------------------------------------------------------


def exportar(client, headers, caso_id) -> dict:
    response = client.get(f"{CASES_URL}/{caso_id}/dsl", headers=headers)
    assert response.status_code == 200
    return response.json()


def normalizar(documento: dict) -> dict:
    """Quita los campos solo informativos y sustituye claves de criterio por su descripción."""
    claves = {c["clave"]: c["descripcion"] for c in (documento.get("rubrica") or {}).get("criterios", [])}

    def dec(valor) -> str:
        return str(Decimal(str(valor)).quantize(Decimal("0.01")))

    rubrica = documento.get("rubrica")
    return {
        "dslVersion": documento["dslVersion"],
        "caso": {k: documento["caso"].get(k) for k in ("nombre", "desarrolloSituacional", "historia", "tiempoEstimadoMin")},
        "escenarios": [
            {
                "titulo": e["titulo"],
                "contexto": e.get("contexto"),
                "preguntas": [
                    {
                        "enunciado": p["enunciado"],
                        "criterio": claves.get(p.get("criterio")),
                        "opciones": [
                            {"texto": o["texto"], "esCorrecta": o.get("esCorrecta", False), "justificacion": o.get("justificacion")}
                            for o in p.get("opciones", [])
                        ],
                    }
                    for p in e.get("preguntas", [])
                ],
            }
            for e in documento.get("escenarios", [])
        ],
        "rubrica": None
        if rubrica is None
        else {
            "escalaMax": dec(rubrica["escalaMax"]),
            "criterios": sorted(
                (
                    {
                        "descripcion": c["descripcion"],
                        "peso": dec(c["peso"]),
                        "niveles": [
                            {"nombre": n["nombre"], "minAciertosPct": dec(n["minAciertosPct"]) if n.get("minAciertosPct") is not None else None, "valor": dec(n["valor"])}
                            for n in c["niveles"]
                        ],
                    }
                    for c in rubrica["criterios"]
                ),
                key=lambda c: c["descripcion"],
            ),
        },
    }


def test_dsl_export_format(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    dsl = exportar(client, creador_headers, caso["id"])

    assert dsl["dslVersion"] == "1.0"
    assert set(dsl) == {"dslVersion", "caso", "escenarios", "rubrica"}
    assert dsl["caso"]["id"] == caso["id"] and dsl["caso"]["estado"] == "BORRADOR"
    assert dsl["caso"]["tiempoEstimadoMin"] == 45
    assert dsl["rubrica"]["escalaMax"] == "5.00"
    claves = [c["clave"] for c in dsl["rubrica"]["criterios"]]
    assert claves == ["C1", "C2"]
    pregunta = dsl["escenarios"][0]["preguntas"][0]
    assert pregunta["criterio"] in claves
    assert set(pregunta["opciones"][0]) == {"id", "texto", "esCorrecta", "justificacion"}
    assert dsl["rubrica"]["criterios"][0]["niveles"][1] == {"nombre": "Aceptable", "minAciertosPct": "50.00", "valor": "3.00"}


def test_dsl_roundtrip_is_consistent_with_relational_model(client, creador_headers):
    original = dsl_valido()
    caso = crear_caso_completo(client, creador_headers, original)
    exportado = exportar(client, creador_headers, caso["id"])
    assert normalizar(exportado) == normalizar(original)

    # El documento exportado se puede volver a importar y produce un caso equivalente.
    copia = crear_caso_completo(client, creador_headers, exportado)
    assert copia["id"] != caso["id"]
    assert normalizar(exportar(client, creador_headers, copia["id"])) == normalizar(original)


def test_dsl_reflects_relational_edits(client, creador_headers):
    """No hay dos fuentes: la DSL se genera siempre desde PostgreSQL."""
    caso = crear_caso_completo(client, creador_headers)
    pregunta_id = caso["escenarios"][0]["preguntas"][0]["id"]
    client.patch(f"{CASES_URL}/{caso['id']}/questions/{pregunta_id}", headers=creador_headers, json={"enunciado": "¿Editada?"})
    dsl = exportar(client, creador_headers, caso["id"])
    assert dsl["escenarios"][0]["preguntas"][0]["enunciado"] == "¿Editada?"
    assert dsl["escenarios"][0]["preguntas"][0]["id"] == pregunta_id


def test_dsl_question_criterion_keys_match_relational_criterio_id(client, creador_headers):
    """Cada `pregunta.criterio` de la DSL apunta al criterio cuyo id es `pregunta.criterio_id`."""
    documento = dsl_valido()
    documento["rubrica"]["criterios"].append(
        {"clave": "SOLO", "descripcion": "Criterio sin preguntas", "peso": 10, "niveles": documento["rubrica"]["criterios"][0]["niveles"]}
    )
    documento["rubrica"]["criterios"][0]["peso"] = 50
    documento["escenarios"][1]["preguntas"][0]["criterio"] = None  # pregunta pendiente
    caso = crear_caso_completo(client, creador_headers, documento)
    dsl = exportar(client, creador_headers, caso["id"])

    id_por_clave = {c["clave"]: c["id"] for c in dsl["rubrica"]["criterios"]}
    criterio_relacional = {p["id"]: p["criterio_id"] for e in caso["escenarios"] for p in e["preguntas"]}
    for escenario in dsl["escenarios"]:
        for pregunta in escenario["preguntas"]:
            esperado = criterio_relacional[pregunta["id"]]
            assert (id_por_clave[pregunta["criterio"]] if pregunta["criterio"] else None) == esperado

    # El criterio sin preguntas se exporta igualmente y ninguna pregunta lo referencia.
    solo = next(c for c in dsl["rubrica"]["criterios"] if c["descripcion"] == "Criterio sin preguntas")
    assert all(p["criterio"] != solo["clave"] for e in dsl["escenarios"] for p in e["preguntas"])
    assert dsl["escenarios"][1]["preguntas"][0]["criterio"] is None


def test_dsl_exporter_refuses_inconsistent_criterion_reference():
    """Si una pregunta apuntara a un criterio ajeno (el disparador lo impide), no se exporta una DSL engañosa."""
    from app.modules.cases.dsl.exporter import InconsistenciaDslError, exportar_caso
    from app.modules.cases.models import CasoEstudio, EstadoCasoEnum, Escenario, Pregunta, Rubrica

    caso = CasoEstudio(
        id=uuid.uuid4(),
        nombre="Transitorio",
        estado=EstadoCasoEnum.BORRADOR,
        escenarios=[
            Escenario(id=uuid.uuid4(), orden=1, titulo="E", preguntas=[Pregunta(id=uuid.uuid4(), orden=1, enunciado="¿?", criterio_id=uuid.uuid4())])
        ],
        rubrica=Rubrica(id=uuid.uuid4(), escala_max=Decimal("5"), criterios=[]),
    )
    with pytest.raises(InconsistenciaDslError):
        exportar_caso(caso)


def test_dsl_export_of_incomplete_draft(client, creador_headers):
    caso = client.post(CASES_URL, headers=creador_headers, json={"nombre": "Vacío"}).json()
    dsl = exportar(client, creador_headers, caso["id"])
    assert dsl["escenarios"] == [] and dsl["rubrica"] is None and dsl["caso"]["historia"] is None


# --- Independencia de las migraciones ---------------------------------------------------


def _modulos_importados(arbol: ast.Module) -> set[str]:
    nombres = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres.add(nodo.module)
    return nombres


def test_migrations_do_not_import_application_code():
    """Una migración histórica no debe cambiar si cambia el código actual de la aplicación."""
    versiones = sorted((PROJECT_ROOT / "alembic" / "versions").glob("*.py"))
    assert len(versiones) >= 4
    for archivo in versiones:
        importados = _modulos_importados(ast.parse(archivo.read_text(encoding="utf-8")))
        assert not any(m == "app" or m.startswith("app.") for m in importados), archivo.name


def test_migration_0004_keeps_its_own_fixed_limit_and_head_matches_domain():
    from app.modules.cases.models import TIEMPO_MAXIMO_MIN

    arbol = ast.parse((PROJECT_ROOT / "alembic" / "versions" / "0004_tiempo_estimado_max_240.py").read_text(encoding="utf-8"))
    fijos = [
        nodo.value.value
        for nodo in arbol.body
        if isinstance(nodo, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "TIEMPO_MAXIMO_MIN" for t in nodo.targets)
        and isinstance(nodo.value, ast.Constant)
    ]
    assert fijos == [240]  # literal propio de la revisión histórica

    # El esquema en head coincide con la regla vigente del dominio.
    with engine.connect() as conn:
        definicion = conn.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_caso_estudio_tiempo_en_rango'")
        ).scalar_one()
    assert f"<= {TIEMPO_MAXIMO_MIN}" in definicion and ">= 1" in definicion
