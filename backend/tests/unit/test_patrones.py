"""Patrones de diseño realmente implementados en los módulos 1-4.

Inventario verificado en el código:
- State: ciclo de vida de casos (`cases/states`) y de sesiones (`sessions/states`).
- Builder con Director: construcción de casos desde CaseDSL (`cases/builders`).
- Chain of Responsibility: validación previa a la publicación (`cases/validators`).
- Repository: acceso a datos separado de las reglas de negocio.
- Puertos y adaptadores con Null Object: `sessions/integracion.py` y `sessions/eventos.py`.
- Policy: reingreso estudiantil y temporizador.

Observer y Strategy NO existen todavía: `sessions/integracion.py` declara expresamente que
"no es Observer", `sessions/eventos.py` deja el Observer al módulo 6 y
`participations/services/consulta_evaluacion.py` reserva la Strategy para el módulo 5.
Por eso no se prueban: una prueba de esos patrones ejercitaría código inexistente.
"""

import inspect
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app import bootstrap
from app.core.exceptions import ConflictError, PermissionDeniedError, UnprocessableEntityError
from app.modules.auth.dependencies.auth import require_admin, require_docente, require_roles
from app.modules.auth.models import RolUsuario
from app.modules.auth.repositories import UsuarioRepository
from app.modules.cases.builders import (
    CasoAcademico,
    ConstructorCaso,
    ConstructorCasoAcademico,
    ErrorConstruccionCaso,
    ReferenciaEscenario,
    ReferenciaPregunta,
    ServicioConstruccionCaso,
)
from app.modules.cases.dsl import CaseDSLDocument, exportar_caso
from app.modules.cases.models import EstadoCasoEnum
from app.modules.cases.states import CicloVidaCaso
from app.modules.cases.states.estados import (
    CasoNoPublicableError,
    EstadoArchivado,
    EstadoBorrador,
    EstadoCaso,
    EstadoPublicado,
    TransicionNoPermitidaError,
)
from app.modules.cases.validators import (
    ResultadoValidacion,
    ValidadorCaso,
    ValidadorDatosCaso,
    ValidadorEscenariosYPreguntas,
    ValidadorOpciones,
    ValidadorRubrica,
    construir_cadena_publicacion,
    validar_caso,
)
from app.modules.participations.policies.integracion_sesiones import CierreParticipacionesSQL, ReingresoConParticipacionActiva
from app.modules.sessions import eventos, integracion
from app.modules.sessions.models import EstadoSesionEnum
from app.modules.sessions.services import AccesoEstudiantilService, CierreSesionesService, SinReingreso
from app.modules.sessions.services.base import ServicioSesionesBase
from app.modules.sessions.states import CicloVidaSesion, IngresoNoPermitidoError
from app.modules.sessions.states.estados import (
    CAMPOS_INFORMATIVOS,
    EstadoEnCurso,
    EstadoFinalizado,
    EstadoSesion,
    EstadoSinIniciar,
    TransicionSesionNoPermitidaError,
)
from tests.conftest import RelojFijo
from tests.unit.fabricas import T8, T9, T10, caso_valido, sesion, sesion_bd, usuario

GRACIA = timedelta(hours=24)


# --- State: casos -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("estado", "clase"),
    [
        (EstadoCasoEnum.BORRADOR, EstadoBorrador),
        (EstadoCasoEnum.PUBLICADO, EstadoPublicado),
        (EstadoCasoEnum.ARCHIVADO, EstadoArchivado),
    ],
)
def test_contexto_de_caso_selecciona_el_estado_concreto(estado, clase):
    assert type(CicloVidaCaso(caso_valido(estado)).estado) is clase


def test_estado_abstracto_de_caso_rechaza_todo_por_defecto():
    class EstadoNuevo(EstadoCaso):
        valor = EstadoCasoEnum.BORRADOR
        descripcion = "nuevo"

    contexto = MagicMock()
    estado = EstadoNuevo()

    for operacion in (
        lambda: estado.asegurar_contenido_editable(contexto),
        lambda: estado.asegurar_eliminable(contexto),
        lambda: estado.publicar(contexto, MagicMock()),
        lambda: estado.archivar(contexto),
    ):
        with pytest.raises(TransicionNoPermitidaError, match="nuevo"):
            operacion()
    assert estado.admite_programacion() is False
    contexto.cambiar_a.assert_not_called()


