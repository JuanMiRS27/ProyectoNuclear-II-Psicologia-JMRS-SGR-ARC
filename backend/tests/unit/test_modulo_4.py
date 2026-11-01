"""Módulo 4: participaciones, respuestas y cierre de intentos (pruebas unitarias, sin base de datos).

El cálculo de notas y la generación de retroalimentación pertenecen al módulo 5, que aún no
existe: aquí se comprueba que el módulo 4 no inventa esos valores y que entrega los datos
que el módulo 5 necesitará.
"""

import logging
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.core.clock import RelojSistema
from app.core.exceptions import AuthenticationError, BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.modules.participations.dependencies import acceso_estudiantil_actual
from app.modules.participations.models import EstadoParticipacionEnum, MotivoCierreEnum, Respuesta
from app.modules.participations.policies import temporizador
from app.modules.participations.policies.integracion_sesiones import CierreParticipacionesSQL, ReingresoConParticipacionActiva
from app.modules.participations.schemas import (
    CasoCuestionario,
    OpcionCuestionario,
    ParticipacionRead,
    PreguntaCuestionario,
    RespuestaRead,
    RespuestaRequest,
)
from app.modules.participations.services import (
    ConsultaEvaluacionService,
    ExpiracionParticipacionesService,
    ParticipacionParaEvaluacion,
    ParticipacionService,
    RespuestaService,
)
from app.modules.participations.services.base import ServicioParticipacionesBase, _traducir
from app.modules.participations.services.respuesta_service import TIEMPO_AGOTADO
from app.modules.sessions.models import EstadoSesionEnum
from app.modules.sessions.services import AccesoEstudiantil
from app.modules.sessions.states import IngresoNoPermitidoError
from app.workers.participation_expiration import __main__ as worker_cli
from app.workers.participation_expiration import runner as worker
from tests.conftest import RelojFijo
from tests.unit.fabricas import T8, T9, caso_valido, convocatoria, error_integridad, participacion, sesion, sesion_bd

LIMITE = T8 + timedelta(minutes=40)
INTENTO_FIN = {"estado": EstadoParticipacionEnum.FINALIZADA, "fin": T9, "motivo_cierre": MotivoCierreEnum.VOLUNTARIO}


# --- Temporizador ---------------------------------------------------------------------------


def test_fecha_limite_es_inicio_mas_tiempo_maximo():
    assert temporizador.fecha_limite(participacion(), 40) == LIMITE


@pytest.mark.parametrize(
    ("ahora", "segundos"),
    [
        (T8, 2400),
        (LIMITE - timedelta(milliseconds=1), 1),
        (LIMITE - timedelta(seconds=1, milliseconds=500), 2),
        (LIMITE, 0),
        (LIMITE + timedelta(hours=1), 0),
    ],
)
def test_tiempo_restante_redondea_hacia_arriba_y_nunca_es_negativo(ahora, segundos):
    assert temporizador.tiempo_restante_segundos(participacion(), 40, ahora) == segundos


def test_intento_cerrado_no_tiene_tiempo_restante():
    assert temporizador.tiempo_restante_segundos(participacion(**INTENTO_FIN), 40, T8) == 0


@pytest.mark.parametrize(
    ("ahora", "esperado"),
    [(LIMITE - timedelta(microseconds=1), False), (LIMITE, True), (LIMITE + timedelta(seconds=1), True)],
)
def test_vencimiento_incluye_el_instante_limite(ahora, esperado):
    assert temporizador.vencida(participacion(), 40, ahora) is esperado


def test_expirar_por_tiempo_registra_el_instante_real_del_limite():
    intento = participacion()

    temporizador.expirar_por_tiempo(intento, 40)

    assert (intento.estado, intento.motivo_cierre, intento.fin) == (
        EstadoParticipacionEnum.EXPIRADA,
        MotivoCierreEnum.TIEMPO_AGOTADO,
        LIMITE,
    )


