"""Módulo 2: casos, escenarios, preguntas, opciones y rúbricas (pruebas unitarias, sin base de datos)."""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.auth.models import RolUsuario
from app.modules.cases.dsl import CaseDSLDocument, exportar_caso
from app.modules.cases.dsl.exporter import InconsistenciaDslError
from app.modules.cases.models import (
    CasoEstudio,
    CriterioRubrica,
    Escenario,
    EstadoCasoEnum,
    Opcion,
    Pregunta,
    Rubrica,
)
from app.modules.cases.models.caso import NivelesJSONB
from app.modules.cases.schemas import (
    CasoCreate,
    CasoUpdate,
    CriterioCreate,
    CriterioUpdate,
    EscenarioCreate,
    EscenarioUpdate,
    OpcionCreate,
    OpcionUpdate,
    PreguntaCreate,
    PreguntaUpdate,
    ReordenarRequest,
    RubricaCreate,
    RubricaUpdate,
)
from app.modules.cases.schemas.common import NivelSchema, blank_to_none
from app.modules.cases.services import CasoService, EscenarioService, OpcionService, PreguntaService, RubricaService
from app.modules.cases.services.base import ServicioCasoBase, _traducir
from app.modules.cases.states.estados import TransicionNoPermitidaError
from app.modules.cases.validators import validar_caso
from tests.unit.fabricas import caso_valido, error_integridad, sesion_bd, usuario

NIVEL = {"nombre": "Bajo", "valor": 1}


# --- Tipos numéricos ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "acepta"),
    [("0", False), ("0.01", True), ("100", True), ("100.00", True), ("100.01", False), ("-1", False), ("1.234", False)],
)
def test_limites_del_peso_de_un_criterio(valor, acepta):
    datos = {"descripcion": "Análisis", "peso": valor}
    if acepta:
        assert CriterioCreate(**datos).peso == Decimal(valor)
    else:
        with pytest.raises(ValidationError):
            CriterioCreate(**datos)


@pytest.mark.parametrize(
    ("valor", "acepta"), [("0", False), ("0.01", True), ("99.99", True), ("100", False), ("1.123", False)]
)
def test_limites_de_la_escala_maxima(valor, acepta):
    if acepta:
        assert RubricaUpdate(escala_max=valor).escala_max == Decimal(valor)
    else:
        with pytest.raises(ValidationError):
            RubricaUpdate(escala_max=valor)


@pytest.mark.parametrize("valor", ["NaN", "Infinity", "-Infinity", "abc", None])
def test_decimales_rechazan_valores_no_finitos(valor):
    with pytest.raises(ValidationError):
        RubricaUpdate(escala_max=valor)


def test_decimales_se_normalizan_a_dos_cifras():
    assert str(RubricaUpdate(escala_max=5).escala_max) == "5.00"
    assert str(NivelSchema(nombre="Alto", valor="4.5", min_aciertos_pct="80").min_aciertos_pct) == "80.00"


@pytest.mark.parametrize(
    "datos",
    [
        {"nombre": " ", "valor": 1},
        {"nombre": "x" * 81, "valor": 1},
        {"nombre": "Bajo", "valor": -1},
        {"nombre": "Bajo"},
        {"valor": 1},
        {"nombre": "Bajo", "valor": 1, "min_aciertos_pct": 101},
        {"nombre": "Bajo", "valor": 1, "min_aciertos_pct": -1},
        {"nombre": "Bajo", "valor": 1, "extra": 1},
    ],
)
def test_nivel_mal_formado(datos):
    with pytest.raises(ValidationError):
        NivelSchema(**datos)


def test_nivel_sin_porcentaje_es_valido():
    assert NivelSchema(**NIVEL).min_aciertos_pct is None


@pytest.mark.parametrize(("entrada", "esperado"), [(None, None), ("", None), ("texto", "texto")])
def test_cadena_vacia_equivale_a_nulo(entrada, esperado):
    assert blank_to_none(entrada) == esperado


# --- Esquemas de casos y contenido ----------------------------------------------------------


