import uuid

from fastapi import APIRouter, Response, status

from app.modules.cases.dependencies import Editor, Lector, RubricaServiceDep
from app.modules.cases.routers.common import EDICION, LECTURA
from app.modules.cases.schemas import CriterioCreate, CriterioRead, CriterioUpdate, RubricaCreate, RubricaRead, RubricaUpdate

router = APIRouter(prefix="/cases/{case_id}/rubric", tags=["Rúbrica"])


@router.post("", response_model=RubricaRead, status_code=status.HTTP_201_CREATED, summary="Crear la rúbrica del caso", responses=EDICION)
def create_rubric(case_id: uuid.UUID, data: RubricaCreate, autor: Editor, service: RubricaServiceDep) -> RubricaRead:
    return RubricaRead.model_validate(service.crear(autor, case_id, data))


@router.get("", response_model=RubricaRead, summary="Consultar la rúbrica", responses=LECTURA)
def get_rubric(case_id: uuid.UUID, autor: Lector, service: RubricaServiceDep) -> RubricaRead:
    return RubricaRead.model_validate(service.obtener(autor, case_id))


@router.patch("", response_model=RubricaRead, summary="Actualizar la escala máxima", responses=EDICION)
def update_rubric(case_id: uuid.UUID, data: RubricaUpdate, autor: Editor, service: RubricaServiceDep) -> RubricaRead:
    return RubricaRead.model_validate(service.actualizar(autor, case_id, data))


@router.post("/criteria", response_model=CriterioRead, status_code=status.HTTP_201_CREATED, summary="Crear criterio", responses=EDICION)
def create_criterion(case_id: uuid.UUID, data: CriterioCreate, autor: Editor, service: RubricaServiceDep) -> CriterioRead:
    return CriterioRead.model_validate(service.crear_criterio(autor, case_id, data))


@router.get("/criteria", response_model=list[CriterioRead], summary="Listar criterios", responses=LECTURA)
def list_criteria(case_id: uuid.UUID, autor: Lector, service: RubricaServiceDep) -> list[CriterioRead]:
    return [CriterioRead.model_validate(c) for c in service.listar_criterios(autor, case_id)]


@router.patch("/criteria/{criterion_id}", response_model=CriterioRead, summary="Modificar criterio", responses=EDICION)
def update_criterion(
    case_id: uuid.UUID, criterion_id: uuid.UUID, data: CriterioUpdate, autor: Editor, service: RubricaServiceDep
) -> CriterioRead:
    return CriterioRead.model_validate(service.actualizar_criterio(autor, case_id, criterion_id, data))


@router.delete("/criteria/{criterion_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar criterio", responses=EDICION)
def delete_criterion(case_id: uuid.UUID, criterion_id: uuid.UUID, autor: Editor, service: RubricaServiceDep) -> Response:
    service.eliminar_criterio(autor, case_id, criterion_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
