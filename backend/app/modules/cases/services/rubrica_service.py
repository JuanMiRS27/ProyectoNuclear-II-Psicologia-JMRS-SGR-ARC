import uuid

from app.core.exceptions import BusinessRuleError, ConflictError, NotFoundError
from app.modules.auth.models import Usuario
from app.modules.cases.models import CriterioRubrica, Rubrica
from app.modules.cases.schemas import CriterioCreate, CriterioUpdate, RubricaCreate, RubricaUpdate
from app.modules.cases.services.base import ServicioCasoBase


def _niveles(niveles) -> list[dict]:
    return [nivel.model_dump(mode="python") for nivel in niveles]


class RubricaService(ServicioCasoBase):
    def obtener(self, autor: Usuario, caso_id: uuid.UUID) -> Rubrica:
        self._caso_lectura(caso_id, autor)
        return self._rubrica(caso_id)

    def crear(self, autor: Usuario, caso_id: uuid.UUID, data: RubricaCreate) -> Rubrica:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            if self._contenido.get_rubrica(caso_id) is not None:
                raise ConflictError("El caso ya tiene una rúbrica.")
            rubrica = Rubrica(
                caso_id=caso_id,
                escala_max=data.escala_max,
                criterios=[
                    CriterioRubrica(descripcion=c.descripcion, peso=c.peso, niveles=_niveles(c.niveles))
                    for c in data.criterios
                ],
            )
            self._contenido.add(rubrica)
        return self._rubrica(caso_id)

    def actualizar(self, autor: Usuario, caso_id: uuid.UUID, data: RubricaUpdate) -> Rubrica:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._rubrica(caso_id).escala_max = data.escala_max
        return self._rubrica(caso_id)

    # --- Criterios ----------------------------------------------------------

    def listar_criterios(self, autor: Usuario, caso_id: uuid.UUID) -> list[CriterioRubrica]:
        return list(self.obtener(autor, caso_id).criterios)

    def crear_criterio(self, autor: Usuario, caso_id: uuid.UUID, data: CriterioCreate) -> CriterioRubrica:
        with self._transaction():
            self._caso_editable(caso_id, autor)
            criterio = CriterioRubrica(
                rubrica_id=self._rubrica(caso_id).id,
                descripcion=data.descripcion,
                peso=data.peso,
                niveles=_niveles(data.niveles),
            )
            self._contenido.add(criterio)
        return criterio

    def actualizar_criterio(
        self, autor: Usuario, caso_id: uuid.UUID, criterio_id: uuid.UUID, data: CriterioUpdate
    ) -> CriterioRubrica:
        cambios = data.model_dump(exclude_unset=True)
        for campo in ("descripcion", "peso", "niveles"):
            if campo in cambios and cambios[campo] is None:
                raise BusinessRuleError(f"El campo '{campo}' del criterio no puede quedar vacío.")
        with self._transaction():
            self._caso_editable(caso_id, autor)
            criterio = self._criterio(caso_id, criterio_id)
            if data.descripcion is not None:
                criterio.descripcion = data.descripcion
            if data.peso is not None:
                criterio.peso = data.peso
            if data.niveles is not None:
                criterio.niveles = _niveles(data.niveles)
        return criterio

    def eliminar_criterio(self, autor: Usuario, caso_id: uuid.UUID, criterio_id: uuid.UUID) -> None:
        """Las preguntas asociadas quedan con el criterio pendiente (ON DELETE SET NULL)."""
        with self._transaction():
            self._caso_editable(caso_id, autor)
            self._contenido.delete(self._criterio(caso_id, criterio_id))

    def _rubrica(self, caso_id: uuid.UUID) -> Rubrica:
        rubrica = self._contenido.get_rubrica(caso_id)
        if rubrica is None:
            raise NotFoundError("El caso no tiene rúbrica.")
        return rubrica

    def _criterio(self, caso_id: uuid.UUID, criterio_id: uuid.UUID) -> CriterioRubrica:
        criterio = self._contenido.get_criterio(caso_id, criterio_id)
        if criterio is None:
            raise NotFoundError("Criterio no encontrado en la rúbrica de este caso.")
        return criterio