def test_cadena_completa_de_transiciones_del_caso():
    caso = caso_valido()
    ciclo = CicloVidaCaso(caso)

    ciclo.publicar(lambda: validar_caso(caso))
    assert (caso.estado, type(ciclo.estado)) == (EstadoCasoEnum.PUBLICADO, EstadoPublicado)

    ciclo.archivar()
    assert (caso.estado, type(ciclo.estado)) == (EstadoCasoEnum.ARCHIVADO, EstadoArchivado)


def test_publicar_invalido_conserva_borrador_y_entrega_errores():
    caso = caso_valido()
    caso.historia = None
    ciclo = CicloVidaCaso(caso)

    with pytest.raises(CasoNoPublicableError) as error:
        ciclo.publicar(lambda: validar_caso(caso))

    assert caso.estado == EstadoCasoEnum.BORRADOR
    assert isinstance(error.value, UnprocessableEntityError)
    assert error.value.extra["valido"] is False
    assert [e["codigo"] for e in error.value.extra["errores"]] == ["CASO_HISTORIA_VACIA"]


@pytest.mark.parametrize("estado", [EstadoCasoEnum.PUBLICADO, EstadoCasoEnum.ARCHIVADO])
def test_solo_el_borrador_ejecuta_la_validacion(estado):
    validar = MagicMock()

    with pytest.raises(TransicionNoPermitidaError):
        CicloVidaCaso(caso_valido(estado)).publicar(validar)

    validar.assert_not_called()


@pytest.mark.parametrize(
    ("estado", "editable", "eliminable", "programable", "archivable"),
    [
        (EstadoCasoEnum.BORRADOR, True, True, False, False),
        (EstadoCasoEnum.PUBLICADO, False, False, True, True),
        (EstadoCasoEnum.ARCHIVADO, False, False, False, False),
    ],
)
def test_matriz_de_permisos_por_estado_del_caso(estado, editable, eliminable, programable, archivable):
    def permite(operacion) -> bool:
        ciclo = CicloVidaCaso(caso_valido(estado))
        try:
            getattr(ciclo, operacion)()
        except TransicionNoPermitidaError:
            return False
        return True

    assert permite("asegurar_contenido_editable") is editable
    assert permite("asegurar_eliminable") is eliminable
    assert permite("archivar") is archivable
    assert CicloVidaCaso(caso_valido(estado)).admite_programacion() is programable


# --- State: sesiones ----------------------------------------------------------------------------


def _ciclo(estado: EstadoSesionEnum, **extra) -> CicloVidaSesion:
    return CicloVidaSesion(sesion(estado, **extra))


@pytest.mark.parametrize(
    ("estado", "clase"),
    [
        (EstadoSesionEnum.SIN_INICIAR, EstadoSinIniciar),
        (EstadoSesionEnum.EN_CURSO, EstadoEnCurso),
        (EstadoSesionEnum.FINALIZADO, EstadoFinalizado),
    ],
)
def test_contexto_de_sesion_selecciona_el_estado_concreto(estado, clase):
    assert type(_ciclo(estado).estado) is clase


def test_transiciones_completas_de_la_sesion_persisten_en_el_modelo():
    ciclo = _ciclo(EstadoSesionEnum.SIN_INICIAR)

    ciclo.iniciar(T8)
    assert ciclo.sesion.estado == EstadoSesionEnum.EN_CURSO

    ciclo.finalizar(T9)
    assert ciclo.sesion.estado == EstadoSesionEnum.FINALIZADO


@pytest.mark.parametrize(
    "ahora",
    [T8 - timedelta(seconds=1), T10, T10 + timedelta(hours=1)],
    ids=["antes-del-inicio", "justo-en-el-fin", "despues-del-fin"],
)
def test_iniciar_fuera_de_la_ventana(ahora):
    ciclo = _ciclo(EstadoSesionEnum.SIN_INICIAR)

    with pytest.raises(TransicionSesionNoPermitidaError):
        ciclo.iniciar(ahora)

    assert ciclo.sesion.estado == EstadoSesionEnum.SIN_INICIAR


@pytest.mark.parametrize("ahora", [T8, T10 - timedelta(seconds=1)], ids=["en-el-inicio", "ultimo-segundo"])
def test_iniciar_dentro_de_la_ventana(ahora):
    ciclo = _ciclo(EstadoSesionEnum.SIN_INICIAR)

    ciclo.iniciar(ahora)

    assert ciclo.sesion.estado == EstadoSesionEnum.EN_CURSO