def test_finalizar_con_tiempo_queda_finalizada():
    intento = participacion()

    temporizador.finalizar_voluntariamente(intento, 40, T8 + timedelta(minutes=10))

    assert (intento.estado, intento.motivo_cierre) == (EstadoParticipacionEnum.FINALIZADA, MotivoCierreEnum.VOLUNTARIO)
    assert intento.fin == T8 + timedelta(minutes=10)


def test_finalizar_en_el_limite_o_despues_queda_expirada():
    intento = participacion()

    temporizador.finalizar_voluntariamente(intento, 40, LIMITE)

    assert intento.estado == EstadoParticipacionEnum.EXPIRADA
    assert intento.fin == LIMITE


@pytest.mark.parametrize("motivo", [MotivoCierreEnum.CONVOCATORIA_REVOCADA, MotivoCierreEnum.SESION_FINALIZADA])
def test_cierre_administrativo_con_tiempo_conserva_su_causa(motivo):
    intento = participacion()

    temporizador.cerrar_administrativamente(intento, motivo, 40, T8 + timedelta(minutes=5))

    assert (intento.estado, intento.motivo_cierre) == (EstadoParticipacionEnum.EXPIRADA, motivo)
    assert intento.fin == T8 + timedelta(minutes=5)


def test_cierre_administrativo_tras_agotar_el_tiempo_prevalece_tiempo_agotado():
    intento = participacion()

    temporizador.cerrar_administrativamente(intento, MotivoCierreEnum.SESION_FINALIZADA, 40, LIMITE + timedelta(hours=1))

    assert (intento.motivo_cierre, intento.fin) == (MotivoCierreEnum.TIEMPO_AGOTADO, LIMITE)


@pytest.mark.parametrize(
    "cerrar",
    [
        lambda p: temporizador.expirar_por_tiempo(p, 40),
        lambda p: temporizador.finalizar_voluntariamente(p, 40, T8),
        lambda p: temporizador.cerrar_administrativamente(p, MotivoCierreEnum.SESION_FINALIZADA, 40, T8),
    ],
    ids=["expirar", "finalizar", "administrativo"],
)
def test_cerrar_un_intento_cerrado_no_lo_cambia(cerrar):
    intento = participacion(**INTENTO_FIN)

    cerrar(intento)

    assert (intento.estado, intento.motivo_cierre, intento.fin) == (
        EstadoParticipacionEnum.FINALIZADA,
        MotivoCierreEnum.VOLUNTARIO,
        T9,
    )


@pytest.mark.parametrize(
    ("estado", "activa"),
    [(EstadoParticipacionEnum.EN_PROGRESO, True), (EstadoParticipacionEnum.FINALIZADA, False), (EstadoParticipacionEnum.EXPIRADA, False)],
)
def test_participacion_activa_solo_en_progreso(estado, activa):
    assert participacion(estado).activa is activa


def test_cierre_no_inventa_nota_ni_retroalimentacion():
    intento = participacion()

    temporizador.finalizar_voluntariamente(intento, 40, T8)

    assert intento.nota_final is None
    assert intento.retroalimentacion_general is None


# --- Esquemas -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clase", "datos", "prohibido"),
    [
        (OpcionCuestionario, {"id": uuid.uuid4(), "orden": 1, "texto": "x"}, "es_correcta"),
        (OpcionCuestionario, {"id": uuid.uuid4(), "orden": 1, "texto": "x"}, "justificacion"),
        (
            PreguntaCuestionario,
            {"id": uuid.uuid4(), "orden": 1, "enunciado": "x", "opcion_seleccionada_id": None, "opciones": []},
            "criterio_id",
        ),
        (CasoCuestionario, {"nombre": "x", "desarrollo_situacional": None, "historia": None}, "rubrica"),
    ],
)
def test_vista_del_estudiante_rechaza_datos_del_docente(clase, datos, prohibido):
    assert clase(**datos)
    with pytest.raises(ValidationError):
        clase(**datos, **{prohibido: True})