@pytest.mark.parametrize(
    ("campo", "valor", "acepta"),
    [
        ("nombre", "   ", False),
        ("nombre", "x" * 200, True),
        ("nombre", "x" * 201, False),
        ("tiempo_estimado_min", 0, False),
        ("tiempo_estimado_min", 1, True),
        ("tiempo_estimado_min", 240, True),
        ("tiempo_estimado_min", 241, False),
        ("historia", "x" * 20_000, True),
        ("historia", "x" * 20_001, False),
        ("estado", "PUBLICADO", False),
        ("autor_id", str(uuid.uuid4()), False),
    ],
)
def test_limites_de_creacion_de_caso(campo, valor, acepta):
    datos = {"nombre": "Caso"} | {campo: valor}
    if acepta:
        assert CasoCreate(**datos)
    else:
        with pytest.raises(ValidationError):
            CasoCreate(**datos)


@pytest.mark.parametrize("campo", ["desarrollo_situacional", "historia"])
def test_textos_en_blanco_del_borrador_se_guardan_como_nulo(campo):
    assert getattr(CasoCreate(nombre="Caso", **{campo: "   "}), campo) is None


@pytest.mark.parametrize("orden", [0, -1, 10_001])
def test_orden_explicito_fuera_de_rango(orden):
    with pytest.raises(ValidationError):
        EscenarioCreate(titulo="Inicio", orden=orden)


def test_orden_es_opcional_al_crear():
    assert EscenarioCreate(titulo="Inicio").orden is None
    assert PreguntaCreate(enunciado="¿Qué pasa?", orden=10_000).orden == 10_000


def test_opcion_por_defecto_no_es_correcta():
    assert OpcionCreate(texto="Opción").es_correcta is False


@pytest.mark.parametrize("texto", ["", "   ", "x" * 20_001])
def test_texto_de_opcion_y_enunciado_requeridos(texto):
    with pytest.raises(ValidationError):
        OpcionCreate(texto=texto)
    with pytest.raises(ValidationError):
        PreguntaCreate(enunciado=texto)


def test_reordenar_exige_ids_sin_repetir():
    uno = uuid.uuid4()

    assert ReordenarRequest(ids=[uno, uuid.uuid4()])
    with pytest.raises(ValidationError):
        ReordenarRequest(ids=[uno, uno])
    with pytest.raises(ValidationError):
        ReordenarRequest(ids=[])


def test_limite_de_niveles_y_criterios():
    niveles = [NIVEL] * 21
    criterio = {"descripcion": "Análisis", "peso": 1}

    assert CriterioCreate(**criterio, niveles=[NIVEL] * 20)
    with pytest.raises(ValidationError):
        CriterioCreate(**criterio, niveles=niveles)
    with pytest.raises(ValidationError):
        RubricaCreate(escala_max=5, criterios=[criterio] * 51)


# --- CaseDSL --------------------------------------------------------------------------------


def _dsl(**cambios) -> dict:
    return {
        "dslVersion": "1.0",
        "caso": {"nombre": "Caso"},
        "escenarios": [{"titulo": "Inicio", "preguntas": [{"enunciado": "¿Qué pasa?", "criterio": "A"}]}],
        "rubrica": {"escalaMax": 5, "criterios": [{"clave": "A", "descripcion": "Análisis", "peso": 100}]},
    } | cambios


def test_dsl_valido_usa_alias_camel_case():
    documento = CaseDSLDocument.model_validate(_dsl())

    assert documento.dsl_version == "1.0"
    assert documento.rubrica.escala_max == Decimal("5.00")


def test_dsl_acepta_nombres_python():
    documento = CaseDSLDocument.model_validate({"dsl_version": "1.0", "caso": {"nombre": "Caso", "tiempo_estimado_min": 10}})

    assert documento.caso.tiempo_estimado_min == 10


@pytest.mark.parametrize(
    "cambios",
    [
        {"dslVersion": "2.0"},
        {"caso": {"nombre": " "}},
        {"caso": {"nombre": "Caso", "estado": "OTRO"}},
        {"caso": {"nombre": "Caso", "tiempoEstimadoMin": 241}},
        {"desconocido": 1},
    ],
    ids=["version", "nombre-vacio", "estado-invalido", "tiempo-excedido", "campo-extra"],
)
def test_dsl_estructura_invalida(cambios):
    with pytest.raises(ValidationError):
        CaseDSLDocument.model_validate(_dsl(**cambios))