@pytest.mark.parametrize(
    ("estado", "operacion"),
    [
        (EstadoSesionEnum.EN_CURSO, "iniciar"),
        (EstadoSesionEnum.FINALIZADO, "iniciar"),
        (EstadoSesionEnum.SIN_INICIAR, "finalizar"),
        (EstadoSesionEnum.FINALIZADO, "finalizar"),
    ],
)
def test_transiciones_invalidas_de_sesion(estado, operacion):
    ciclo = _ciclo(estado)

    with pytest.raises(TransicionSesionNoPermitidaError):
        getattr(ciclo, operacion)(T8)

    assert ciclo.sesion.estado == estado


@pytest.mark.parametrize(
    ("ahora", "autoriza"),
    [
        (T8 - timedelta(seconds=1), False),
        (T8, True),
        (T9, True),
        (T9 + timedelta(seconds=1), False),
        (T10, False),
        (T10 + timedelta(hours=2), False),
    ],
)
def test_ventana_de_ingreso_en_curso(ahora, autoriza):
    ciclo = _ciclo(EstadoSesionEnum.EN_CURSO)

    if autoriza:
        ciclo.autorizar_ingreso(ahora)
    else:
        with pytest.raises(IngresoNoPermitidoError):
            ciclo.autorizar_ingreso(ahora)


@pytest.mark.parametrize(("estado", "mensaje"), [(EstadoSesionEnum.SIN_INICIAR, "iniciada"), (EstadoSesionEnum.FINALIZADO, "finalizado")])
def test_ingreso_en_estados_sin_acceso(estado, mensaje):
    with pytest.raises(IngresoNoPermitidoError, match=mensaje) as error:
        _ciclo(estado).autorizar_ingreso(T8)

    assert isinstance(error.value, PermissionDeniedError)


@pytest.mark.parametrize(
    ("estado", "mantiene"),
    [(EstadoSesionEnum.SIN_INICIAR, False), (EstadoSesionEnum.EN_CURSO, True), (EstadoSesionEnum.FINALIZADO, False)],
)
def test_solo_en_curso_mantiene_los_accesos(estado, mantiene):
    assert _ciclo(estado).mantiene_accesos() is mantiene


def test_edicion_de_programacion_segun_estado():
    _ciclo(EstadoSesionEnum.SIN_INICIAR).asegurar_programacion_editable({"inicio", "fin", "caso_id"})
    _ciclo(EstadoSesionEnum.EN_CURSO).asegurar_programacion_editable(CAMPOS_INFORMATIVOS)

    with pytest.raises(TransicionSesionNoPermitidaError, match="inicio"):
        _ciclo(EstadoSesionEnum.EN_CURSO).asegurar_programacion_editable({"lugar", "inicio"})
    with pytest.raises(TransicionSesionNoPermitidaError):
        _ciclo(EstadoSesionEnum.FINALIZADO).asegurar_programacion_editable({"lugar"})


@pytest.mark.parametrize("estado", [EstadoSesionEnum.SIN_INICIAR, EstadoSesionEnum.EN_CURSO])
def test_nuevas_convocatorias_hasta_el_limite_de_ingreso_inclusive(estado):
    ciclo = _ciclo(estado)

    ciclo.asegurar_nuevas_convocatorias(T9)
    ciclo.asegurar_convocatorias_editables()
    with pytest.raises(TransicionSesionNoPermitidaError):
        ciclo.asegurar_nuevas_convocatorias(T9 + timedelta(seconds=1))


def test_sesion_finalizada_no_admite_convocatorias():
    ciclo = _ciclo(EstadoSesionEnum.FINALIZADO)

    with pytest.raises(TransicionSesionNoPermitidaError):
        ciclo.asegurar_convocatorias_editables()
    with pytest.raises(TransicionSesionNoPermitidaError):
        ciclo.asegurar_nuevas_convocatorias(T8)


@pytest.mark.parametrize("estado", [EstadoSesionEnum.SIN_INICIAR, EstadoSesionEnum.EN_CURSO])
def test_cierre_por_vencimiento_respeta_el_plazo_exacto(estado):
    ciclo = _ciclo(estado)

    assert ciclo.cerrar_por_vencimiento(T10 + GRACIA - timedelta(seconds=1), GRACIA) is False
    assert ciclo.sesion.estado == estado

    assert ciclo.cerrar_por_vencimiento(T10 + GRACIA, GRACIA) is True
    assert ciclo.sesion.estado == EstadoSesionEnum.FINALIZADO