def test_respuesta_nunca_indica_si_es_correcta():
    assert "es_correcta" not in RespuestaRead.model_fields
    assert "nota_final" not in ParticipacionRead.model_fields


@pytest.mark.parametrize("datos", [{}, {"opcion_id": "no-uuid"}, {"opcion_id": str(uuid.uuid4()), "es_correcta": True}])
def test_solicitud_de_respuesta_invalida(datos):
    with pytest.raises(ValidationError):
        RespuestaRequest(**datos)


@pytest.mark.parametrize(
    ("restriccion", "tipo"),
    [
        ("uq_participacion_activa_por_convocatoria", ConflictError),
        ("uq_participacion_convocatoria_intento", ConflictError),
        ("ck_participacion_intentos_autorizados", ConflictError),
        ("ck_participacion_convocatoria_activa", PermissionDeniedError),
        ("ck_participacion_sesion_en_curso", ConflictError),
        ("ck_participacion_cerrada_inmutable", ConflictError),
        ("ck_respuesta_participacion_en_progreso", ConflictError),
        ("ck_respuesta_pregunta_del_caso", NotFoundError),
        ("fk_respuesta_opcion_de_la_pregunta", BusinessRuleError),
        ("desconocida", BusinessRuleError),
    ],
)
def test_traduccion_de_restricciones(restriccion, tipo):
    assert type(_traducir(error_integridad(restriccion))) is tipo


# --- Base de servicios ----------------------------------------------------------------------


def _armar(clase=ParticipacionService, ahora=T8, estado_sesion=EstadoSesionEnum.EN_CURSO, intento=None, **extra_conv):
    """Servicio con repositorios simulados y un token válido para una convocatoria."""
    servicio = clase(sesion_bd(), RelojFijo(ahora))
    actual = sesion(estado_sesion)
    conv = convocatoria(actual, **extra_conv)
    acceso = AccesoEstudiantil(conv.estudiante, conv, actual)
    servicio._participaciones = MagicMock()
    servicio._respuestas = MagicMock()
    servicio._participaciones.bloquear_sesion_compartida.return_value = actual
    servicio._participaciones.bloquear_convocatoria.return_value = conv
    servicio._participaciones.bloquear_participacion.return_value = intento
    servicio._participaciones.bloquear_activa.return_value = intento if (intento is not None and intento.activa) else None
    servicio._participaciones.intentos_utilizados.return_value = 0
    servicio._participaciones.add.side_effect = lambda nueva: setattr(nueva, "id", uuid.uuid4())  # como el flush real
    servicio._respuestas.total_preguntas.return_value = 3
    servicio._respuestas.contar.return_value = 0
    return servicio, acceso


def test_reloj_por_defecto_es_el_del_sistema():
    assert isinstance(ServicioParticipacionesBase(sesion_bd())._reloj, RelojSistema)


@pytest.mark.parametrize("faltante", ["bloquear_sesion_compartida", "bloquear_convocatoria"])
def test_bloqueo_sin_filas_responde_404(faltante):
    servicio, acceso = _armar()
    getattr(servicio._participaciones, faltante).return_value = None

    with pytest.raises(NotFoundError):
        servicio._bloquear(acceso)


def test_bloqueo_rechaza_convocatoria_revocada():
    servicio, acceso = _armar(revocada_en=T8)

    with pytest.raises(PermissionDeniedError):
        servicio._bloquear(acceso)


@pytest.mark.parametrize("estado", [EstadoSesionEnum.SIN_INICIAR, EstadoSesionEnum.FINALIZADO])
def test_bloqueo_rechaza_sesion_que_no_esta_en_curso(estado):
    servicio, acceso = _armar(estado_sesion=estado)

    with pytest.raises(ConflictError):
        servicio._bloquear(acceso)


def test_participacion_ajena_responde_404():
    servicio, acceso = _armar()

    with pytest.raises(NotFoundError):
        servicio._bloquear(acceso, uuid.uuid4())

    assert servicio._participaciones.bloquear_participacion.call_args.args[1] == acceso.convocatoria.id