def test_dsl_rechaza_claves_de_criterio_repetidas():
    criterio = {"clave": "A", "descripcion": "Análisis", "peso": 50}

    with pytest.raises(ValidationError, match="repetidas"):
        CaseDSLDocument.model_validate(_dsl(rubrica={"escalaMax": 5, "criterios": [criterio, criterio]}))


@pytest.mark.parametrize("rubrica", [None, {"escalaMax": 5, "criterios": [{"clave": "B", "descripcion": "x", "peso": 1}]}])
def test_dsl_rechaza_criterios_inexistentes(rubrica):
    with pytest.raises(ValidationError, match="inexistentes"):
        CaseDSLDocument.model_validate(_dsl(rubrica=rubrica))


@pytest.mark.parametrize("clave", ["con espacio", "ñ", "", "x" * 51])
def test_dsl_formato_de_clave_de_criterio(clave):
    with pytest.raises(ValidationError):
        CaseDSLDocument.model_validate(
            _dsl(escenarios=[], rubrica={"escalaMax": 5, "criterios": [{"clave": clave, "descripcion": "x", "peso": 1}]})
        )


def test_exportacion_genera_claves_y_referencias_consistentes():
    caso = caso_valido()

    documento = exportar_caso(caso)

    criterios = {c.id: c.clave for c in documento.rubrica.criterios}
    assert list(criterios.values()) == ["C1", "C2"]
    assert [p.criterio for p in documento.escenarios[0].preguntas] == [criterios[p.criterio_id] for p in caso.preguntas]
    assert documento.caso.estado == "BORRADOR"


def test_exportacion_de_borrador_sin_rubrica_ni_criterios():
    caso = caso_valido()
    caso.rubrica = None
    for pregunta in caso.preguntas:
        pregunta.criterio_id = None

    documento = exportar_caso(caso)

    assert documento.rubrica is None
    assert {p.criterio for p in documento.escenarios[0].preguntas} == {None}


def test_exportacion_no_oculta_un_criterio_ajeno():
    caso = caso_valido()
    caso.preguntas[0].criterio_id = uuid.uuid4()

    with pytest.raises(InconsistenciaDslError):
        exportar_caso(caso)


# --- Modelo ---------------------------------------------------------------------------------


def test_preguntas_recorre_todos_los_escenarios_en_orden():
    caso = caso_valido()
    caso.escenarios.append(Escenario(orden=2, titulo="Fin", preguntas=[Pregunta(orden=1, enunciado="¿Y ahora?")]))

    assert [p.enunciado for p in caso.preguntas] == ["¿Qué se observa?", "¿Qué haría?", "¿Y ahora?"]


def test_niveles_jsonb_conservan_decimales_exactos():
    tipo = NivelesJSONB()
    niveles = [{"nombre": "Alto", "valor": Decimal("4.35"), "min_aciertos_pct": None}]

    guardado = tipo.process_bind_param(niveles, None)
    leido = tipo.process_result_value(guardado, None)

    assert isinstance(guardado[0]["valor"], float)
    assert leido == niveles
    assert isinstance(leido[0]["valor"], Decimal)


def test_niveles_jsonb_respetan_tipos_no_numericos():
    tipo = NivelesJSONB()
    datos = {"lista": [True, None, "texto"], "entero": 3}

    assert tipo.process_result_value(datos, None) == {"lista": [True, None, "texto"], "entero": Decimal("3")}
    assert tipo.process_bind_param(None, None) is None


# --- Cadena de validación: un error por regla ---------------------------------------------------


def _vaciar(objeto, campo, valor=None):
    setattr(objeto, campo, valor)


def _pregunta(caso, indice=0):
    return caso.escenarios[0].preguntas[indice]


def _opcion(caso, indice=0):
    return _pregunta(caso).opciones[indice]


def _criterio(caso, indice=0):
    return caso.rubrica.criterios[indice]


