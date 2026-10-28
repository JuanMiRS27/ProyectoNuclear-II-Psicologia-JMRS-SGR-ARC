import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.notificaciones.eventos import (
    EventoAcademico,
    ObservadorEvento,
    PublicadorEventos,
    TipoEvento,
)

cliente = TestClient(app)


def test_health():
    respuesta = cliente.get("/health")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok"}


def test_publicador_notifica_solo_a_observadores_del_tipo():
    recibidos: list[EventoAcademico] = []

    class Observador(ObservadorEvento):
        def actualizar(self, evento: EventoAcademico) -> None:
            recibidos.append(evento)

    publicador = PublicadorEventos()
    publicador.suscribir(TipoEvento.SESION_PROGRAMADA, Observador())

    publicador.publicar(EventoAcademico(TipoEvento.SESION_PROGRAMADA, uuid.uuid4()))
    publicador.publicar(EventoAcademico(TipoEvento.CALIFICACION_REGISTRADA, uuid.uuid4()))

    assert [e.tipo for e in recibidos] == [TipoEvento.SESION_PROGRAMADA]


def test_modelos_registrados():
    from app.models import Base

    assert len(Base.metadata.tables) == 13