def test_orden_de_bloqueo_y_lectura_del_reloj_tras_los_bloqueos():
    intento = participacion()
    servicio, acceso = _armar(intento=intento)
    gestor = MagicMock()
    gestor.attach_mock(servicio._participaciones, "repo")
    reloj = MagicMock()
    reloj.ahora.return_value = T8
    gestor.attach_mock(reloj, "reloj")
    servicio._reloj = reloj

    contexto = servicio._bloquear(acceso, intento.id, convocatoria_exclusiva=True)

    assert gestor.mock_calls == [
        call.repo.bloquear_sesion_compartida(acceso.sesion.id),
        call.repo.bloquear_convocatoria(acceso.convocatoria.id, exclusiva=True),
        call.repo.bloquear_participacion(intento.id, acceso.convocatoria.id),
        call.reloj.ahora(),
    ]
    assert contexto.ahora == T8


@pytest.mark.parametrize(
    ("total", "respondidas", "pendientes", "porcentaje"),
    [(0, 0, 0, 0), (3, 0, 3, 0), (3, 1, 2, 33), (3, 2, 1, 66), (3, 3, 0, 100)],
)
def test_progreso_redondea_hacia_abajo(total, respondidas, pendientes, porcentaje):
    servicio, _ = _armar()
    servicio._respuestas.total_preguntas.return_value = total
    servicio._respuestas.contar.return_value = respondidas

    progreso = servicio._progreso(participacion(), uuid.uuid4())

    assert (progreso.pendientes, progreso.porcentaje) == (pendientes, porcentaje)


# --- Inicio y recuperación ------------------------------------------------------------------


def test_primer_inicio_crea_el_intento_uno_con_hora_del_servidor():
    servicio, acceso = _armar()

    vista, creada = servicio.iniciar_o_recuperar(acceso)

    assert creada is True
    nueva = servicio._participaciones.add.call_args.args[0]
    assert (nueva.numero_intento, nueva.inicio, nueva.estado) == (1, T8, EstadoParticipacionEnum.EN_PROGRESO)
    assert (vista.tiempo_restante_segundos, vista.intentos_autorizados) == (2400, 1)


def test_siguiente_intento_continua_la_numeracion():
    servicio, acceso = _armar(intentos_autorizados=3)
    servicio._participaciones.intentos_utilizados.return_value = 2

    servicio.iniciar_o_recuperar(acceso)

    assert servicio._participaciones.add.call_args.args[0].numero_intento == 3


def test_intento_activo_se_recupera_sin_consumir_otro():
    intento = participacion()
    servicio, acceso = _armar(intento=intento, ahora=T8 + timedelta(minutes=5))

    vista, creada = servicio.iniciar_o_recuperar(acceso)

    assert (creada, vista.id) == (False, intento.id)
    assert vista.tiempo_restante_segundos == 35 * 60
    servicio._participaciones.add.assert_not_called()


def test_recuperar_funciona_fuera_de_la_ventana_de_ingreso():
    intento = participacion(inicio=T9 - timedelta(minutes=5))
    servicio, acceso = _armar(intento=intento, ahora=T9 + timedelta(minutes=10))

    assert servicio.iniciar_o_recuperar(acceso)[1] is False


def test_intentos_agotados():
    servicio, acceso = _armar()
    servicio._participaciones.intentos_utilizados.return_value = 1

    with pytest.raises(ConflictError, match=r"1 de 1"):
        servicio.iniciar_o_recuperar(acceso)

    servicio._participaciones.add.assert_not_called()


def test_nuevo_intento_fuera_de_la_ventana_se_rechaza():
    servicio, acceso = _armar(ahora=T9 + timedelta(seconds=1))

    with pytest.raises(IngresoNoPermitidoError):
        servicio.iniciar_o_recuperar(acceso)