REGLAS = {
    "CASO_NOMBRE_VACIO": (lambda c: _vaciar(c, "nombre", "  "), "caso.nombre"),
    "CASO_DESARROLLO_SITUACIONAL_VACIO": (lambda c: _vaciar(c, "desarrollo_situacional"), "caso.desarrollo_situacional"),
    "CASO_HISTORIA_VACIA": (lambda c: _vaciar(c, "historia", ""), "caso.historia"),
    "CASO_TIEMPO_REQUERIDO": (lambda c: _vaciar(c, "tiempo_estimado_min"), "caso.tiempo_estimado_min"),
    "CASO_TIEMPO_FUERA_DE_RANGO": (lambda c: _vaciar(c, "tiempo_estimado_min", 241), "caso.tiempo_estimado_min"),
    "CASO_SIN_ESCENARIOS": (lambda c: _vaciar(c, "escenarios", []), "escenarios"),
    "ESCENARIO_ORDEN_INVALIDO": (lambda c: _vaciar(c.escenarios[0], "orden", 2), "escenarios"),
    "ESCENARIO_TITULO_VACIO": (lambda c: _vaciar(c.escenarios[0], "titulo", " "), "escenarios[0].titulo"),
    "ESCENARIO_CONTEXTO_VACIO": (lambda c: _vaciar(c.escenarios[0], "contexto"), "escenarios[0].contexto"),
    "ESCENARIO_SIN_PREGUNTAS": (lambda c: _vaciar(c.escenarios[0], "preguntas", []), "escenarios[0].preguntas"),
    "PREGUNTA_ORDEN_INVALIDO": (lambda c: _vaciar(_pregunta(c, 1), "orden", 5), "escenarios[0].preguntas"),
    "PREGUNTA_ENUNCIADO_VACIO": (lambda c: _vaciar(_pregunta(c), "enunciado", ""), "escenarios[0].preguntas[0].enunciado"),
    "PREGUNTA_OPCIONES_INSUFICIENTES": (
        lambda c: _vaciar(_pregunta(c), "opciones", _pregunta(c).opciones[:1]),
        "escenarios[0].preguntas[0].opciones",
    ),
    "PREGUNTA_SIN_OPCION_CORRECTA": (lambda c: _vaciar(_opcion(c), "es_correcta", False), "escenarios[0].preguntas[0].opciones"),
    "OPCION_TEXTO_VACIO": (lambda c: _vaciar(_opcion(c, 1), "texto", " "), "escenarios[0].preguntas[0].opciones[1].texto"),
    "OPCION_SIN_JUSTIFICACION": (
        lambda c: _vaciar(_opcion(c, 1), "justificacion"),
        "escenarios[0].preguntas[0].opciones[1].justificacion",
    ),
    "RUBRICA_ESCALA_INVALIDA": (lambda c: _vaciar(c.rubrica, "escala_max", Decimal("0")), "rubrica.escala_max"),
    "RUBRICA_PESOS_NO_SUMAN_100": (lambda c: _vaciar(_criterio(c), "peso", Decimal("59.99")), "rubrica.criterios"),
    "CRITERIO_DESCRIPCION_VACIA": (lambda c: _vaciar(_criterio(c), "descripcion", " "), "rubrica.criterios[0].descripcion"),
    "CRITERIO_NIVELES_FORMATO_INVALIDO": (
        lambda c: _vaciar(_criterio(c), "niveles", [{"nombre": "Sin valor"}]),
        "rubrica.criterios[0].niveles",
    ),
    "CRITERIO_NIVELES_INSUFICIENTES": (
        lambda c: _vaciar(_criterio(c), "niveles", _criterio(c).niveles[:1]),
        "rubrica.criterios[0].niveles",
    ),
    "NIVEL_NOMBRE_DUPLICADO": (
        lambda c: _vaciar(_criterio(c), "niveles", [NIVEL, NIVEL | {"nombre": "BAJO"}]),
        "rubrica.criterios[0].niveles",
    ),
    "PREGUNTA_SIN_CRITERIO": (lambda c: _vaciar(_pregunta(c), "criterio_id"), "escenarios[0].preguntas[0].criterio_id"),
    "PREGUNTA_CRITERIO_AJENO": (lambda c: _vaciar(_pregunta(c), "criterio_id", uuid.uuid4()), "escenarios[0].preguntas[0].criterio_id"),
}


