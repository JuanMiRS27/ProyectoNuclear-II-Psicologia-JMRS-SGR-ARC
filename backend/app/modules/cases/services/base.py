import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.auth.models import Usuario
from app.modules.cases.models import CasoEstudio
from app.modules.cases.repositories import CasoRepository, ContenidoCasoRepository
from app.modules.cases.states import CicloVidaCaso

CASO_NO_ENCONTRADO = "Caso no encontrado."

_MENSAJES_RESTRICCION = {
    "uq_escenario_caso_orden": (ConflictError, "Ya existe un escenario con ese orden en el caso."),
    "uq_pregunta_escenario_orden": (ConflictError, "Ya existe una pregunta con ese orden en el escenario."),
    "uq_opcion_pregunta_orden": (ConflictError, "Ya existe una opción con ese orden en la pregunta."),
    "uq_rubrica_caso_id": (ConflictError, "El caso ya tiene una rúbrica."),
    "ck_pregunta_criterio_mismo_caso": (BusinessRuleError, "El criterio no pertenece a la rúbrica de este caso."),
    "fk_pregunta_criterio_id_criterio_rubrica": (BusinessRuleError, "El criterio indicado no existe."),
}


class ServicioCasoBase:
    """Comportamiento común a los servicios del módulo 2.

    - Pertenencia: un caso de otro docente responde 404, igual que uno inexistente.
    - Escritura: se bloquea la fila del caso (SELECT ... FOR UPDATE) y el patrón
      State decide si el contenido puede modificarse.
    - Transacción: todo el bloque de escritura se confirma o se revierte entero.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._casos = CasoRepository(session)
        self._contenido = ContenidoCasoRepository(session)

    def _caso_lectura(self, caso_id: uuid.UUID, autor: Usuario, *, cargar_contenido: bool = False) -> CasoEstudio:
        caso = self._casos.get_owned(caso_id, autor.id, cargar_contenido=cargar_contenido)
        if caso is None:
            raise NotFoundError(CASO_NO_ENCONTRADO)
        return caso

    def _caso_bloqueado(self, caso_id: uuid.UUID, autor: Usuario, *, cargar_contenido: bool = False) -> CasoEstudio:
        caso = self._casos.get_owned(caso_id, autor.id, for_update=True, cargar_contenido=cargar_contenido)
        if caso is None:
            raise NotFoundError(CASO_NO_ENCONTRADO)
        return caso

    def _caso_editable(self, caso_id: uuid.UUID, autor: Usuario) -> CasoEstudio:
        """Bloquea el caso, exige que su estado permita editar y registra la modificación."""
        caso = self._caso_bloqueado(caso_id, autor)
        CicloVidaCaso(caso).asegurar_contenido_editable()
        caso.actualizado_en = func.now()
        return caso

    def _reordenar(self, actuales: Sequence, ids: list[uuid.UUID]) -> None:
        por_id = {elemento.id: elemento for elemento in actuales}
        if set(ids) != set(por_id):
            raise BusinessRuleError("El nuevo orden debe incluir exactamente todos los elementos actuales, una vez cada uno.")
        self._contenido.aplicar_orden([por_id[i] for i in ids])

    def _compactar(self, restantes: Sequence) -> None:
        """Tras una eliminación, renumera los hermanos restantes como 1..n."""
        self._contenido.aplicar_orden(sorted(restantes, key=lambda e: e.orden))

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            yield
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise _traducir(exc) from exc
        except BaseException:
            self._session.rollback()
            raise


def _traducir(exc: IntegrityError) -> Exception:
    nombre = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if nombre in _MENSAJES_RESTRICCION:
        tipo, mensaje = _MENSAJES_RESTRICCION[nombre]
        return tipo(mensaje)
    return BusinessRuleError("Los datos no cumplen las restricciones de integridad.")