def test_intento_vencido_se_cierra_y_confirma_aunque_no_se_pueda_iniciar_otro():
    intento = participacion()
    servicio, acceso = _armar(intento=intento, ahora=LIMITE)
    servicio._participaciones.bloquear_activa.side_effect = [intento, None]
    servicio._participaciones.intentos_utilizados.return_value = 1

    with pytest.raises(ConflictError):
        servicio.iniciar_o_recuperar(acceso)

    assert intento.estado == EstadoParticipacionEnum.EXPIRADA
    servicio._session.commit.assert_called_once()  # el cierre sobrevive al rollback del segundo paso
    servicio._session.rollback.assert_called_once()


# --- Consulta y finalización ----------------------------------------------------------------


def test_consultar_intento_vencido_lo_cierra():
    intento = participacion()
    servicio, acceso = _armar(intento=intento, ahora=LIMITE)

    vista = servicio.obtener(acceso, intento.id)

    assert vista.estado == EstadoParticipacionEnum.EXPIRADA
    assert vista.tiempo_restante_segundos == 0
    assert vista.fin == LIMITE


def test_listar_devuelve_los_intentos_de_la_convocatoria():
    intento = participacion(**INTENTO_FIN)
    servicio, acceso = _armar()
    servicio._participaciones.de_convocatoria.return_value = [intento]

    vistas = servicio.listar(acceso)

    assert [v.id for v in vistas] == [intento.id]
    servicio._participaciones.de_convocatoria.assert_called_once_with(acceso.convocatoria.id)


def test_cuestionario_proyecta_solo_campos_permitidos_y_seleccion():
    intento = participacion()
    caso = caso_valido()
    pregunta = caso.preguntas[0]
    elegida = pregunta.opciones[1].id
    servicio, acceso = _armar(intento=intento)
    servicio._participaciones.caso_con_contenido.return_value = caso
    servicio._respuestas.de_participacion.return_value = [Respuesta(pregunta_id=pregunta.id, opcion_id=elegida)]

    cuestionario = servicio.cuestionario(acceso, intento.id)

    preguntas = cuestionario.escenarios[0].preguntas
    assert preguntas[0].opcion_seleccionada_id == elegida
    assert preguntas[1].opcion_seleccionada_id is None
    volcado = cuestionario.model_dump_json()
    assert "es_correcta" not in volcado and "justificacion" not in volcado and "criterio" not in volcado


def test_finalizar_no_exige_responder_todo():
    intento = participacion()
    servicio, acceso = _armar(intento=intento, ahora=T8 + timedelta(minutes=1))

    vista = servicio.finalizar(acceso, intento.id)

    assert vista.estado == EstadoParticipacionEnum.FINALIZADA
    assert vista.progreso.pendientes == 3


def test_finalizar_dos_veces_devuelve_el_mismo_cierre():
    intento = participacion(**INTENTO_FIN)
    servicio, acceso = _armar(intento=intento, ahora=T9 + timedelta(minutes=5))

    assert servicio.finalizar(acceso, intento.id).fin == T9


def test_finalizar_tras_el_limite_queda_expirada():
    intento = participacion()
    servicio, acceso = _armar(intento=intento, ahora=LIMITE)

    assert servicio.finalizar(acceso, intento.id).motivo_cierre == MotivoCierreEnum.TIEMPO_AGOTADO


# --- Respuestas -----------------------------------------------------------------------------


def _respuestas(intento, ahora=T8):
    servicio, acceso = _armar(RespuestaService, intento=intento, ahora=ahora)
    return servicio, acceso


def test_guardar_respuesta_usa_la_hora_del_servidor():
    intento = participacion()
    pregunta, opcion = uuid.uuid4(), uuid.uuid4()
    servicio, acceso = _respuestas(intento)
    servicio._respuestas.get.return_value = Respuesta(pregunta_id=pregunta, opcion_id=opcion, respondida_en=T8, actualizada_en=T8)

    leida = servicio.guardar(acceso, intento.id, pregunta, opcion)

    servicio._respuestas.guardar.assert_called_once_with(intento.id, pregunta, opcion, T8)
    assert (leida.pregunta_id, leida.opcion_id) == (pregunta, opcion)