def test_caso_completo_no_tiene_errores():
    assert validar_caso(caso_valido()).valido


@pytest.mark.parametrize("codigo", REGLAS)
def test_cada_regla_reporta_codigo_y_ruta(codigo):
    alterar, ruta = REGLAS[codigo]
    caso = caso_valido()
    alterar(caso)

    resultado = validar_caso(caso)

    assert not resultado.valido
    errores = [e for e in resultado.errores if e.codigo == codigo]
    assert len(errores) == 1
    assert errores[0].ruta == ruta


def test_regla_de_tiempo_valida_ambos_extremos():
    caso = caso_valido()
    caso.tiempo_estimado_min = 0
    assert "CASO_TIEMPO_FUERA_DE_RANGO" in validar_caso(caso).codigos
    caso.tiempo_estimado_min = 240
    assert validar_caso(caso).valido


@pytest.mark.parametrize("peso", [Decimal("0"), Decimal("-1"), Decimal("100.01"), None])
def test_peso_de_criterio_invalido(peso):
    caso = caso_valido()
    caso.rubrica.criterios = caso.rubrica.criterios[:1]
    caso.rubrica.criterios[0].peso = peso if peso is not None else Decimal("0")

    assert "CRITERIO_PESO_INVALIDO" in validar_caso(caso).codigos


def test_rubrica_ausente_reporta_tambien_las_preguntas():
    caso = caso_valido()
    caso.rubrica = None

    codigos = validar_caso(caso).codigos

    assert codigos.count("CASO_SIN_RUBRICA") == 1
    assert codigos.count("PREGUNTA_CRITERIO_AJENO") == 2


def test_rubrica_sin_criterios():
    caso = caso_valido()
    caso.rubrica.criterios = []

    assert "RUBRICA_SIN_CRITERIOS" in validar_caso(caso).codigos


def test_niveles_de_tipo_incorrecto_no_rompen_la_validacion():
    caso = caso_valido()
    caso.rubrica.criterios[0].niveles = ["no-es-un-objeto", 3]

    assert "CRITERIO_NIVELES_FORMATO_INVALIDO" in validar_caso(caso).codigos


def test_error_incluye_el_componente_afectado():
    caso = caso_valido()
    caso.preguntas[0].opciones[0].justificacion = None

    error = validar_caso(caso).errores[0]

    assert error.componente == "opcion"
    assert error.componente_id == caso.preguntas[0].opciones[0].id
    assert "opción 1" in error.mensaje


# --- Servicios (repositorios simulados) ---------------------------------------------------------


def _con_dobles(clase):
    servicio = clase(sesion_bd())
    servicio._casos = MagicMock()
    servicio._contenido = MagicMock()
    servicio._casos.get_owned.return_value = caso_valido()
    return servicio


@pytest.fixture
def autor():
    return usuario(puede_crear_casos=True)


@pytest.mark.parametrize(
    ("restriccion", "tipo"),
    [
        ("uq_escenario_caso_orden", ConflictError),
        ("uq_pregunta_escenario_orden", ConflictError),
        ("uq_opcion_pregunta_orden", ConflictError),
        ("uq_rubrica_caso_id", ConflictError),
        ("ck_pregunta_criterio_mismo_caso", BusinessRuleError),
        ("fk_pregunta_criterio_id_criterio_rubrica", BusinessRuleError),
        ("otra_restriccion", BusinessRuleError),
        (None, BusinessRuleError),
    ],
)
def test_traduccion_de_restricciones(restriccion, tipo):
    assert type(_traducir(error_integridad(restriccion))) is tipo


def test_transaccion_revierte_ante_integridad_y_error_generico(autor):
    servicio = _con_dobles(ServicioCasoBase)

    with pytest.raises(ConflictError):
        with servicio._transaction():
            raise error_integridad("uq_rubrica_caso_id")
    with pytest.raises(ValueError):
        with servicio._transaction():
            raise ValueError

    assert servicio._session.rollback.call_count == 2
    servicio._session.commit.assert_not_called()


