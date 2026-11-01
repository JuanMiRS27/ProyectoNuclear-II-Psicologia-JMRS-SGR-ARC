"""Patrón State: ciclo de vida, persistencia de estados y concurrencia."""

import threading
import uuid

import pytest
from sqlalchemy import text

from app.core.database import SessionLocal, engine
from app.core.exceptions import NotFoundError
from app.modules.cases.models import CasoEstudio, EstadoCasoEnum
from app.modules.cases.repositories import CasoRepository
from app.modules.cases.services import CasoService
from app.modules.cases.states import (
    CasoNoPublicableError,
    CicloVidaCaso,
    EstadoArchivado,
    EstadoBorrador,
    EstadoCaso,
    EstadoPublicado,
    TransicionNoPermitidaError,
)
from app.modules.cases.validators import ResultadoValidacion
from tests.cases_helpers import CASES_URL, codigos, crear_caso_completo, dsl_valido, publicar


def estado_en_bd(caso_id) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT estado::text FROM caso_estudio WHERE id = :id"), {"id": caso_id}).scalar_one()


# --- Transiciones vía API ------------------------------------------------------------


def test_new_case_starts_in_draft(client, creador_headers):
    caso = client.post(CASES_URL, headers=creador_headers, json={"nombre": "Nuevo"}).json()
    assert caso["estado"] == "BORRADOR"
    assert estado_en_bd(caso["id"]) == "BORRADOR"