def test_cierre_por_vencimiento_es_idempotente():
    assert _ciclo(EstadoSesionEnum.FINALIZADO).cerrar_por_vencimiento(T10 + GRACIA * 2, GRACIA) is False


def test_estados_de_casos_y_sesiones_son_jerarquias_independientes():
    assert not issubclass(EstadoSesion, EstadoCaso)
    assert not issubclass(EstadoCaso, EstadoSesion)


# --- Builder --------------------------------------------------------------------------------


AUTOR = uuid.uuid4()
NIVELES = [{"nombre": "Bajo", "valor": Decimal("1.00")}, {"nombre": "Alto", "valor": Decimal("5.00")}]


class ConstructorEspia(ConstructorCaso):
    """Segundo constructor concreto: demuestra que el Director solo depende de la abstracción."""

    def __init__(self):
        self.pasos: list[str] = []

    def reiniciar(self, autor_id):
        self.pasos.append("reiniciar")

    def establecer_datos_generales(self, nombre, desarrollo_situacional, historia, tiempo_estimado_min):
        self.pasos.append(f"datos:{nombre}")

    def agregar_escenario(self, titulo, contexto):
        self.pasos.append(f"escenario:{titulo}")
        return ReferenciaEscenario(0)

    def agregar_pregunta(self, escenario, enunciado, clave_criterio=None):
        self.pasos.append(f"pregunta:{enunciado}:{clave_criterio}")
        return ReferenciaPregunta(escenario, 0)

    def agregar_opcion(self, pregunta, texto, es_correcta, justificacion):
        self.pasos.append(f"opcion:{texto}")

    def establecer_rubrica(self, escala_max):
        self.pasos.append("rubrica")

    def agregar_criterio(self, clave, descripcion, peso, niveles):
        self.pasos.append(f"criterio:{clave}")

    def construir(self):
        self.pasos.append("construir")
        return "producto"


def _documento(**cambios) -> CaseDSLDocument:
    return CaseDSLDocument.model_validate(
        {
            "dslVersion": "1.0",
            "caso": {"nombre": "Caso", "estado": "PUBLICADO"},
            "escenarios": [
                {
                    "titulo": "E1",
                    "preguntas": [{"enunciado": "P1", "criterio": "A", "opciones": [{"texto": "O1", "esCorrecta": True}, {"texto": "O2"}]}],
                }
            ],
            "rubrica": {
                "escalaMax": 5,
                "criterios": [{"clave": "A", "descripcion": "Análisis", "peso": 100, "niveles": [{"nombre": "Bajo", "valor": 1}]}],
            },
        }
        | cambios
    )


@pytest.fixture
def constructor():
    c = ConstructorCasoAcademico()
    c.reiniciar(AUTOR)
    c.establecer_datos_generales("Caso", None, None, 30)
    return c


def test_constructor_abstracto_no_se_instancia():
    with pytest.raises(TypeError):
        ConstructorCaso()
    assert issubclass(ConstructorCasoAcademico, ConstructorCaso)


def test_director_impone_la_secuencia_de_pasos():
    espia = ConstructorEspia()

    resultado = ServicioConstruccionCaso(espia).construir_desde_dsl(_documento(), AUTOR)

    assert resultado == "producto"
    assert espia.pasos == [
        "reiniciar",
        "datos:Caso",
        "rubrica",
        "criterio:A",
        "escenario:E1",
        "pregunta:P1:A",
        "opcion:O1",
        "opcion:O2",
        "construir",
    ]


def test_director_omite_la_rubrica_si_no_viene():
    espia = ConstructorEspia()
    documento = _documento(rubrica=None, escenarios=[])

    ServicioConstruccionCaso(espia).construir_desde_dsl(documento, AUTOR)

    assert "rubrica" not in espia.pasos


def test_producto_final_tiene_todo_enlazado_y_en_borrador():
    producto = ServicioConstruccionCaso(ConstructorCasoAcademico()).construir_desde_dsl(_documento(), AUTOR)

    assert isinstance(producto, CasoAcademico)
    assert (producto.caso.estado, producto.caso.autor_id) == (EstadoCasoEnum.BORRADOR, AUTOR)  # se ignora el estado del documento
    assert [o.texto for o in producto.opciones] == ["O1", "O2"]
    assert producto.preguntas[0].criterio is producto.rubrica.criterios[0]
    assert len(producto.escenarios) == 1


