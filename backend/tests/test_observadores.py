import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.enums import EstadoParticipacion, TipoNotificacion
from app.models import (
    CasoEstudio,
    Convocatoria,
    EspacioAcademico,
    Notificacion,
    Participacion,
    Rubrica,
    Sesion,
)
from app.notificaciones.correo import ErrorEnvioCorreo, ServicioCorreo
from app.notificaciones.eventos import EventoAcademico, PublicadorEventos, TipoEvento
from app.notificaciones.observadores import registrar_observadores
from tests.conftest import _crear


class CorreoFalso(ServicioCorreo):
    def __init__(self, fallar_a: set[str] | None = None) -> None:
        self.enviados: list[tuple[str, str, str]] = []
        self.fallar_a = fallar_a or set()

    def enviar(self, destinatario: str, asunto: str, cuerpo: str) -> None:
        if destinatario in self.fallar_a:
            raise ErrorEnvioCorreo("rechazado")
        self.enviados.append((destinatario, asunto, cuerpo))


@pytest.fixture
def escenario(sesion, docente, estudiante):
    """Una sesión programada con dos estudiantes convocados."""
    otro = _crear(
        sesion,
        nombre_completo="Otro Estudiante",
        correo="otro@prueba.edu.co",
        rol=estudiante.rol,
        codigo_institucional="EST-0100",
    )
    caso = CasoEstudio(
        autor_id=docente.id,
        nombre="Conformidad en el trabajo en equipo",
        desarrollo_situacional="Un grupo de estudiantes debe entregar un proyecto.",
        historia="Laura cursa tercer semestre de Psicología y se integra a un equipo.",
        tiempo_estimado_min=40,
    )
    espacio = EspacioAcademico(nombre="Psicología Social - Grupo 01", periodo="2026-2")
    sesion.add_all([caso, espacio])
    sesion.flush()
    sesion.add(Rubrica(caso_id=caso.id, escala_max=Decimal("5.00")))
    programada = Sesion(
        caso_id=caso.id,
        espacio_academico_id=espacio.id,
        docente_id=docente.id,
        inicio=datetime(2026, 11, 3, 8, 0),
        fin=datetime(2026, 11, 3, 10, 0),
        tiempo_max_min=40,
        lugar="Sala B-204",
        mensaje="Lean el contexto antes de responder.",
    )
    sesion.add(programada)
    sesion.flush()
    conv1 = Convocatoria(
        sesion_id=programada.id, estudiante_id=estudiante.id, codigo_autorizacion="ABC123"
    )
    conv2 = Convocatoria(
        sesion_id=programada.id, estudiante_id=otro.id, codigo_autorizacion="XYZ789"
    )
    sesion.add_all([conv1, conv2])
    sesion.commit()
    return programada, conv1, conv2


def _publicador(fabrica_sesion, correo):
    publicador = PublicadorEventos()
    registrar_observadores(publicador, fabrica_sesion, correo)
    return publicador


def _notificaciones(sesion):
    sesion.expire_all()
    return list(sesion.scalars(select(Notificacion)))


def test_sesion_programada_notifica_a_cada_convocado(fabrica_sesion, sesion, escenario):
    programada, _, _ = escenario
    correo = CorreoFalso()

    _publicador(fabrica_sesion, correo).publicar(
        EventoAcademico(TipoEvento.SESION_PROGRAMADA, programada.id)
    )

    destinatarios = {d for d, _, _ in correo.enviados}
    assert destinatarios == {"estudiante@prueba.edu.co", "otro@prueba.edu.co"}
    cuerpo = next(c for d, _, c in correo.enviados if d == "estudiante@prueba.edu.co")
    assert "ABC123" in cuerpo and "Sala B-204" in cuerpo

    notificaciones = _notificaciones(sesion)
    assert len(notificaciones) == 2
    assert all(n.tipo == TipoNotificacion.CONVOCATORIA for n in notificaciones)


def test_cambio_de_estudiantes_solo_avisa_a_los_indicados(fabrica_sesion, sesion, escenario):
    programada, _, conv2 = escenario
    correo = CorreoFalso()

    _publicador(fabrica_sesion, correo).publicar(
        EventoAcademico(
            TipoEvento.ESTUDIANTES_MODIFICADOS,
            programada.id,
            {"convocatoria_ids": [str(conv2.id)]},
        )
    )

    assert [d for d, _, _ in correo.enviados] == ["otro@prueba.edu.co"]
    [notificacion] = _notificaciones(sesion)
    assert notificacion.tipo == TipoNotificacion.CAMBIO_ESTUDIANTES
    assert notificacion.convocatoria_id == conv2.id


def test_un_envio_fallido_no_detiene_a_los_demas(fabrica_sesion, sesion, escenario):
    programada, _, conv2 = escenario
    correo = CorreoFalso(fallar_a={"estudiante@prueba.edu.co"})

    _publicador(fabrica_sesion, correo).publicar(
        EventoAcademico(TipoEvento.SESION_PROGRAMADA, programada.id)
    )

    # Solo queda evidencia del correo que sí salió
    [notificacion] = _notificaciones(sesion)
    assert notificacion.convocatoria_id == conv2.id


def test_calificacion_y_retroalimentacion(fabrica_sesion, sesion, escenario):
    _, conv1, _ = escenario
    participacion = Participacion(
        convocatoria_id=conv1.id,
        inicio=datetime(2026, 11, 3, 8, 5),
        fin=datetime(2026, 11, 3, 8, 40),
        estado=EstadoParticipacion.FINALIZADA,
        nota_final=Decimal("4.20"),
        retroalimentacion_general="Buen análisis de la conformidad normativa.",
    )
    sesion.add(participacion)
    sesion.commit()
    correo = CorreoFalso()
    publicador = _publicador(fabrica_sesion, correo)

    publicador.publicar(EventoAcademico(TipoEvento.CALIFICACION_REGISTRADA, participacion.id))
    publicador.publicar(
        EventoAcademico(TipoEvento.RETROALIMENTACION_REGISTRADA, participacion.id)
    )

    assert "4.20 / 5.00" in correo.enviados[0][2]
    assert "conformidad normativa" in correo.enviados[1][2]
    tipos = [n.tipo for n in _notificaciones(sesion)]
    assert tipos == [TipoNotificacion.CALIFICACION, TipoNotificacion.RETROALIMENTACION]


def test_observador_que_falla_no_rompe_al_publicador():
    class Roto:
        def actualizar(self, evento):
            raise RuntimeError("falla")

    recibidos = []

    class Bueno:
        def actualizar(self, evento):
            recibidos.append(evento)

    publicador = PublicadorEventos()
    publicador.suscribir(TipoEvento.SESION_PROGRAMADA, Roto())
    publicador.suscribir(TipoEvento.SESION_PROGRAMADA, Bueno())
    evento = EventoAcademico(TipoEvento.SESION_PROGRAMADA, uuid.uuid4())

    publicador.publicar(evento)

    assert recibidos == [evento]
