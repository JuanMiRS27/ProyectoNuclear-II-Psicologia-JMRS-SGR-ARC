"""Módulo 3: sesiones, convocatorias y acceso estudiantil (pruebas unitarias, sin base de datos)."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import jwt
import pytest
from pydantic import ValidationError

from app.core import security
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.modules.auth.models import RolUsuario
from app.modules.cases.models import EstadoCasoEnum
from app.modules.sessions import eventos, integracion
from app.modules.sessions.models import ALFABETO_CODIGO, LONGITUD_CODIGO, Convocatoria, EspacioAcademico, EstadoSesionEnum
from app.modules.sessions.schemas import (
    ConvocatoriaCreate,
    ConvocatoriaRead,
    IntentosUpdate,
    RegenerarCodigoRequest,
    SesionCreate,
    SesionUpdate,
    StudentLoginRequest,
)
from app.modules.sessions.security import (
    TIPO_TOKEN_ESTUDIANTIL,
    CodigoCifradoInvalidoError,
    DemasiadosIntentosError,
    LimitadorIntentos,
    cifrar_codigo,
    crear_token_estudiante,
    decodificar_token_estudiante,
    descifrar_codigo,
    generar_codigo,
    get_limitador,
    huella_codigo,
)
from app.modules.sessions.services import (
    AccesoEstudiantil,
    AccesoEstudiantilService,
    CierreSesionesService,
    ConvocatoriaService,
    SesionService,
    SinReingreso,
    plazo_cierre,
    sesion_service,
)
from app.modules.sessions.services.base import ServicioSesionesBase, _traducir
from app.modules.sessions.states import IngresoNoPermitidoError
from app.modules.sessions.states.estados import TransicionSesionNoPermitidaError
from app.workers import session_expiration
from app.workers.session_expiration import __main__ as worker_cli
from app.workers.session_expiration import runner as worker
from tests.conftest import RelojFijo
from tests.unit.fabricas import CODIGO, T8, T9, T10, caso_valido, convocatoria, error_integridad, sesion, sesion_bd, usuario

CORREO = "eva@universidad.edu.co"


class PublicadorEspia:
    def __init__(self):
        self.recibidos: list[eventos.EventoSesion] = []

    def publicar(self, evento: eventos.EventoSesion) -> None:
        self.recibidos.append(evento)

    @property
    def tipos(self) -> list[str]:
        return [e.tipo for e in self.recibidos]


# --- Modelo ---------------------------------------------------------------------------------


def test_limite_de_ingreso_es_la_mitad_de_la_ventana():
    assert sesion().limite_ingreso == T9
    impar = sesion(fin=T8 + timedelta(minutes=3))
    assert impar.limite_ingreso == T8 + timedelta(seconds=90)


def test_convocatoria_activa_hasta_ser_revocada():
    conv = convocatoria()

    assert conv.activa is True
    conv.revocada_en = T8
    assert conv.activa is False


def test_representacion_de_convocatoria_omite_el_codigo():
    conv = convocatoria()

    assert conv.codigo_autorizacion not in repr(conv)
    assert "codigo" not in repr(conv).lower()


def test_lectura_de_convocatoria_no_expone_codigo():
    assert "codigo_autorizacion" not in ConvocatoriaRead.model_fields


# --- Esquemas -------------------------------------------------------------------------------


def _sesion_datos(**cambios) -> dict:
    return {
        "caso_id": uuid.uuid4(),
        "espacio_academico_id": uuid.uuid4(),
        "inicio": "2026-11-03T08:00:00-05:00",
        "fin": "2026-11-03T10:00:00-05:00",
        "tiempo_max_min": 40,
    } | cambios


def test_fechas_se_normalizan_a_utc():
    datos = SesionCreate(**_sesion_datos())

    assert datos.inicio == datetime(2026, 11, 3, 13, 0, tzinfo=UTC)
    assert datos.inicio.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "cambios",
    [
        {"inicio": "2026-11-03T08:00:00"},
        {"fin": "2026-11-03T10:00:00"},
        {"inicio": "no-es-fecha"},
        {"fin": "2026-11-03T08:00:00-05:00"},
        {"fin": "2026-11-03T07:59:59-05:00"},
        {"tiempo_max_min": 0},
        {"tiempo_max_min": 241},
        {"lugar": "x" * 151},
        {"mensaje": "x" * 5001},
        {"docente_id": str(uuid.uuid4())},
        {"estado": "EN_CURSO"},
        {"estudiantes": ["no-es-correo"]},
        {"estudiantes": [CORREO, CORREO.upper()]},
        {"estudiantes": [f"e{i}@u.edu.co" for i in range(501)]},
    ],
    ids=lambda c: next(iter(c)) + "-" + str(next(iter(c.values())))[:20],
)
def test_programacion_invalida(cambios):
    with pytest.raises(ValidationError):
        SesionCreate(**_sesion_datos(**cambios))


@pytest.mark.parametrize("cambios", [{"tiempo_max_min": 1}, {"tiempo_max_min": 240}, {"estudiantes": [f"e{i}@u.edu.co" for i in range(500)]}])
def test_limites_validos_de_programacion(cambios):
    assert SesionCreate(**_sesion_datos(**cambios))


@pytest.mark.parametrize("campo", ["lugar", "mensaje"])
def test_textos_informativos_en_blanco_se_guardan_como_nulo(campo):
    assert getattr(SesionCreate(**_sesion_datos(**{campo: "   "})), campo) is None
    assert getattr(SesionUpdate(**{campo: ""}), campo) is None


@pytest.mark.parametrize(
    ("inicio", "fin"),
    [("0001-01-01T00:00:00+05:00", "2026-11-03T10:00:00Z"), ("2026-11-03T08:00:00Z", "9999-12-31T23:59:59-05:00")],
    ids=["antes-del-anio-1", "despues-del-anio-9999"],
)
def test_fecha_no_representable_en_utc_es_error_de_validacion(inicio, fin):
    # Regresión DEF-04: la conversión a UTC desbordaba y terminaba en HTTP 500.
    with pytest.raises(ValidationError):
        SesionCreate(**_sesion_datos(inicio=inicio, fin=fin))


def test_convocar_exige_al_menos_un_correo_y_normaliza():
    assert ConvocatoriaCreate(estudiantes=["EVA@universidad.edu.co"]).estudiantes == [CORREO]
    with pytest.raises(ValidationError):
        ConvocatoriaCreate(estudiantes=[])


@pytest.mark.parametrize(("valor", "acepta"), [(0, False), (1, True), (100, True), (101, False)])
def test_limites_de_intentos(valor, acepta):
    if acepta:
        assert IntentosUpdate(intentos_autorizados=valor, motivo="Incapacidad médica")
    else:
        with pytest.raises(ValidationError):
            IntentosUpdate(intentos_autorizados=valor, motivo="Incapacidad médica")


@pytest.mark.parametrize(("motivo", "acepta"), [("x" * 4, False), ("    abcd    ", False), ("x" * 5, True), ("x" * 500, True), ("x" * 501, False)])
def test_motivo_requiere_justificacion_real(motivo, acepta):
    if acepta:
        assert RegenerarCodigoRequest(motivo=motivo)
    else:
        with pytest.raises(ValidationError):
            RegenerarCodigoRequest(motivo=motivo)


@pytest.mark.parametrize(
    ("escrito", "normalizado"),
    [("abcd-efgh-2345", "ABCDEFGH2345"), (" ABCD EFGH 2345 ", "ABCDEFGH2345"), ("abcdefgh2345", "ABCDEFGH2345")],
)
def test_codigo_tolera_errores_de_transcripcion(escrito, normalizado):
    assert StudentLoginRequest(correo=CORREO, codigo=escrito).codigo == normalizado


@pytest.mark.parametrize("codigo", ["", "x" * 41])
def test_longitud_de_codigo_en_el_login(codigo):
    with pytest.raises(ValidationError):
        StudentLoginRequest(correo=CORREO, codigo=codigo)


# --- Configuración de cifrado -------------------------------------------------------------------


@pytest.mark.parametrize("clave", ["corta", "@@@@", "QUJD"], ids=["no-base64", "invalida", "tamano-incorrecto"])
def test_clave_de_cifrado_invalida_impide_arrancar(clave):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql://x",
            JWT_SECRET_KEY="k" * 32,
            CONVOCATION_CODE_ENCRYPTION_KEY=clave,
        )


# --- Cifrado de códigos -------------------------------------------------------------------------


def test_cifrado_ida_y_vuelta_con_formato_versionado():
    conv_id = uuid.uuid4()

    cifrado = cifrar_codigo(CODIGO, conv_id)

    assert cifrado.startswith("v1.") and len(cifrado) == 57
    assert CODIGO not in cifrado
    assert descifrar_codigo(cifrado, conv_id) == CODIGO


@pytest.mark.parametrize(
    "valor",
    ["sin-punto", "v2.AAAA", "v1.", "v1.AAAA", "v1.@@@@", "v1.ñ"],
)
def test_valores_almacenados_invalidos(valor):
    with pytest.raises(CodigoCifradoInvalidoError):
        descifrar_codigo(valor, uuid.uuid4())


def test_cifrado_ligado_a_su_convocatoria():
    with pytest.raises(CodigoCifradoInvalidoError):
        descifrar_codigo(cifrar_codigo(CODIGO, uuid.uuid4()), uuid.uuid4())


# --- Códigos y huellas --------------------------------------------------------------------------


def test_codigo_generado_respeta_longitud_y_alfabeto():
    for _ in range(50):
        codigo = generar_codigo()
        assert len(codigo) == LONGITUD_CODIGO
        assert set(codigo) <= set(ALFABETO_CODIGO)


def test_alfabeto_evita_caracteres_confundibles():
    assert not set("ILOU") & set(ALFABETO_CODIGO)


def test_huella_es_determinista_y_depende_del_valor():
    assert huella_codigo("v1.abc") == huella_codigo("v1.abc")
    assert huella_codigo("v1.abc") != huella_codigo("v1.abd")
    assert len(huella_codigo("v1.abc")) == 32


# --- Token estudiantil --------------------------------------------------------------------------


def _token(**cambios) -> str:
    datos = {
        "estudiante_id": uuid.uuid4(),
        "convocatoria_id": uuid.uuid4(),
        "sesion_id": uuid.uuid4(),
        "version_usuario": 0,
        "huella_codigo": "abc",
        "duracion": timedelta(minutes=5),
    } | cambios
    return crear_token_estudiante(**datos)


def test_token_estudiantil_conserva_su_alcance():
    conv_id, sesion_id = uuid.uuid4(), uuid.uuid4()

    claims = decodificar_token_estudiante(_token(convocatoria_id=conv_id, sesion_id=sesion_id, version_usuario=3))

    assert (claims.convocatoria_id, claims.sesion_id, claims.version_usuario) == (conv_id, sesion_id, 3)


def test_tokens_no_son_intercambiables_entre_alcances():
    with pytest.raises(security.InvalidTokenError):
        security.decode_access_token(_token())
    with pytest.raises(security.InvalidTokenError):
        decodificar_token_estudiante(security.create_access_token(uuid.uuid4(), "ESTUDIANTE", 0))


def test_token_estudiantil_expirado():
    with pytest.raises(security.InvalidTokenError):
        decodificar_token_estudiante(_token(duracion=timedelta(seconds=-1)))


@pytest.mark.parametrize(
    "cambios",
    [{"conv": "x"}, {"ses": "x"}, {"sub": "x"}, {"ver": True}, {"ver": "1"}, {"conv": None}],
    ids=["conv", "ses", "sub", "ver-bool", "ver-texto", "conv-nulo"],
)
def test_claims_estudiantiles_mal_formados(cambios):
    settings = get_settings()
    payload = {
        "sub": str(uuid.uuid4()),
        "conv": str(uuid.uuid4()),
        "ses": str(uuid.uuid4()),
        "ver": 0,
        "cod": "abc",
        "type": TIPO_TOKEN_ESTUDIANTIL,
        "iat": 1,
        "exp": 4_000_000_000,
    } | cambios

    with pytest.raises(security.InvalidTokenError):
        decodificar_token_estudiante(jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


# --- Limitador de intentos ----------------------------------------------------------------------


@pytest.fixture
def reloj():
    return RelojFijo(T8)


@pytest.fixture
def limitador(reloj):
    return LimitadorIntentos(reloj, timedelta(seconds=60), max_por_correo=3, max_por_ip=5)


def _fallar(limitador, veces, correo=CORREO, ip="10.0.0.1"):
    for _ in range(veces):
        limitador.registrar_fallo(correo, ip)


def test_por_debajo_del_limite_no_bloquea(limitador):
    _fallar(limitador, 2)

    limitador.verificar(CORREO, "10.0.0.1")


def test_al_alcanzar_el_limite_bloquea_con_retry_after(limitador, reloj):
    _fallar(limitador, 3)
    reloj.avanzar(seconds=10)

    with pytest.raises(DemasiadosIntentosError) as error:
        limitador.verificar(CORREO, "10.0.0.1")

    assert error.value.status_code == 429
    assert error.value.reintentar_en == 50
    assert error.value.headers == {"Retry-After": "50"}


def test_bloqueo_termina_exactamente_al_salir_de_la_ventana(limitador, reloj):
    _fallar(limitador, 3)
    reloj.avanzar(seconds=59)
    with pytest.raises(DemasiadosIntentosError):
        limitador.verificar(CORREO, "10.0.0.1")

    reloj.avanzar(seconds=1)

    limitador.verificar(CORREO, "10.0.0.1")


def test_ventana_es_deslizante(limitador, reloj):
    _fallar(limitador, 2)
    reloj.avanzar(seconds=40)
    _fallar(limitador, 1)
    reloj.avanzar(seconds=30)  # los dos primeros fallos salen de la ventana

    limitador.verificar(CORREO, "10.0.0.1")


def test_limite_por_ip_afecta_a_cualquier_correo(limitador):
    for i in range(5):
        limitador.registrar_fallo(f"e{i}@u.edu.co", "10.0.0.1")

    with pytest.raises(DemasiadosIntentosError):
        limitador.verificar("nuevo@u.edu.co", "10.0.0.1")
    limitador.verificar("nuevo@u.edu.co", "10.0.0.2")


def test_exito_limpia_el_correo_pero_no_la_ip(limitador):
    _fallar(limitador, 3)

    limitador.registrar_exito(CORREO)
    limitador.verificar(CORREO, "10.0.0.9")

    _fallar(limitador, 2, correo="otro@u.edu.co")
    with pytest.raises(DemasiadosIntentosError):
        limitador.verificar("tercero@u.edu.co", "10.0.0.1")


def test_reiniciar_vacia_todo(limitador):
    _fallar(limitador, 3)

    limitador.reiniciar()

    limitador.verificar(CORREO, "10.0.0.1")


def test_limitador_no_acumula_memoria_por_claves_caducadas(limitador, reloj):
    # Regresión DEF-05: cada correo o IP dejaba una entrada para siempre.
    for i in range(100):
        limitador.registrar_fallo(f"e{i}@u.edu.co", f"10.0.0.{i}")
    reloj.avanzar(seconds=61)

    limitador.verificar("nuevo@u.edu.co", "10.0.1.1")

    assert len(limitador._fallos) == 0


def test_consultar_sin_fallos_no_crea_entradas(limitador):
    limitador.verificar(CORREO, "10.0.0.1")

    assert len(limitador._fallos) == 0


def test_limpieza_no_libera_un_bloqueo_vigente(limitador, reloj):
    _fallar(limitador, 1, correo="viejo@u.edu.co", ip="10.0.0.2")
    reloj.avanzar(seconds=30)
    _fallar(limitador, 3)
    reloj.avanzar(seconds=31)  # el primer fallo caducó; los tres últimos siguen vigentes

    limitador.registrar_fallo("otro@u.edu.co", "10.0.0.3")  # dispara la limpieza

    assert ("correo", "viejo@u.edu.co") not in limitador._fallos
    with pytest.raises(DemasiadosIntentosError):
        limitador.verificar(CORREO, "10.0.0.9")


def test_limpieza_completa_ocurre_como_maximo_una_vez_por_ventana(limitador, reloj):
    limitador.registrar_fallo("a@u.edu.co", "10.0.0.1")
    reloj.avanzar(seconds=59)
    limitador.registrar_fallo("b@u.edu.co", "10.0.0.2")
    reloj.avanzar(seconds=2)  # 61 s después de la primera limpieza: toca otra pasada y "a" ya caducó

    limitador.registrar_fallo("c@u.edu.co", "10.0.0.3")
    reloj.avanzar(seconds=58)  # solo 58 s desde esa limpieza: "b" caducó, pero no se recorre todo otra vez

    limitador.registrar_fallo("d@u.edu.co", "10.0.0.4")

    assert ("correo", "a@u.edu.co") not in limitador._fallos
    assert ("correo", "b@u.edu.co") in limitador._fallos


def test_clave_caducada_se_elimina_al_consultarla_entre_limpiezas(limitador, reloj):
    limitador.verificar("a@u.edu.co", "10.0.0.1")  # primera limpieza; la siguiente será a los 60 s
    reloj.avanzar(seconds=30)
    limitador.registrar_fallo(CORREO, "10.0.0.2")
    reloj.avanzar(seconds=30)
    limitador.verificar("a@u.edu.co", "10.0.0.1")  # segunda limpieza: el fallo sigue vigente
    reloj.avanzar(seconds=30)

    limitador.verificar(CORREO, "10.0.0.2")  # 90 s: aún no toca limpiar, pero el fallo ya caducó

    assert len(limitador._fallos) == 0


# --- Transacciones y eventos --------------------------------------------------------------------


def _servicio(clase=ServicioSesionesBase, **extra):
    publicador = PublicadorEspia()
    servicio = clase(sesion_bd(), RelojFijo(T8), publicador, **extra)
    servicio._espacios = MagicMock()
    servicio._sesiones = MagicMock()
    servicio._convocatorias = MagicMock()
    servicio._estudiantes = MagicMock()
    return servicio, publicador


def test_eventos_se_publican_despues_del_commit_y_en_orden():
    servicio, publicador = _servicio()
    orden = []
    servicio._session.commit.side_effect = lambda: orden.append(list(publicador.tipos))

    with servicio._transaction():
        servicio._emitir(eventos.EventoSesion("A", uuid.uuid4()))
        servicio._emitir(eventos.EventoSesion("B", uuid.uuid4()))

    assert orden == [[]]  # en el commit todavía no se había publicado nada
    assert publicador.tipos == ["A", "B"]


@pytest.mark.parametrize("error", [error_integridad("otra"), ValueError("fallo")], ids=["integridad", "generico"])
def test_fallo_descarta_los_eventos_pendientes(error):
    servicio, publicador = _servicio()

    with pytest.raises((BusinessRuleError, ValueError)):
        with servicio._transaction():
            servicio._emitir(eventos.EventoSesion("A", uuid.uuid4()))
            raise error

    assert publicador.recibidos == []
    servicio._session.rollback.assert_called_once()
    with servicio._transaction():
        pass
    assert publicador.recibidos == []  # no quedaron pendientes para la siguiente transacción


@pytest.mark.parametrize(
    ("restriccion", "tipo"),
    [
        ("uq_espacio_academico_nombre_periodo", ConflictError),
        ("uq_convocatoria_sesion_estudiante", ConflictError),
        ("ck_sesion_caso_publicado", ConflictError),
        ("ck_sesion_tiempo_max_hasta_estimado", BusinessRuleError),
        ("ck_sesion_responsable_docente", BusinessRuleError),
        ("ck_sesion_inicio_antes_de_fin", BusinessRuleError),
        ("ck_convocatoria_estudiante_rol", BusinessRuleError),
        ("desconocida", BusinessRuleError),
    ],
)
def test_traduccion_de_restricciones(restriccion, tipo):
    assert type(_traducir(error_integridad(restriccion))) is tipo


def test_evento_de_convocatoria_nunca_lleva_codigo():
    conv_id = uuid.uuid4()

    evento = eventos.evento_convocatoria(eventos.CODIGO_REGENERADO, uuid.uuid4(), conv_id, motivo="Expuesto")

    assert evento.datos == {"convocatoria_id": str(conv_id), "motivo": "Expuesto"}


# --- Reglas de programación ---------------------------------------------------------------------


def _validar(inicio=T8, fin=T10, tiempo=40, ahora=T8, estimado=45, fechas_cambiadas=True):
    caso = caso_valido()
    caso.tiempo_estimado_min = estimado
    SesionService._validar_condiciones(caso, inicio, fin, tiempo, ahora, fechas_cambiadas=fechas_cambiadas)


@pytest.mark.parametrize(
    "condiciones",
    [
        {"inicio": T10, "fin": T10},
        {"inicio": T10, "fin": T8},
        {"fin": T9, "ahora": T9},
        {"fin": T9, "ahora": T10},
        {"tiempo": 46},
        {"estimado": None},
    ],
    ids=["inicio-igual-fin", "inicio-tras-fin", "fin-igual-ahora", "fin-pasado", "tiempo-excedido", "sin-estimado"],
)
def test_condiciones_de_programacion_invalidas(condiciones):
    with pytest.raises(BusinessRuleError):
        _validar(**condiciones)


@pytest.mark.parametrize(
    "condiciones",
    [{"tiempo": 45}, {"inicio": T8 - timedelta(days=1), "ahora": T9}, {"fin": T9, "ahora": T10, "fechas_cambiadas": False}],
    ids=["tiempo-igual-estimado", "inicio-pasado-fin-futuro", "fechas-sin-cambios"],
)
def test_condiciones_de_programacion_validas(condiciones):
    _validar(**condiciones)


def test_caso_para_programar_debe_ser_propio_y_publicado():
    servicio, _ = _servicio(SesionService)
    with patch.object(sesion_service, "CasoRepository") as repositorio:
        repositorio.return_value.get_owned.return_value = None
        with pytest.raises(NotFoundError):
            servicio._caso_programable(usuario(), uuid.uuid4())

        for estado in (EstadoCasoEnum.BORRADOR, EstadoCasoEnum.ARCHIVADO):
            repositorio.return_value.get_owned.return_value = caso_valido(estado)
            with pytest.raises(ConflictError, match=estado.value):
                servicio._caso_programable(usuario(), uuid.uuid4())

        publicado = caso_valido(EstadoCasoEnum.PUBLICADO)
        repositorio.return_value.get_owned.return_value = publicado
        assert servicio._caso_programable(usuario(), uuid.uuid4()) is publicado


def test_espacio_para_programar_debe_existir_y_estar_activo():
    servicio, _ = _servicio(SesionService)

    servicio._espacios.get.return_value = None
    with pytest.raises(NotFoundError):
        servicio._espacio_activo(uuid.uuid4())

    servicio._espacios.get.return_value = EspacioAcademico(activo=False)
    with pytest.raises(BusinessRuleError, match="inactivo"):
        servicio._espacio_activo(uuid.uuid4())

    activo = EspacioAcademico(activo=True)
    servicio._espacios.get.return_value = activo
    assert servicio._espacio_activo(uuid.uuid4()) is activo


@pytest.mark.parametrize("campo", sesion_service.CAMPOS_OBLIGATORIOS)
def test_actualizar_sesion_no_vacia_campos_obligatorios(campo):
    servicio, _ = _servicio(SesionService)

    with pytest.raises(BusinessRuleError, match=campo):
        servicio.actualizar(usuario(), uuid.uuid4(), SesionUpdate(**{campo: None}))


def test_actualizar_sin_cambios_no_abre_transaccion():
    servicio, publicador = _servicio(SesionService)
    actual = sesion(docente_id=uuid.uuid4())
    servicio._sesiones.get_owned.return_value = actual

    assert servicio.actualizar(usuario(), actual.id, SesionUpdate()) is actual

    servicio._session.commit.assert_not_called()
    assert publicador.recibidos == []


def test_cambio_informativo_no_revalida_condiciones_y_emite_campos():
    servicio, publicador = _servicio(SesionService)
    actual = sesion(EstadoSesionEnum.SIN_INICIAR, caso=caso_valido())
    servicio._sesiones.get_owned.return_value = actual

    servicio.actualizar(usuario(), actual.id, SesionUpdate(mensaje="Hola", lugar="Aula"))

    assert (actual.mensaje, actual.lugar) == ("Hola", "Aula")
    assert publicador.recibidos[0].datos == {"campos": "lugar,mensaje"}


def test_docente_no_iniciado_antes_de_hora_no_emite_evento():
    servicio, publicador = _servicio(SesionService)
    servicio._reloj = RelojFijo(T8 - timedelta(minutes=1))
    servicio._sesiones.get_owned.return_value = sesion(EstadoSesionEnum.SIN_INICIAR)

    with pytest.raises(TransicionSesionNoPermitidaError):
        servicio.iniciar(usuario(), uuid.uuid4())

    assert publicador.recibidos == []


def test_iniciar_y_finalizar_emiten_su_evento_y_cierran_participaciones():
    servicio, publicador = _servicio(SesionService)
    actual = sesion(EstadoSesionEnum.SIN_INICIAR)
    servicio._sesiones.get_owned.return_value = actual
    puerto = MagicMock()

    with patch.object(integracion, "_cierre", puerto):
        servicio.iniciar(usuario(), actual.id)
        servicio.finalizar(usuario(), actual.id)

    assert actual.estado == EstadoSesionEnum.FINALIZADO
    assert publicador.tipos == [eventos.SESION_INICIADA, eventos.SESION_FINALIZADA]
    puerto.cerrar_por_fin_de_sesion.assert_called_once_with(servicio._session, actual.id, T8)


def test_sesion_ajena_responde_404():
    servicio, _ = _servicio(SesionService)
    servicio._sesiones.get_owned.return_value = None

    for operacion in (servicio.iniciar, servicio.finalizar, servicio.obtener):
        with pytest.raises(NotFoundError):
            operacion(usuario(), uuid.uuid4())


def test_listar_cuenta_convocatorias_de_la_pagina():
    servicio, _ = _servicio(SesionService)
    actual = sesion()
    servicio._sesiones.list_by_docente.return_value = ([actual], 1)
    servicio._sesiones.contar_convocatorias.return_value = {actual.id: 4}

    assert servicio.listar(usuario(), estado=None, skip=0, limit=5) == ([actual], 1, {actual.id: 4})
    assert servicio.total_convocatorias(actual) == 4


# --- Convocatorias ------------------------------------------------------------------------------


def _convocador(clase=SesionService):
    servicio, publicador = _servicio(clase)
    servicio._convocatorias.por_estudiante.return_value = {}
    servicio._convocatorias.cifrados_de_estudiante.return_value = []
    return servicio, publicador


def test_convocar_sin_correos_no_consulta_nada():
    servicio, _ = _convocador()

    assert servicio._convocar(sesion(), []) == []

    servicio._estudiantes.por_correos.assert_not_called()


@pytest.mark.parametrize(
    ("encontrados", "tipo", "mensaje"),
    [
        ({}, BusinessRuleError, "no corresponden"),
        ({CORREO: {"rol": RolUsuario.DOCENTE}}, BusinessRuleError, "no corresponden"),
        ({CORREO: {"rol": RolUsuario.ESTUDIANTE, "activo": False}}, BusinessRuleError, "desactivadas"),
    ],
    ids=["no-registrado", "no-estudiante", "inactivo"],
)
def test_convocar_valida_todos_los_correos_antes_de_insertar(encontrados, tipo, mensaje):
    servicio, _ = _convocador()
    servicio._estudiantes.por_correos.return_value = {
        correo: usuario(datos.pop("rol"), **datos) for correo, datos in {k: dict(v) for k, v in encontrados.items()}.items()
    }

    with pytest.raises(tipo, match=mensaje):
        servicio._convocar(sesion(), [CORREO])

    servicio._convocatorias.add.assert_not_called()


def test_convocar_rechaza_estudiante_ya_convocado():
    servicio, _ = _convocador()
    estudiante = usuario(RolUsuario.ESTUDIANTE)
    servicio._estudiantes.por_correos.return_value = {CORREO: estudiante}
    servicio._convocatorias.por_estudiante.return_value = {estudiante.id: convocatoria(estudiante=estudiante)}

    with pytest.raises(ConflictError):
        servicio._convocar(sesion(), [CORREO])


def test_convocatoria_nueva_cifra_con_su_id_y_tiene_un_intento():
    servicio, publicador = _convocador()
    estudiante = usuario(RolUsuario.ESTUDIANTE)
    actual = sesion()
    servicio._estudiantes.por_correos.return_value = {CORREO: estudiante}

    creada = servicio._convocar(actual, [CORREO])[0]

    assert creada.intentos_autorizados == 1
    assert len(descifrar_codigo(creada.codigo_autorizacion, creada.id)) == LONGITUD_CODIGO
    servicio._convocatorias.bloquear_codigos_de_estudiantes.assert_called_once_with([estudiante.id])
    assert servicio._pendientes[0].tipo == eventos.CONVOCATORIA_CREADA


def test_reconvocar_reactiva_la_misma_fila_con_codigo_nuevo():
    servicio, _ = _convocador()
    estudiante = usuario(RolUsuario.ESTUDIANTE)
    revocada = convocatoria(estudiante=estudiante, revocada_en=T8)
    servicio._estudiantes.por_correos.return_value = {CORREO: estudiante}
    servicio._convocatorias.por_estudiante.return_value = {estudiante.id: revocada}

    resultado = servicio._convocar(sesion(), [CORREO])

    assert resultado == [revocada]
    assert revocada.activa
    assert descifrar_codigo(revocada.codigo_autorizacion, revocada.id) != CODIGO
    servicio._convocatorias.add.assert_not_called()
    assert servicio._pendientes[0].tipo == eventos.CONVOCATORIA_REACTIVADA


def test_codigo_unico_evita_los_del_estudiante_e_ignora_cifrados_danados():
    servicio, _ = _convocador()
    conv_id = uuid.uuid4()
    servicio._convocatorias.cifrados_de_estudiante.return_value = [
        (conv_id, cifrar_codigo("YA0000000000", conv_id)),
        (uuid.uuid4(), "v1.dañado"),
    ]

    with patch.object(sesion_service, "generar_codigo", side_effect=["YA0000000000", "LIBRE0000000"]):
        assert servicio._codigo_unico(uuid.uuid4()) == "LIBRE0000000"


def test_codigo_unico_se_rinde_tras_colisiones_repetidas():
    servicio, _ = _convocador()
    conv_id = uuid.uuid4()
    servicio._convocatorias.cifrados_de_estudiante.return_value = [(conv_id, cifrar_codigo("YA0000000000", conv_id))]

    with patch.object(sesion_service, "generar_codigo", return_value="YA0000000000") as generador:
        with pytest.raises(ConflictError):
            servicio._codigo_unico(uuid.uuid4())

    assert generador.call_count == sesion_service.INTENTOS_GENERACION_CODIGO


def _gestor(actual=None, conv=None):
    servicio, publicador = _convocador(ConvocatoriaService)
    actual = actual or sesion()
    conv = conv or convocatoria(actual)
    servicio._sesiones.get_owned.return_value = actual
    servicio._convocatorias.get.return_value = conv
    return servicio, publicador, actual, conv


def test_codigo_danado_pide_regenerar():
    servicio, _, actual, conv = _gestor()
    conv.codigo_autorizacion = "v1.dañado"

    with pytest.raises(ConflictError, match="regenérelo"):
        servicio.obtener_codigo(usuario(), actual.id, conv.id)


def test_codigo_de_convocatoria_revocada_no_se_entrega():
    servicio, _, actual, conv = _gestor()
    conv.revocada_en = T8

    with pytest.raises(ConflictError, match="revocada"):
        servicio.obtener_codigo(usuario(), actual.id, conv.id)


def test_codigo_consultado_se_registra_sin_revelarlo(caplog):
    servicio, _, actual, conv = _gestor()

    with caplog.at_level(logging.INFO, logger="iter_animi.sesiones"):
        leido = servicio.obtener_codigo(usuario(), actual.id, conv.id)

    assert leido.codigo_autorizacion == CODIGO
    assert CODIGO not in caplog.text


def test_convocatoria_de_otra_sesion():
    servicio, _, actual, _conv = _gestor()
    servicio._convocatorias.get.return_value = None

    with pytest.raises(NotFoundError):
        servicio.obtener(usuario(), actual.id, uuid.uuid4())


@pytest.mark.parametrize("intentos", [1, 0])
def test_intentos_solo_pueden_aumentar(intentos):
    servicio, publicador, actual, conv = _gestor()

    with pytest.raises(BusinessRuleError, match="aumentar"):
        servicio.modificar_intentos(usuario(), actual.id, conv.id, intentos, "Motivo válido")

    assert publicador.recibidos == []


def test_aumento_de_intentos_emite_evento_con_motivo():
    servicio, publicador, actual, conv = _gestor()

    servicio.modificar_intentos(usuario(), actual.id, conv.id, 3, "Falla eléctrica")

    assert conv.intentos_autorizados == 3
    assert publicador.recibidos[0].datos | {"convocatoria_id": ""} == {"convocatoria_id": "", "intentos": "3", "motivo": "Falla eléctrica"}


def test_retirar_es_logico_y_cierra_el_intento_activo():
    servicio, publicador, actual, conv = _gestor()
    puerto = MagicMock()

    with patch.object(integracion, "_cierre", puerto):
        servicio.retirar(usuario(), actual.id, conv.id)

    assert conv.revocada_en == T8
    puerto.cerrar_por_revocacion.assert_called_once_with(servicio._session, conv.id, T8)
    assert publicador.tipos == [eventos.CONVOCATORIA_RETIRADA]


def test_regenerar_cambia_el_codigo_bajo_bloqueo():
    servicio, publicador, actual, conv = _gestor()
    anterior = conv.codigo_autorizacion

    leido = servicio.regenerar_codigo(usuario(), actual.id, conv.id, "Código expuesto")

    assert conv.codigo_autorizacion != anterior
    assert descifrar_codigo(conv.codigo_autorizacion, conv.id) == leido.codigo_autorizacion
    servicio._convocatorias.bloquear_codigos_de_estudiantes.assert_called_once_with([conv.estudiante_id])
    assert "codigo_autorizacion" not in str(publicador.recibidos[0].datos)


@pytest.mark.parametrize(
    "operacion",
    [
        lambda s, a, c: s.retirar(usuario(), a.id, c.id),
        lambda s, a, c: s.modificar_intentos(usuario(), a.id, c.id, 5, "Motivo válido"),
        lambda s, a, c: s.regenerar_codigo(usuario(), a.id, c.id, "Motivo válido"),
    ],
    ids=["retirar", "intentos", "regenerar"],
)
def test_sesion_finalizada_congela_las_convocatorias(operacion):
    servicio, publicador, actual, conv = _gestor(sesion(EstadoSesionEnum.FINALIZADO))

    with pytest.raises(TransicionSesionNoPermitidaError):
        operacion(servicio, actual, conv)

    assert publicador.recibidos == []


# --- Acceso estudiantil -------------------------------------------------------------------------


def _acceso(reloj_actual=T8, reingreso=None):
    servicio = AccesoEstudiantilService(sesion_bd(), RelojFijo(reloj_actual), reingreso=reingreso)
    servicio._convocatorias = MagicMock()
    return servicio


def _limitador_doble():
    return MagicMock(spec=LimitadorIntentos)


def test_ingreso_correcto_emite_token_con_el_alcance_de_la_convocatoria():
    servicio = _acceso()
    conv = convocatoria()
    servicio._convocatorias.de_estudiante_por_correo.return_value = [conv]
    limitador = _limitador_doble()

    respuesta = servicio.ingresar(CORREO, CODIGO, "10.0.0.1", limitador)

    claims = decodificar_token_estudiante(respuesta.access_token)
    assert claims.convocatoria_id == conv.id
    assert claims.huella_codigo == huella_codigo(conv.codigo_autorizacion)
    assert respuesta.expires_in == get_settings().STUDENT_TOKEN_EXPIRE_MINUTES * 60
    limitador.registrar_exito.assert_called_once_with(CORREO)
    limitador.registrar_fallo.assert_not_called()


@pytest.mark.parametrize("codigo", ["CORTO", "X" * 13], ids=["corto", "largo"])
def test_codigo_de_longitud_incorrecta_no_consulta_la_base(codigo):
    servicio = _acceso()
    limitador = _limitador_doble()

    with pytest.raises(AuthenticationError):
        servicio.ingresar(CORREO, codigo, "10.0.0.1", limitador)

    servicio._convocatorias.de_estudiante_por_correo.assert_not_called()
    limitador.registrar_fallo.assert_called_once_with(CORREO, "10.0.0.1")


def test_codigo_con_caracteres_no_ascii_es_credencial_invalida():
    # Regresión DEF-01: hmac.compare_digest lanzaba TypeError con texto no ASCII (HTTP 500 sin contar el fallo).
    servicio = _acceso()
    servicio._convocatorias.de_estudiante_por_correo.return_value = [convocatoria()]
    limitador = _limitador_doble()

    with pytest.raises(AuthenticationError):
        servicio.ingresar(CORREO, "ÑÑÑÑÑÑÑÑÑÑÑÑ", "10.0.0.1", limitador)

    limitador.registrar_fallo.assert_called_once_with(CORREO, "10.0.0.1")


def test_verificacion_del_limitador_precede_a_la_consulta():
    servicio = _acceso()
    limitador = _limitador_doble()
    limitador.verificar.side_effect = DemasiadosIntentosError(30)

    with pytest.raises(DemasiadosIntentosError):
        servicio.ingresar(CORREO, CODIGO, "10.0.0.1", limitador)

    servicio._convocatorias.de_estudiante_por_correo.assert_not_called()


def test_cifrado_danado_nunca_coincide(caplog):
    servicio = _acceso()
    conv = convocatoria()
    conv.codigo_autorizacion = "v1.dañado"
    servicio._convocatorias.de_estudiante_por_correo.return_value = [conv]

    with caplog.at_level(logging.ERROR, logger="iter_animi.sesiones.acceso"):
        with pytest.raises(AuthenticationError):
            servicio.ingresar(CORREO, CODIGO, "10.0.0.1", _limitador_doble())

    assert str(conv.id) in caplog.text


def test_usuario_no_estudiante_con_convocatoria_se_ignora():
    servicio = _acceso()
    servicio._convocatorias.de_estudiante_por_correo.return_value = [convocatoria(estudiante=usuario(RolUsuario.DOCENTE))]

    with pytest.raises(AuthenticationError):
        servicio.ingresar(CORREO, CODIGO, "10.0.0.1", _limitador_doble())


def test_ingreso_identifica_la_convocatoria_correcta_entre_varias():
    servicio = _acceso()
    otra, buscada = convocatoria(codigo="OTRO00000000"), convocatoria()
    servicio._convocatorias.de_estudiante_por_correo.return_value = [otra, buscada]

    respuesta = servicio.ingresar(CORREO, CODIGO, "10.0.0.1", _limitador_doble())

    assert respuesta.convocatoria_id == buscada.id


@pytest.mark.parametrize(
    "alterar",
    [
        lambda c: setattr(c, "revocada_en", T8),
        lambda c: setattr(c.estudiante, "activo", False),
    ],
    ids=["revocada", "cuenta-inactiva"],
)
def test_credenciales_validas_con_acceso_cerrado_informan_el_motivo(alterar):
    servicio = _acceso()
    conv = convocatoria()
    alterar(conv)
    servicio._convocatorias.de_estudiante_por_correo.return_value = [conv]

    with pytest.raises(PermissionDeniedError):
        servicio.ingresar(CORREO, CODIGO, "10.0.0.1", _limitador_doble())


def _reingreso(permite: bool) -> MagicMock:
    politica = MagicMock()
    politica.permite_reingreso.return_value = permite
    return politica


def test_fuera_de_ventana_la_politica_decide_el_reingreso():
    tarde = T9 + timedelta(minutes=1)
    conv = convocatoria()

    autorizado = _acceso(tarde, _reingreso(True))
    autorizado._convocatorias.de_estudiante_por_correo.return_value = [conv]
    assert autorizado.ingresar(CORREO, CODIGO, "10.0.0.1", _limitador_doble()).access_token

    rechazado = _acceso(tarde, _reingreso(False))
    rechazado._convocatorias.de_estudiante_por_correo.return_value = [conv]
    with pytest.raises(IngresoNoPermitidoError):
        rechazado.ingresar(CORREO, CODIGO, "10.0.0.1", _limitador_doble())


@pytest.mark.parametrize("estado", [EstadoSesionEnum.SIN_INICIAR, EstadoSesionEnum.FINALIZADO])
def test_politica_de_reingreso_no_se_consulta_si_la_sesion_no_esta_en_curso(estado):
    politica = _reingreso(True)
    servicio = _acceso(T8, politica)
    servicio._convocatorias.de_estudiante_por_correo.return_value = [convocatoria(sesion(estado))]

    with pytest.raises(IngresoNoPermitidoError):
        servicio.ingresar(CORREO, CODIGO, "10.0.0.1", _limitador_doble())

    politica.permite_reingreso.assert_not_called()


def test_politica_por_defecto_nunca_permite_reingreso():
    assert SinReingreso().permite_reingreso(convocatoria(), T8) is False
    assert isinstance(_acceso()._reingreso, SinReingreso)


def _token_de(conv: Convocatoria, **cambios) -> str:
    datos = {
        "estudiante_id": conv.estudiante_id,
        "convocatoria_id": conv.id,
        "sesion_id": conv.sesion_id,
        "version_usuario": conv.estudiante.version_token,
        "huella_codigo": huella_codigo(conv.codigo_autorizacion),
        "duracion": timedelta(minutes=5),
    } | cambios
    return crear_token_estudiante(**datos)


def test_resolver_devuelve_el_acceso_vigente():
    servicio = _acceso()
    conv = convocatoria()
    servicio._convocatorias.get_by_id.return_value = conv

    acceso = servicio.resolver(_token_de(conv), sesion_id=conv.sesion_id)

    assert (acceso.estudiante, acceso.convocatoria, acceso.sesion) == (conv.estudiante, conv, conv.sesion)


@pytest.mark.parametrize(
    "invalidar",
    [
        lambda c: setattr(c, "revocada_en", T8),
        lambda c: setattr(c, "codigo_autorizacion", "v1.regenerado"),
        lambda c: setattr(c, "estudiante_id", uuid.uuid4()),
        lambda c: setattr(c, "sesion_id", uuid.uuid4()),
        lambda c: setattr(c.estudiante, "activo", False),
        lambda c: setattr(c.estudiante, "version_token", 9),
        lambda c: setattr(c.estudiante, "rol", RolUsuario.DOCENTE),
        lambda c: setattr(c.sesion, "estado", EstadoSesionEnum.FINALIZADO),
    ],
    ids=["revocada", "codigo-regenerado", "otro-estudiante", "otra-sesion", "inactivo", "version", "rol", "finalizada"],
)
def test_token_deja_de_autorizar_cuando_cambia_el_estado_actual(invalidar):
    servicio = _acceso()
    conv = convocatoria()
    token = _token_de(conv)
    invalidar(conv)
    servicio._convocatorias.get_by_id.return_value = conv

    with pytest.raises(AuthenticationError):
        servicio.resolver(token)


def test_token_de_convocatoria_inexistente_o_mal_formado():
    servicio = _acceso()
    servicio._convocatorias.get_by_id.return_value = None

    for token in (_token_de(convocatoria()), "basura"):
        with pytest.raises(AuthenticationError):
            servicio.resolver(token)


def test_token_no_sirve_para_otra_sesion():
    servicio = _acceso()
    conv = convocatoria()
    servicio._convocatorias.get_by_id.return_value = conv

    with pytest.raises(PermissionDeniedError):
        servicio.resolver(_token_de(conv), sesion_id=uuid.uuid4())


@pytest.mark.parametrize(
    ("estado", "ahora", "esperado"),
    [
        (EstadoSesionEnum.EN_CURSO, T8, True),
        (EstadoSesionEnum.EN_CURSO, T9, True),
        (EstadoSesionEnum.EN_CURSO, T9 + timedelta(seconds=1), False),
        (EstadoSesionEnum.SIN_INICIAR, T8, False),
        (EstadoSesionEnum.FINALIZADO, T8, False),
    ],
)
def test_puede_iniciar_participacion(estado, ahora, esperado):
    conv = convocatoria(sesion(estado))

    assert AccesoEstudiantil(conv.estudiante, conv, conv.sesion).puede_iniciar_participacion(ahora) is esperado


# --- Cierre automático --------------------------------------------------------------------------


def _cierre(ahora=T10 + timedelta(hours=25)):
    servicio = CierreSesionesService(sesion_bd(), RelojFijo(ahora), PublicadorEspia())
    servicio._sesiones = MagicMock()
    return servicio


def test_plazo_de_cierre_viene_de_la_configuracion():
    assert plazo_cierre() == timedelta(hours=get_settings().SESSION_AUTO_CLOSE_HOURS)


@pytest.mark.parametrize("estado", [EstadoSesionEnum.SIN_INICIAR, EstadoSesionEnum.EN_CURSO])
def test_cierre_finaliza_vencidas_y_notifica(estado):
    servicio = _cierre()
    vencida = sesion(estado)
    servicio._sesiones.ids_vencidas.return_value = [vencida.id]
    servicio._sesiones.get_for_update_skip_locked.return_value = vencida
    puerto = MagicMock()

    with patch.object(integracion, "_cierre", puerto):
        assert servicio.cerrar_vencidas(timedelta(hours=24)) == [vencida.id]

    assert vencida.estado == EstadoSesionEnum.FINALIZADO
    puerto.cerrar_por_fin_de_sesion.assert_called_once()
    assert servicio._eventos.recibidos[0].datos == {"origen": "cierre_automatico", "anterior": estado.value}


def test_cierre_omite_sesiones_bloqueadas_o_ya_finalizadas():
    servicio = _cierre()
    servicio._sesiones.ids_vencidas.return_value = [uuid.uuid4(), uuid.uuid4()]
    servicio._sesiones.get_for_update_skip_locked.side_effect = [None, sesion(EstadoSesionEnum.FINALIZADO)]
    puerto = MagicMock()

    with patch.object(integracion, "_cierre", puerto):
        assert servicio.cerrar_vencidas(timedelta(hours=24)) == []

    puerto.cerrar_por_fin_de_sesion.assert_not_called()
    assert servicio._eventos.recibidos == []


def test_cierre_busca_con_el_plazo_de_gracia_y_el_lote():
    ahora = T10 + timedelta(hours=25)
    servicio = _cierre(ahora)
    servicio._sesiones.ids_vencidas.return_value = []

    servicio.cerrar_vencidas(timedelta(hours=2), lote=7)

    servicio._sesiones.ids_vencidas.assert_called_once_with(ahora - timedelta(hours=2), 7)


# --- Worker de cierre ---------------------------------------------------------------------------


def test_limitador_es_instancia_unica_del_proceso():
    assert get_limitador() is get_limitador()


def test_worker_exporta_su_interfaz():
    assert session_expiration.ejecutar_una_vez is worker.ejecutar_una_vez


def test_pasada_del_worker_conecta_integraciones_y_cierra_la_sesion():
    fabrica = MagicMock()
    with (
        patch.object(worker, "configurar_integraciones") as configurar,
        patch.object(worker, "CierreSesionesService") as servicio,
    ):
        servicio.return_value.cerrar_vencidas.return_value = [uuid.uuid4()]
        resultado = worker.ejecutar_una_vez(fabrica, plazo=timedelta(0))

    configurar.assert_called_once()
    assert len(resultado) == 1
    fabrica.return_value.__exit__.assert_called_once()


def test_bucle_del_worker_sobrevive_a_una_pasada_fallida(caplog):
    class Detener(Exception):
        pass

    with (
        patch.object(worker, "ejecutar_una_vez", side_effect=[RuntimeError("bd caída"), []]) as pasada,
        patch.object(worker.time, "sleep", side_effect=[None, Detener]) as dormir,
    ):
        with pytest.raises(Detener):
            worker.ejecutar_periodicamente(30)

    assert pasada.call_count == 2
    dormir.assert_called_with(30)
    assert "fallo en la pasada" in caplog.text


@pytest.mark.parametrize(("argumentos", "periodico"), [([], False), (["--intervalo", "0"], False), (["--intervalo", "60"], True)])
def test_linea_de_comandos_del_worker(argumentos, periodico):
    with (
        patch.object(worker_cli, "ejecutar_una_vez") as una_vez,
        patch.object(worker_cli, "ejecutar_periodicamente") as bucle,
        patch.object(worker_cli.logging, "basicConfig"),
    ):
        assert worker_cli.main(argumentos) == 0

    assert bucle.called is periodico
    assert una_vez.called is not periodico