def test_constructor_numera_segun_el_orden_de_incorporacion(constructor):
    e1 = constructor.agregar_escenario("E1", None)
    e2 = constructor.agregar_escenario("E2", None)
    p1 = constructor.agregar_pregunta(e2, "P1")
    p2 = constructor.agregar_pregunta(e2, "P2")
    constructor.agregar_opcion(p2, "O1", True, None)
    constructor.agregar_opcion(p2, "O2", False, None)

    producto = constructor.construir()

    assert (e1.indice, e2.indice, p1.indice, p2.indice) == (0, 1, 0, 1)
    assert [e.orden for e in producto.escenarios] == [1, 2]
    assert [p.orden for p in producto.preguntas] == [1, 2]
    assert [o.orden for o in producto.opciones] == [1, 2]


@pytest.mark.parametrize(
    ("operacion", "mensaje"),
    [
        (lambda c: c.establecer_datos_generales("Otra vez", None, None, None), "ya fueron establecidos"),
        (lambda c: c.agregar_criterio("A", "x", Decimal(1), []), "rúbrica antes"),
        (lambda c: c.agregar_pregunta(ReferenciaEscenario(0), "P"), "referencia de escenario"),
        (lambda c: c.agregar_pregunta(ReferenciaEscenario(-1), "P"), "referencia de escenario"),
    ],
    ids=["datos-duplicados", "criterio-sin-rubrica", "escenario-fuera-de-rango", "escenario-negativo"],
)
def test_constructor_exige_secuencia_valida(constructor, operacion, mensaje):
    with pytest.raises(ErrorConstruccionCaso, match=mensaje):
        operacion(constructor)


def test_constructor_sin_autor_o_sin_datos():
    with pytest.raises(ErrorConstruccionCaso, match="autor"):
        ConstructorCasoAcademico().establecer_datos_generales("Caso", None, None, None)

    vacio = ConstructorCasoAcademico()
    vacio.reiniciar(AUTOR)
    for operacion in (vacio.construir, lambda: vacio.agregar_escenario("E", None), lambda: vacio.establecer_rubrica(Decimal(5))):
        with pytest.raises(ErrorConstruccionCaso, match="datos generales"):
            operacion()


def test_constructor_una_sola_rubrica_y_claves_unicas(constructor):
    constructor.establecer_rubrica(Decimal(5))
    constructor.agregar_criterio("A", "x", Decimal(50), NIVELES)

    with pytest.raises(ErrorConstruccionCaso, match="una rúbrica"):
        constructor.establecer_rubrica(Decimal(5))
    with pytest.raises(ErrorConstruccionCaso, match="repetida"):
        constructor.agregar_criterio("A", "y", Decimal(50), NIVELES)


def test_constructor_resuelve_criterios_declarados_despues(constructor):
    escenario = constructor.agregar_escenario("E", None)
    constructor.agregar_pregunta(escenario, "P", "TARDIO")
    constructor.establecer_rubrica(Decimal(5))
    constructor.agregar_criterio("TARDIO", "x", Decimal(100), NIVELES)

    assert constructor.construir().preguntas[0].criterio.descripcion == "x"


def test_constructor_rechaza_criterio_desconocido(constructor):
    constructor.agregar_pregunta(constructor.agregar_escenario("E", None), "P", "FANTASMA")

    with pytest.raises(ErrorConstruccionCaso, match="FANTASMA"):
        constructor.construir()


def test_referencia_de_pregunta_fuera_de_rango(constructor):
    referencia = ReferenciaPregunta(constructor.agregar_escenario("E", None), 0)

    with pytest.raises(ErrorConstruccionCaso, match="referencia de pregunta"):
        constructor.agregar_opcion(referencia, "O", True, None)


def test_constructor_queda_limpio_tras_construir(constructor):
    producto = constructor.construir()

    with pytest.raises(ErrorConstruccionCaso, match="datos generales"):
        constructor.agregar_escenario("Tarde", None)

    constructor.establecer_datos_generales("Nuevo", None, None, None)
    assert constructor.construir().caso is not producto.caso