def test_pregunta_ajena_al_caso():
    intento = participacion()
    servicio, acceso = _respuestas(intento)
    servicio._respuestas.pregunta_del_caso.return_value = None

    with pytest.raises(NotFoundError):
        servicio.guardar(acceso, intento.id, uuid.uuid4(), uuid.uuid4())
    with pytest.raises(NotFoundError):
        servicio.eliminar(acceso, intento.id, uuid.uuid4())

    servicio._respuestas.guardar.assert_not_called()
    servicio._respuestas.eliminar.assert_not_called()


def test_opcion_de_otra_pregunta():
    intento = participacion()
    servicio, acceso = _respuestas(intento)
    servicio._respuestas.opcion_de_pregunta.return_value = None

    with pytest.raises(BusinessRuleError):
        servicio.guardar(acceso, intento.id, uuid.uuid4(), uuid.uuid4())

    servicio._respuestas.guardar.assert_not_called()


@pytest.mark.parametrize(
    "operacion",
    [
        lambda s, a, i: s.guardar(a, i, uuid.uuid4(), uuid.uuid4()),
        lambda s, a, i: s.eliminar(a, i, uuid.uuid4()),
    ],
    ids=["guardar", "eliminar"],
)
def test_intento_cerrado_no_admite_cambios(operacion):
    intento = participacion(**INTENTO_FIN)
    servicio, acceso = _respuestas(intento)

    with pytest.raises(ConflictError, match="FINALIZADA"):
        operacion(servicio, acceso, intento.id)

    servicio._respuestas.guardar.assert_not_called()
    servicio._respuestas.eliminar.assert_not_called()


@pytest.mark.parametrize(
    "operacion",
    [
        lambda s, a, i: s.guardar(a, i, uuid.uuid4(), uuid.uuid4()),
        lambda s, a, i: s.eliminar(a, i, uuid.uuid4()),
    ],
    ids=["guardar", "eliminar"],
)
def test_tiempo_agotado_expira_confirma_y_rechaza(operacion):
    intento = participacion()
    servicio, acceso = _respuestas(intento, ahora=LIMITE)

    with pytest.raises(ConflictError, match=TIEMPO_AGOTADO):
        operacion(servicio, acceso, intento.id)

    assert intento.estado == EstadoParticipacionEnum.EXPIRADA
    servicio._session.commit.assert_called_once()
    servicio._session.rollback.assert_not_called()


def test_eliminar_deja_la_pregunta_sin_responder():
    intento = participacion()
    pregunta = uuid.uuid4()
    servicio, acceso = _respuestas(intento)

    servicio.eliminar(acceso, intento.id, pregunta)

    servicio._respuestas.eliminar.assert_called_once_with(intento.id, pregunta)


def test_listar_respuestas_expira_el_intento_vencido_y_conserva_lo_guardado():
    intento = participacion()
    pregunta, opcion = uuid.uuid4(), uuid.uuid4()
    servicio, acceso = _respuestas(intento, ahora=LIMITE)
    servicio._respuestas.de_participacion.return_value = [
        Respuesta(pregunta_id=pregunta, opcion_id=opcion, respondida_en=T8, actualizada_en=T8)
    ]

    leidas = servicio.listar(acceso, intento.id)

    assert intento.estado == EstadoParticipacionEnum.EXPIRADA
    assert [(r.pregunta_id, r.opcion_id) for r in leidas] == [(pregunta, opcion)]


# --- Expiración automática ------------------------------------------------------------------


def _expiracion(ahora=LIMITE):
    servicio = ExpiracionParticipacionesService(sesion_bd(), RelojFijo(ahora))
    servicio._participaciones = MagicMock()
    servicio._participaciones.tiempo_max_de.return_value = 40
    return servicio


def test_expiracion_cierra_los_intentos_vencidos():
    servicio = _expiracion()
    intento = participacion()
    servicio._participaciones.ids_vencidas.return_value = [intento.id]
    servicio._participaciones.bloquear_para_expirar.return_value = intento

    assert servicio.expirar_vencidas() == [intento.id]

    assert intento.fin == LIMITE
    servicio._session.commit.assert_called_once()