def test_publish_valid_case_persists_state(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    response = publicar(client, creador_headers, caso["id"])
    assert response.status_code == 200 and response.json()["estado"] == "PUBLICADO"
    assert estado_en_bd(caso["id"]) == "PUBLICADO"


def test_invalid_publication_returns_errors_and_keeps_draft(client, creador_headers):
    documento = dsl_valido()
    documento["rubrica"]["criterios"][0]["peso"] = 1
    documento["escenarios"][0]["contexto"] = None
    caso = crear_caso_completo(client, creador_headers, documento)

    response = publicar(client, creador_headers, caso["id"])
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "El caso no cumple los requisitos de publicación."
    assert body["valido"] is False
    assert codigos(body) == ["ESCENARIO_CONTEXTO_VACIO", "RUBRICA_PESOS_NO_SUMAN_100"]
    assert estado_en_bd(caso["id"]) == "BORRADOR"


def test_draft_can_be_fixed_and_then_published(client, creador_headers):
    documento = dsl_valido()
    documento["escenarios"][0]["contexto"] = None
    caso = crear_caso_completo(client, creador_headers, documento)
    assert publicar(client, creador_headers, caso["id"]).status_code == 422

    escenario_id = caso["escenarios"][0]["id"]
    client.patch(f"{CASES_URL}/{caso['id']}/scenarios/{escenario_id}", headers=creador_headers, json={"contexto": "Corregido"})
    assert publicar(client, creador_headers, caso["id"]).status_code == 200


def test_published_case_can_be_archived(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    publicar(client, creador_headers, caso["id"])
    response = client.post(f"{CASES_URL}/{caso['id']}/archive", headers=creador_headers)
    assert response.status_code == 200 and response.json()["estado"] == "ARCHIVADO"
    assert estado_en_bd(caso["id"]) == "ARCHIVADO"


def test_forbidden_transitions(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    cid = caso["id"]
    archivar = lambda: client.post(f"{CASES_URL}/{cid}/archive", headers=creador_headers)  # noqa: E731

    # BORRADOR -> ARCHIVADO no existe (un borrador se elimina, no se archiva).
    assert archivar().status_code == 409
    assert estado_en_bd(cid) == "BORRADOR"

    assert publicar(client, creador_headers, cid).status_code == 200
    # PUBLICADO -> PUBLICADO.
    response = publicar(client, creador_headers, cid)
    assert response.status_code == 409
    assert response.json() == {"detail": "No se puede publicar un caso publicado."}

    assert archivar().status_code == 200
    # ARCHIVADO -> PUBLICADO y ARCHIVADO -> ARCHIVADO.
    assert publicar(client, creador_headers, cid).status_code == 409
    assert archivar().status_code == 409
    assert client.delete(f"{CASES_URL}/{cid}", headers=creador_headers).status_code == 409
    assert estado_en_bd(cid) == "ARCHIVADO"


def test_publish_requires_case_permission(client, docente_headers, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    # Un docente sin permiso no puede publicar (403, antes de comprobar la autoría).
    assert publicar(client, docente_headers, caso["id"]).status_code == 403


def test_state_is_not_changeable_through_update(client, creador_headers):
    caso = crear_caso_completo(client, creador_headers)
    response = client.patch(f"{CASES_URL}/{caso['id']}", headers=creador_headers, json={"estado": "PUBLICADO"})
    assert response.status_code == 422
    assert estado_en_bd(caso["id"]) == "BORRADOR"


def test_database_rejects_published_case_without_general_data(client, creador_headers):
    """Defensa adicional: ni siquiera un UPDATE directo puede publicar un caso sin datos generales."""
    caso = client.post(CASES_URL, headers=creador_headers, json={"nombre": "Incompleto"}).json()
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE caso_estudio SET estado = 'PUBLICADO' WHERE id = :id"), {"id": caso["id"]})


# --- Clases de estado sin base de datos -------------------------------------------------


def _caso(estado: EstadoCasoEnum) -> CasoEstudio:
    return CasoEstudio(id=uuid.uuid4(), nombre="Caso", estado=estado)


def test_context_selects_concrete_state_class():
    assert isinstance(CicloVidaCaso(_caso(EstadoCasoEnum.BORRADOR)).estado, EstadoBorrador)
    assert isinstance(CicloVidaCaso(_caso(EstadoCasoEnum.PUBLICADO)).estado, EstadoPublicado)
    assert isinstance(CicloVidaCaso(_caso(EstadoCasoEnum.ARCHIVADO)).estado, EstadoArchivado)
    assert all(issubclass(e, EstadoCaso) for e in (EstadoBorrador, EstadoPublicado, EstadoArchivado))


def test_draft_publish_uses_validation_result():
    ciclo = CicloVidaCaso(_caso(EstadoCasoEnum.BORRADOR))
    invalido = ResultadoValidacion()
    invalido.agregar("X", componente="caso", componente_id=None, campo="c", ruta="r", mensaje="m")

    with pytest.raises(CasoNoPublicableError) as info:
        ciclo.publicar(lambda: invalido)
    assert info.value.extra["errores"][0]["codigo"] == "X"
    assert ciclo.caso.estado == EstadoCasoEnum.BORRADOR

    ciclo.publicar(lambda: ResultadoValidacion())
    assert ciclo.caso.estado == EstadoCasoEnum.PUBLICADO
    assert isinstance(ciclo.estado, EstadoPublicado)


@pytest.mark.parametrize("estado", [EstadoCasoEnum.PUBLICADO, EstadoCasoEnum.ARCHIVADO])
def test_non_draft_states_reject_publication_without_running_validation(estado):
    llamadas = []
    ciclo = CicloVidaCaso(_caso(estado))
    with pytest.raises(TransicionNoPermitidaError):
        ciclo.publicar(lambda: llamadas.append(1) or ResultadoValidacion())
    assert llamadas == []  # State no duplica ni dispara la validación cuando no corresponde


def test_state_permissions_matrix():
    borrador = CicloVidaCaso(_caso(EstadoCasoEnum.BORRADOR))
    publicado = CicloVidaCaso(_caso(EstadoCasoEnum.PUBLICADO))
    archivado = CicloVidaCaso(_caso(EstadoCasoEnum.ARCHIVADO))

    borrador.asegurar_contenido_editable()
    borrador.asegurar_eliminable()
    for ciclo in (publicado, archivado):
        with pytest.raises(TransicionNoPermitidaError):
            ciclo.asegurar_contenido_editable()
        with pytest.raises(TransicionNoPermitidaError):
            ciclo.asegurar_eliminable()
    with pytest.raises(TransicionNoPermitidaError):
        borrador.archivar()

    assert [c.admite_programacion() for c in (borrador, publicado, archivado)] == [False, True, False]
    publicado.archivar()
    assert publicado.caso.estado == EstadoCasoEnum.ARCHIVADO and not publicado.admite_programacion()


# --- Concurrencia -------------------------------------------------------------------------


def _en_paralelo(*tareas):
    resultados = [None] * len(tareas)

    def ejecutar(i, tarea):
        try:
            resultados[i] = tarea()
        except Exception as exc:  # noqa: BLE001
            resultados[i] = exc

    hilos = [threading.Thread(target=ejecutar, args=(i, t)) for i, t in enumerate(tareas)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=30)
        assert not h.is_alive()
    return resultados


@pytest.fixture
def barrera_antes_del_bloqueo(monkeypatch):
    """Hace que ambas operaciones lleguen a la vez al SELECT ... FOR UPDATE del caso."""
    barrera = threading.Barrier(2, timeout=10)
    original = CasoRepository.get_owned

    def get_owned(self, *args, **kwargs):
        if kwargs.get("for_update"):
            barrera.wait()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CasoRepository, "get_owned", get_owned)


def _servicio(operacion, autor, caso_id):
    def tarea():
        with SessionLocal() as session:
            return getattr(CasoService(session), operacion)(autor, caso_id)

    return tarea


def test_concurrent_publications_only_one_succeeds(client, creador, creador_headers, barrera_antes_del_bloqueo):
    caso_id = uuid.UUID(crear_caso_completo(client, creador_headers)["id"])

    resultados = _en_paralelo(_servicio("publicar", creador, caso_id), _servicio("publicar", creador, caso_id))

    exitos = [r for r in resultados if isinstance(r, CasoEstudio)]
    conflictos = [r for r in resultados if isinstance(r, TransicionNoPermitidaError)]
    assert len(exitos) == 1 and len(conflictos) == 1, resultados
    assert estado_en_bd(caso_id) == "PUBLICADO"


def test_concurrent_publish_and_delete_are_serialized(client, creador, creador_headers, barrera_antes_del_bloqueo):
    caso_id = uuid.UUID(crear_caso_completo(client, creador_headers)["id"])

    publicacion, eliminacion = _en_paralelo(_servicio("publicar", creador, caso_id), _servicio("eliminar", creador, caso_id))

    if isinstance(publicacion, CasoEstudio):
        # Publicó primero: la eliminación vio un caso publicado.
        assert isinstance(eliminacion, TransicionNoPermitidaError)
        assert estado_en_bd(caso_id) == "PUBLICADO"
    else:
        # Eliminó primero: la publicación ya no encuentra el caso.
        assert eliminacion is None and isinstance(publicacion, NotFoundError)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM caso_estudio")).scalar_one() == 0


def test_concurrent_edit_and_publish_never_modify_a_published_case(client, creador, creador_headers, barrera_antes_del_bloqueo):
    caso = crear_caso_completo(client, creador_headers)
    caso_id = uuid.UUID(caso["id"])
    from app.modules.cases.schemas import CasoUpdate

    def editar():
        with SessionLocal() as session:
            return CasoService(session).actualizar(creador, caso_id, CasoUpdate(nombre="Editado en paralelo"))

    publicacion, edicion = _en_paralelo(_servicio("publicar", creador, caso_id), editar)

    assert isinstance(publicacion, CasoEstudio), publicacion
    with engine.connect() as conn:
        nombre = conn.execute(text("SELECT nombre FROM caso_estudio WHERE id = :id"), {"id": caso_id}).scalar_one()
    if isinstance(edicion, TransicionNoPermitidaError):
        assert nombre == "Conformidad en el aula"  # publicó primero: la edición fue rechazada
    else:
        assert nombre == "Editado en paralelo"  # editó primero (el borrador seguía siendo válido) y luego se publicó
    assert estado_en_bd(caso_id) == "PUBLICADO"


def test_control_without_row_lock_a_published_case_gets_modified(client, creador, creador_headers, monkeypatch):
    """Control: si se quita el bloqueo FOR UPDATE, la carrera edición/publicación sí rompe la regla.

    Ambas operaciones leen el caso en BORRADOR antes de que la otra confirme: la
    publicación valida y publica, y la edición modifica un caso que ya quedó publicado.
    Demuestra que las pruebas de concurrencia anteriores dependen del bloqueo.
    """
    from app.modules.cases.schemas import CasoUpdate

    caso_id = uuid.UUID(crear_caso_completo(client, creador_headers)["id"])
    barrera = threading.Barrier(2, timeout=10)
    original = CasoRepository.get_owned

    def sin_bloqueo(self, *args, **kwargs):
        bloquear = kwargs.pop("for_update", False)
        caso = original(self, *args, **kwargs)
        if bloquear:
            barrera.wait()  # ambas ya leyeron el caso en BORRADOR
        return caso

    monkeypatch.setattr(CasoRepository, "get_owned", sin_bloqueo)

    def editar():
        with SessionLocal() as session:
            return CasoService(session).actualizar(creador, caso_id, CasoUpdate(nombre="Modificado tras publicar"))

    publicacion, edicion = _en_paralelo(_servicio("publicar", creador, caso_id), editar)

    assert isinstance(publicacion, CasoEstudio) and isinstance(edicion, CasoEstudio)
    with engine.connect() as conn:
        fila = conn.execute(text("SELECT estado::text, nombre FROM caso_estudio WHERE id = :id"), {"id": caso_id}).one()
    assert tuple(fila) == ("PUBLICADO", "Modificado tras publicar")