def test_colaboracion_builder_y_exportador_es_reversible():
    original = ServicioConstruccionCaso(ConstructorCasoAcademico()).construir_desde_dsl(_documento(), AUTOR).caso
    for criterio in original.rubrica.criterios:
        criterio.id = uuid.uuid4()
    original.preguntas[0].criterio_id = original.preguntas[0].criterio.id
    original.id = uuid.uuid4()

    exportado = exportar_caso(original)
    reconstruido = ServicioConstruccionCaso(ConstructorCasoAcademico()).construir_desde_dsl(exportado, AUTOR).caso

    assert reconstruido.nombre == original.nombre
    assert reconstruido.preguntas[0].criterio.descripcion == "Análisis"
    assert [o.texto for o in reconstruido.preguntas[0].opciones] == ["O1", "O2"]


# --- Chain of Responsibility --------------------------------------------------------------------


class EslabonEspia(ValidadorCaso):
    def __init__(self, nombre, traza, error=None):
        super().__init__()
        self.nombre, self.traza, self.error = nombre, traza, error

    def _revisar(self, caso, resultado):
        self.traza.append(self.nombre)
        if self.error:
            resultado.agregar(self.error, componente="caso", componente_id=None, campo="x", ruta="x", mensaje="error")


def test_validador_abstracto_no_se_instancia():
    with pytest.raises(TypeError):
        ValidadorCaso()


def test_enlazar_devuelve_el_siguiente_para_encadenar():
    primero, segundo = EslabonEspia("a", []), EslabonEspia("b", [])

    assert primero.enlazar(segundo) is segundo


def test_cada_eslabon_delega_incluso_si_encontro_errores():
    traza = []
    cabeza = EslabonEspia("a", traza, "E1")
    cabeza.enlazar(EslabonEspia("b", traza)).enlazar(EslabonEspia("c", traza, "E3"))

    resultado = cabeza.validar(caso_valido(), ResultadoValidacion())

    assert traza == ["a", "b", "c"]
    assert resultado.codigos == ["E1", "E3"]


def test_eslabon_final_termina_la_cadena():
    traza = []

    resultado = EslabonEspia("solo", traza).validar(caso_valido(), ResultadoValidacion())

    assert traza == ["solo"] and resultado.valido


def test_cadena_de_publicacion_se_ensambla_en_orden():
    eslabon, clases = construir_cadena_publicacion(), []
    while eslabon is not None:
        clases.append(type(eslabon))
        eslabon = eslabon._siguiente

    assert clases == [ValidadorDatosCaso, ValidadorEscenariosYPreguntas, ValidadorOpciones, ValidadorRubrica]


def test_cadena_acumula_errores_de_todos_los_aspectos():
    caso = caso_valido()
    caso.historia = None
    caso.escenarios[0].contexto = None
    caso.preguntas[0].opciones[0].justificacion = None
    caso.rubrica.escala_max = Decimal("0")

    assert validar_caso(caso).codigos == [
        "CASO_HISTORIA_VACIA",
        "ESCENARIO_CONTEXTO_VACIO",
        "OPCION_SIN_JUSTIFICACION",
        "RUBRICA_ESCALA_INVALIDA",
    ]


def test_cadena_no_modifica_el_caso():
    caso = caso_valido()
    caso.historia = None
    antes = (caso.estado, caso.nombre, len(caso.preguntas))

    validar_caso(caso)

    assert (caso.estado, caso.nombre, len(caso.preguntas)) == antes


def test_cada_validacion_usa_una_cadena_nueva():
    assert construir_cadena_publicacion() is not construir_cadena_publicacion()


def test_state_y_chain_colaboran_en_la_publicacion():
    caso = caso_valido()
    caso.escenarios = []

    with pytest.raises(CasoNoPublicableError) as error:
        CicloVidaCaso(caso).publicar(lambda: validar_caso(caso))

    assert "CASO_SIN_ESCENARIOS" in [e["codigo"] for e in error.value.extra["errores"]]


# --- Repository ---------------------------------------------------------------------------------


def test_repositorio_encapsula_la_sesion_y_no_confirma():
    session = sesion_bd()
    repositorio = UsuarioRepository(session)
    nuevo = usuario()

    assert repositorio.add(nuevo) is nuevo

    session.add.assert_called_once_with(nuevo)
    session.flush.assert_called_once()
    session.commit.assert_not_called()  # la transacción es responsabilidad del servicio