def test_expiracion_omite_bloqueados_y_ya_cerrados():
    servicio = _expiracion()
    servicio._participaciones.ids_vencidas.return_value = [uuid.uuid4(), uuid.uuid4()]
    servicio._participaciones.bloquear_para_expirar.side_effect = [None, participacion(**INTENTO_FIN)]

    assert servicio.expirar_vencidas() == []


def test_expiracion_revalida_el_tiempo_tras_el_bloqueo():
    servicio = _expiracion(LIMITE - timedelta(seconds=1))
    intento = participacion()
    servicio._participaciones.ids_vencidas.return_value = [intento.id]
    servicio._participaciones.bloquear_para_expirar.return_value = intento

    assert servicio.expirar_vencidas() == []
    assert intento.activa


def test_expiracion_respeta_el_tamano_de_lote():
    servicio = _expiracion()
    servicio._participaciones.ids_vencidas.return_value = []

    servicio.expirar_vencidas(lote=3)

    servicio._participaciones.ids_vencidas.assert_called_once_with(LIMITE, 3)


# --- Integración con el módulo 3 (puertos) --------------------------------------------------


@pytest.fixture
def repositorio():
    with patch("app.modules.participations.policies.integracion_sesiones.ParticipacionRepository") as clase:
        clase.return_value.tiempo_max_de.return_value = 40
        yield clase.return_value


def test_revocacion_cierra_el_intento_activo(repositorio):
    intento = participacion()
    repositorio.bloquear_activa.return_value = intento

    CierreParticipacionesSQL().cerrar_por_revocacion(sesion_bd(), uuid.uuid4(), T8 + timedelta(minutes=1))

    assert intento.motivo_cierre == MotivoCierreEnum.CONVOCATORIA_REVOCADA
    repositorio.flush.assert_called_once()


def test_revocacion_sin_intento_activo_no_escribe(repositorio):
    repositorio.bloquear_activa.return_value = None

    CierreParticipacionesSQL().cerrar_por_revocacion(sesion_bd(), uuid.uuid4(), T8)

    repositorio.flush.assert_not_called()


def test_fin_de_sesion_cierra_todos_los_intentos_activos(repositorio):
    intentos = [participacion(), participacion()]
    repositorio.bloquear_activas_de_sesion.return_value = intentos

    CierreParticipacionesSQL().cerrar_por_fin_de_sesion(sesion_bd(), uuid.uuid4(), T8 + timedelta(minutes=1))

    assert {i.motivo_cierre for i in intentos} == {MotivoCierreEnum.SESION_FINALIZADA}
    repositorio.tiempo_max_de.assert_called_once()


def test_fin_de_sesion_sin_intentos_no_escribe(repositorio):
    repositorio.bloquear_activas_de_sesion.return_value = []

    CierreParticipacionesSQL().cerrar_por_fin_de_sesion(sesion_bd(), uuid.uuid4(), T8)

    repositorio.flush.assert_not_called()


@pytest.mark.parametrize(
    ("activa", "ahora", "esperado"),
    [
        (None, T8, False),
        (participacion(), LIMITE - timedelta(seconds=1), True),
        (participacion(), LIMITE, False),
    ],
    ids=["sin-intento", "con-tiempo", "vencido"],
)
def test_reingreso_exige_un_intento_vigente(repositorio, activa, ahora, esperado):
    repositorio.activa.return_value = activa

    assert ReingresoConParticipacionActiva(sesion_bd()).permite_reingreso(convocatoria(), ahora) is esperado


# --- Datos para la evaluación (módulo 5) ------------------------------------------------------


def _consulta(intento):
    servicio = ConsultaEvaluacionService(sesion_bd())
    servicio._participaciones = MagicMock()
    servicio._respuestas = MagicMock()
    servicio._participaciones.get.return_value = intento
    return servicio