@pytest.mark.parametrize("for_update", [False, True])
def test_caso_ajeno_responde_igual_que_inexistente(autor, for_update):
    servicio = _con_dobles(ServicioCasoBase)
    servicio._casos.get_owned.return_value = None

    with pytest.raises(NotFoundError, match="Caso no encontrado"):
        (servicio._caso_bloqueado if for_update else servicio._caso_lectura)(uuid.uuid4(), autor)


def test_escritura_bloquea_la_fila_del_caso(autor):
    servicio = _con_dobles(ServicioCasoBase)
    caso_id = uuid.uuid4()

    servicio._caso_editable(caso_id, autor)

    servicio._casos.get_owned.assert_called_once_with(caso_id, autor.id, for_update=True, cargar_contenido=False)


@pytest.mark.parametrize("estado", [EstadoCasoEnum.PUBLICADO, EstadoCasoEnum.ARCHIVADO])
def test_caso_no_borrador_no_se_edita(autor, estado):
    servicio = _con_dobles(ServicioCasoBase)
    servicio._casos.get_owned.return_value = caso_valido(estado)

    with pytest.raises(TransicionNoPermitidaError):
        servicio._caso_editable(uuid.uuid4(), autor)


def test_reordenar_exige_exactamente_los_elementos_actuales(autor):
    servicio = _con_dobles(ServicioCasoBase)
    actuales = [Escenario(id=uuid.uuid4(), orden=1), Escenario(id=uuid.uuid4(), orden=2)]
    otro = uuid.uuid4()

    for ids in ([actuales[0].id], [actuales[0].id, otro]):
        with pytest.raises(BusinessRuleError):
            servicio._reordenar(actuales, ids)
    servicio._contenido.aplicar_orden.assert_not_called()

    servicio._reordenar(actuales, [actuales[1].id, actuales[0].id])
    servicio._contenido.aplicar_orden.assert_called_once_with([actuales[1], actuales[0]])


def test_compactar_conserva_el_orden_relativo():
    servicio = _con_dobles(ServicioCasoBase)
    a, b, c = Escenario(orden=5), Escenario(orden=2), Escenario(orden=9)

    servicio._compactar([a, b, c])

    servicio._contenido.aplicar_orden.assert_called_once_with([b, a, c])


def test_crear_borrador_toma_el_autor_del_usuario_autenticado(autor):
    servicio = _con_dobles(CasoService)

    servicio.crear_borrador(autor, CasoCreate(nombre="Caso"))

    creado = servicio._casos.add.call_args.args[0]
    assert (creado.autor_id, creado.estado) == (autor.id, EstadoCasoEnum.BORRADOR)
    servicio._session.commit.assert_called_once()


def test_actualizar_caso_rechaza_nombre_nulo_antes_de_bloquear(autor):
    servicio = _con_dobles(CasoService)

    with pytest.raises(BusinessRuleError):
        servicio.actualizar(autor, uuid.uuid4(), CasoUpdate(nombre=None))

    servicio._casos.get_owned.assert_not_called()


def test_actualizar_caso_permite_vaciar_campos_opcionales(autor):
    servicio = _con_dobles(CasoService)
    caso = servicio._casos.get_owned.return_value

    servicio.actualizar(autor, caso.id, CasoUpdate(historia=None))

    assert caso.historia is None
    assert caso.nombre == "Conformidad en el aula"


@pytest.mark.parametrize("estado", [EstadoCasoEnum.PUBLICADO, EstadoCasoEnum.ARCHIVADO])
def test_solo_se_eliminan_borradores(autor, estado):
    servicio = _con_dobles(CasoService)
    servicio._casos.get_owned.return_value = caso_valido(estado)

    with pytest.raises(TransicionNoPermitidaError):
        servicio.eliminar(autor, uuid.uuid4())

    servicio._casos.delete.assert_not_called()
    servicio._session.rollback.assert_called_once()


def test_listar_filtra_por_autor(autor):
    servicio = _con_dobles(CasoService)

    servicio.listar(autor, estado=EstadoCasoEnum.PUBLICADO, skip=1, limit=2)

    servicio._casos.list_by_author.assert_called_once_with(autor.id, estado=EstadoCasoEnum.PUBLICADO, skip=1, limit=2)