def test_repositorio_de_usuarios_no_conoce_reglas_de_negocio():
    fuente = inspect.getsource(inspect.getmodule(UsuarioRepository))

    assert "app.core.exceptions" not in fuente


@pytest.mark.parametrize(
    "operacion",
    [
        lambda r: r.exists_by_correo("a@u.edu.co", uuid.uuid4()),
        lambda r: r.exists_by_codigo_institucional("A-1", uuid.uuid4()),
    ],
)
def test_repositorio_aplica_la_exclusion_en_la_consulta(operacion):
    session = sesion_bd()
    session.scalars.return_value.first.return_value = None

    assert operacion(UsuarioRepository(session)) is False

    assert "usuario.id !=" in str(session.scalars.call_args.args[0])


# --- Puertos, adaptadores y Null Object ----------------------------------------------------------


@pytest.fixture
def puertos(monkeypatch):
    """Restaura los puertos globales al terminar: `configurar` los modifica a nivel de módulo."""
    monkeypatch.setattr(integracion, "_cierre", integracion._cierre)
    monkeypatch.setattr(integracion, "_fabrica_reingreso", integracion._fabrica_reingreso)


def test_puerto_de_cierre_por_defecto_no_hace_nada():
    nulo = integracion._SinParticipaciones()

    assert nulo.cerrar_por_revocacion(sesion_bd(), uuid.uuid4(), T8) is None
    assert nulo.cerrar_por_fin_de_sesion(sesion_bd(), uuid.uuid4(), T8) is None


def test_configurar_reemplaza_solo_lo_indicado(puertos):
    cierre_inicial = integracion.cierre_participaciones()
    fabrica = MagicMock()

    integracion.configurar(fabrica_reingreso=fabrica)

    assert integracion.cierre_participaciones() is cierre_inicial
    db = sesion_bd()
    assert integracion.politica_reingreso(db) is fabrica.return_value
    fabrica.assert_called_once_with(db)


def test_raiz_de_composicion_conecta_las_implementaciones_del_modulo_4(puertos):
    integracion.configurar(cierre=integracion._SinParticipaciones(), fabrica_reingreso=lambda db: SinReingreso())

    bootstrap.configurar_integraciones()

    assert isinstance(integracion.cierre_participaciones(), CierreParticipacionesSQL)
    assert isinstance(integracion.politica_reingreso(sesion_bd()), ReingresoConParticipacionActiva)


def test_modulo_3_no_importa_al_modulo_4():
    for modulo in (integracion, eventos, CierreSesionesService, AccesoEstudiantilService):
        assert "app.modules.participations" not in inspect.getsource(inspect.getmodule(modulo))


def test_publicador_nulo_registra_sin_enviar(caplog):
    evento = eventos.EventoSesion(eventos.SESION_INICIADA, uuid.uuid4(), {"origen": "docente"})

    with caplog.at_level(logging.INFO, logger="iter_animi.sesiones.eventos"):
        assert eventos.PublicadorNulo().publicar(evento) is None

    assert "SESION_INICIADA" in caplog.text


def test_servicios_usan_publicador_nulo_por_defecto_y_admiten_otro():
    assert isinstance(ServicioSesionesBase(sesion_bd())._eventos, eventos.PublicadorNulo)

    propio = MagicMock()
    servicio = ServicioSesionesBase(sesion_bd(), RelojFijo(T8), propio)
    evento = eventos.EventoSesion("X", uuid.uuid4())
    with servicio._transaction():
        servicio._emitir(evento)

    propio.publicar.assert_called_once_with(evento)


def test_evento_es_inmutable():
    evento = eventos.EventoSesion("X", uuid.uuid4())

    with pytest.raises(AttributeError):
        evento.tipo = "Y"


# --- Fábrica de dependencias de autorización ----------------------------------------------------


def test_fabrica_de_dependencias_crea_verificadores_independientes():
    admin, docente = usuario(RolUsuario.ADMINISTRADOR), usuario(RolUsuario.DOCENTE)

    assert require_admin(admin) is admin
    assert require_docente(docente) is docente
    with pytest.raises(PermissionDeniedError):
        require_admin(docente)
    with pytest.raises(PermissionDeniedError):
        require_roles()(admin)


def test_conflicto_es_la_base_de_las_transiciones_prohibidas():
    assert issubclass(TransicionNoPermitidaError, ConflictError)
    assert issubclass(TransicionSesionNoPermitidaError, ConflictError)