def test_consulta_de_participacion_inexistente():
    with pytest.raises(NotFoundError):
        _consulta(None).obtener(uuid.uuid4())


def test_consulta_separa_respondidas_de_pendientes_sin_calificar():
    actual = sesion()
    conv = convocatoria(actual, id=uuid.uuid4())
    intento = participacion(**INTENTO_FIN, convocatoria=conv)
    caso = caso_valido()
    respondida, pendiente = caso.preguntas
    opcion = respondida.opciones[0].id
    servicio = _consulta(intento)
    servicio._participaciones.caso_con_contenido.return_value = caso
    servicio._respuestas.de_participacion.return_value = [Respuesta(pregunta_id=respondida.id, opcion_id=opcion)]

    datos = servicio.obtener(intento.id)

    assert datos.respuestas == {respondida.id: opcion}
    assert datos.preguntas_sin_responder == [pendiente.id]
    assert datos.rubrica is caso.rubrica
    assert datos.completa_voluntariamente is True
    assert not hasattr(datos, "nota_final")


@pytest.mark.parametrize(
    ("estado", "esperado"), [(EstadoParticipacionEnum.FINALIZADA, True), (EstadoParticipacionEnum.EXPIRADA, False)]
)
def test_completa_voluntariamente(estado, esperado):
    datos = ParticipacionParaEvaluacion(
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), 1, estado, None, {}, [], caso_valido()
    )

    assert datos.completa_voluntariamente is esperado


def test_token_estudiantil_requerido_en_rutas_sin_sesion():
    servicio = MagicMock()

    for credenciales in (None, HTTPAuthorizationCredentials(scheme="Basic", credentials="x")):
        with pytest.raises(AuthenticationError):
            acceso_estudiantil_actual(credenciales, servicio)
    servicio.resolver.assert_not_called()


# --- Worker de expiración -----------------------------------------------------------------------


def test_pasada_del_worker_de_expiracion(caplog):
    fabrica = MagicMock()
    with (
        patch.object(worker, "configurar_integraciones") as configurar,
        patch.object(worker, "ExpiracionParticipacionesService") as servicio,
        caplog.at_level(logging.INFO, logger="iter_animi.workers.participation_expiration"),
    ):
        servicio.return_value.expirar_vencidas.return_value = [uuid.uuid4(), uuid.uuid4()]
        resultado = worker.ejecutar_una_vez(fabrica)

    configurar.assert_called_once()
    assert len(resultado) == 2
    assert "2 expirada" in caplog.text


def test_bucle_del_worker_de_expiracion_sobrevive_a_errores(caplog):
    class Detener(Exception):
        pass

    with (
        patch.object(worker, "ejecutar_una_vez", side_effect=RuntimeError("bd caída")),
        patch.object(worker.time, "sleep", side_effect=Detener) as dormir,
    ):
        with pytest.raises(Detener):
            worker.ejecutar_periodicamente(15)

    dormir.assert_called_once_with(15)
    assert "fallo en la pasada" in caplog.text


@pytest.mark.parametrize(("argumentos", "periodico"), [([], False), (["--intervalo", "60"], True)])
def test_linea_de_comandos_del_worker_de_expiracion(argumentos, periodico):
    with (
        patch.object(worker_cli, "ejecutar_una_vez") as una_vez,
        patch.object(worker_cli, "ejecutar_periodicamente") as bucle,
        patch.object(worker_cli.logging, "basicConfig"),
    ):
        assert worker_cli.main(argumentos) == 0

    assert bucle.called is periodico
    assert una_vez.called is not periodico


def test_transaccion_de_participaciones_revierte_y_traduce():
    servicio, _ = _armar(ServicioParticipacionesBase)

    with pytest.raises(ConflictError):
        with servicio._transaction():
            raise error_integridad("ck_participacion_sesion_en_curso")
    with pytest.raises(ValueError):
        with servicio._transaction():
            raise ValueError

    assert servicio._session.rollback.call_count == 2