def test_validar_no_confirma_nada(autor):
    servicio = _con_dobles(CasoService)

    assert servicio.validar(autor, uuid.uuid4()).valido

    servicio._session.commit.assert_not_called()


def test_crear_escenario_al_final_si_no_se_indica_orden(autor):
    servicio = _con_dobles(EscenarioService)
    servicio._contenido.next_orden_escenario.return_value = 4

    servicio.crear(autor, uuid.uuid4(), EscenarioCreate(titulo="Inicio"))

    assert servicio._contenido.add.call_args.args[0].orden == 4


def test_crear_escenario_con_orden_explicito(autor):
    servicio = _con_dobles(EscenarioService)

    servicio.crear(autor, uuid.uuid4(), EscenarioCreate(titulo="Inicio", orden=2))

    assert servicio._contenido.add.call_args.args[0].orden == 2
    servicio._contenido.next_orden_escenario.assert_not_called()


@pytest.mark.parametrize(
    ("clase", "metodo", "argumentos", "esquema"),
    [
        (EscenarioService, "actualizar", 2, EscenarioUpdate(titulo=None)),
        (PreguntaService, "actualizar", 2, PreguntaUpdate(enunciado=None)),
        (OpcionService, "actualizar", 3, OpcionUpdate(texto=None)),
        (OpcionService, "actualizar", 3, OpcionUpdate(es_correcta=None)),
    ],
)
def test_campos_obligatorios_no_se_vacian(autor, clase, metodo, argumentos, esquema):
    servicio = _con_dobles(clase)

    with pytest.raises(BusinessRuleError):
        getattr(servicio, metodo)(autor, *[uuid.uuid4() for _ in range(argumentos)], esquema)

    servicio._session.commit.assert_not_called()


@pytest.mark.parametrize(
    ("clase", "metodo", "faltante", "n_ids"),
    [
        (EscenarioService, "obtener", "get_escenario", 2),
        (EscenarioService, "eliminar", "get_escenario", 2),
        (PreguntaService, "obtener", "get_pregunta", 2),
        (PreguntaService, "eliminar", "get_pregunta", 2),
        (PreguntaService, "listar", "get_escenario", 2),
        (OpcionService, "listar", "get_pregunta", 2),
        (OpcionService, "eliminar", "get_opcion", 3),
    ],
)
def test_componente_inexistente_o_de_otro_caso(autor, clase, metodo, faltante, n_ids):
    servicio = _con_dobles(clase)
    setattr(servicio._contenido, faltante, MagicMock(return_value=None))

    with pytest.raises(NotFoundError):
        getattr(servicio, metodo)(autor, *[uuid.uuid4() for _ in range(n_ids)])


def test_pregunta_con_criterio_de_otro_caso(autor):
    servicio = _con_dobles(PreguntaService)
    servicio._contenido.get_criterio.return_value = None

    with pytest.raises(BusinessRuleError, match="rúbrica de este caso"):
        servicio.crear(autor, uuid.uuid4(), uuid.uuid4(), PreguntaCreate(enunciado="¿Qué?", criterio_id=uuid.uuid4()))

    servicio._contenido.add.assert_not_called()


def test_quitar_criterio_de_pregunta_no_consulta_la_rubrica(autor):
    servicio = _con_dobles(PreguntaService)
    pregunta = Pregunta(criterio_id=uuid.uuid4())
    servicio._contenido.get_pregunta.return_value = pregunta

    servicio.actualizar(autor, uuid.uuid4(), uuid.uuid4(), PreguntaUpdate(criterio_id=None))

    assert pregunta.criterio_id is None
    servicio._contenido.get_criterio.assert_not_called()


def test_opcion_nueva_recibe_el_siguiente_orden(autor):
    servicio = _con_dobles(OpcionService)
    servicio._contenido.next_orden_opcion.return_value = 3

    opcion = servicio.crear(autor, uuid.uuid4(), uuid.uuid4(), OpcionCreate(texto="Opción", es_correcta=True))

    assert (opcion.orden, opcion.es_correcta) == (3, True)


def test_eliminar_opcion_compacta_las_restantes(autor):
    servicio = _con_dobles(OpcionService)
    restantes = [Opcion(orden=3), Opcion(orden=2)]
    servicio._contenido.list_opciones.return_value = restantes

    servicio.eliminar(autor, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    servicio._contenido.aplicar_orden.assert_called_once_with([restantes[1], restantes[0]])


def test_segunda_rubrica_se_rechaza(autor):
    servicio = _con_dobles(RubricaService)
    servicio._contenido.get_rubrica.return_value = Rubrica()

    with pytest.raises(ConflictError):
        servicio.crear(autor, uuid.uuid4(), RubricaCreate(escala_max=5))

    servicio._contenido.add.assert_not_called()


def test_rubrica_se_crea_con_sus_criterios_y_niveles(autor):
    servicio = _con_dobles(RubricaService)
    servicio._contenido.get_rubrica.side_effect = [None, Rubrica()]
    datos = RubricaCreate(escala_max=5, criterios=[{"descripcion": "Análisis", "peso": 100, "niveles": [NIVEL]}])

    servicio.crear(autor, uuid.uuid4(), datos)

    creada = servicio._contenido.add.call_args.args[0]
    assert creada.criterios[0].peso == Decimal("100.00")
    assert creada.criterios[0].niveles[0]["valor"] == Decimal("1.00")


@pytest.mark.parametrize("campo", ["descripcion", "peso", "niveles"])
def test_campos_del_criterio_no_se_vacian(autor, campo):
    servicio = _con_dobles(RubricaService)

    with pytest.raises(BusinessRuleError, match=campo):
        servicio.actualizar_criterio(autor, uuid.uuid4(), uuid.uuid4(), CriterioUpdate(**{campo: None}))


def test_actualizar_criterio_conserva_lo_no_enviado(autor):
    servicio = _con_dobles(RubricaService)
    criterio = CriterioRubrica(descripcion="Original", peso=Decimal("10.00"), niveles=[])
    servicio._contenido.get_criterio.return_value = criterio

    servicio.actualizar_criterio(autor, uuid.uuid4(), uuid.uuid4(), CriterioUpdate(peso=20))

    assert (criterio.descripcion, criterio.peso) == ("Original", Decimal("20.00"))


def test_actualizar_criterio_reemplaza_descripcion_y_niveles(autor):
    servicio = _con_dobles(RubricaService)
    criterio = CriterioRubrica(descripcion="Original", peso=Decimal("10.00"), niveles=[])
    servicio._contenido.get_criterio.return_value = criterio

    servicio.actualizar_criterio(autor, uuid.uuid4(), uuid.uuid4(), CriterioUpdate(descripcion="Nueva", niveles=[NIVEL]))

    assert criterio.descripcion == "Nueva"
    assert criterio.niveles[0]["nombre"] == "Bajo"


@pytest.mark.parametrize(
    ("metodo", "faltante", "argumentos"),
    [
        ("obtener", "get_rubrica", ()),
        ("actualizar", "get_rubrica", (RubricaUpdate(escala_max=5),)),
        ("crear_criterio", "get_rubrica", (CriterioCreate(descripcion="x", peso=1),)),
        ("actualizar_criterio", "get_criterio", (uuid.uuid4(), CriterioUpdate(peso=1))),
        ("eliminar_criterio", "get_criterio", (uuid.uuid4(),)),
    ],
)
def test_rubrica_o_criterio_inexistente(autor, metodo, faltante, argumentos):
    servicio = _con_dobles(RubricaService)
    setattr(servicio._contenido, faltante, MagicMock(return_value=None))

    with pytest.raises(NotFoundError):
        getattr(servicio, metodo)(autor, uuid.uuid4(), *argumentos)


def test_caso_recien_creado_reporta_lo_que_le_falta():
    caso = CasoEstudio(nombre="Vacío", estado=EstadoCasoEnum.BORRADOR, autor_id=usuario(RolUsuario.DOCENTE).id)

    assert set(validar_caso(caso).codigos) >= {"CASO_SIN_ESCENARIOS", "CASO_SIN_RUBRICA"}
