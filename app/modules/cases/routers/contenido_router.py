"""Escenarios, preguntas y opciones de un caso.

Todas las rutas empiezan por /cases/{case_id}: el servicio comprueba primero la
autoría del caso y después que el subrecurso le pertenezca.
"""

import uuid

from fastapi import APIRouter, Response, status

from app.modules.cases.dependencies import Editor, EscenarioServiceDep, Lector, OpcionServiceDep, PreguntaServiceDep
from app.modules.cases.routers.common import EDICION, LECTURA
from app.modules.cases.schemas import (
    EscenarioCreate,
    EscenarioRead,
    EscenarioUpdate,
    OpcionCreate,
    OpcionRead,
    OpcionUpdate,
    PreguntaCreate,
    PreguntaRead,
    PreguntaUpdate,
    ReordenarRequest,
)

router = APIRouter(prefix="/cases/{case_id}")

_ESC = ["Escenarios"]
_PRE = ["Preguntas"]
_OPC = ["Opciones"]


# --- Escenarios ---------------------------------------------------------------


@router.post("/scenarios", response_model=EscenarioRead, status_code=status.HTTP_201_CREATED, tags=_ESC, summary="Crear escenario", responses=EDICION)
def create_scenario(case_id: uuid.UUID, data: EscenarioCreate, autor: Editor, service: EscenarioServiceDep) -> EscenarioRead:
    return EscenarioRead.model_validate(service.crear(autor, case_id, data))


@router.get("/scenarios", response_model=list[EscenarioRead], tags=_ESC, summary="Listar escenarios", responses=LECTURA)
def list_scenarios(case_id: uuid.UUID, autor: Lector, service: EscenarioServiceDep) -> list[EscenarioRead]:
    return [EscenarioRead.model_validate(e) for e in service.listar(autor, case_id)]


@router.put("/scenarios/order", response_model=list[EscenarioRead], tags=_ESC, summary="Reordenar escenarios", responses=EDICION)
def reorder_scenarios(case_id: uuid.UUID, data: ReordenarRequest, autor: Editor, service: EscenarioServiceDep) -> list[EscenarioRead]:
    return [EscenarioRead.model_validate(e) for e in service.reordenar(autor, case_id, data.ids)]


@router.get("/scenarios/{scenario_id}", response_model=EscenarioRead, tags=_ESC, summary="Consultar escenario", responses=LECTURA)
def get_scenario(case_id: uuid.UUID, scenario_id: uuid.UUID, autor: Lector, service: EscenarioServiceDep) -> EscenarioRead:
    return EscenarioRead.model_validate(service.obtener(autor, case_id, scenario_id))


@router.patch("/scenarios/{scenario_id}", response_model=EscenarioRead, tags=_ESC, summary="Modificar escenario", responses=EDICION)
def update_scenario(
    case_id: uuid.UUID, scenario_id: uuid.UUID, data: EscenarioUpdate, autor: Editor, service: EscenarioServiceDep
) -> EscenarioRead:
    return EscenarioRead.model_validate(service.actualizar(autor, case_id, scenario_id, data))


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT, tags=_ESC, summary="Eliminar escenario", responses=EDICION)
def delete_scenario(case_id: uuid.UUID, scenario_id: uuid.UUID, autor: Editor, service: EscenarioServiceDep) -> Response:
    service.eliminar(autor, case_id, scenario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Preguntas ----------------------------------------------------------------


@router.post(
    "/scenarios/{scenario_id}/questions",
    response_model=PreguntaRead,
    status_code=status.HTTP_201_CREATED,
    tags=_PRE,
    summary="Crear pregunta en un escenario",
    responses=EDICION,
)
def create_question(
    case_id: uuid.UUID, scenario_id: uuid.UUID, data: PreguntaCreate, autor: Editor, service: PreguntaServiceDep
) -> PreguntaRead:
    return PreguntaRead.model_validate(service.crear(autor, case_id, scenario_id, data))


@router.get("/scenarios/{scenario_id}/questions", response_model=list[PreguntaRead], tags=_PRE, summary="Listar preguntas de un escenario", responses=LECTURA)
def list_questions(case_id: uuid.UUID, scenario_id: uuid.UUID, autor: Lector, service: PreguntaServiceDep) -> list[PreguntaRead]:
    return [PreguntaRead.model_validate(p) for p in service.listar(autor, case_id, scenario_id)]


@router.put("/scenarios/{scenario_id}/questions/order", response_model=list[PreguntaRead], tags=_PRE, summary="Reordenar preguntas", responses=EDICION)
def reorder_questions(
    case_id: uuid.UUID, scenario_id: uuid.UUID, data: ReordenarRequest, autor: Editor, service: PreguntaServiceDep
) -> list[PreguntaRead]:
    return [PreguntaRead.model_validate(p) for p in service.reordenar(autor, case_id, scenario_id, data.ids)]


@router.get("/questions/{question_id}", response_model=PreguntaRead, tags=_PRE, summary="Consultar pregunta", responses=LECTURA)
def get_question(case_id: uuid.UUID, question_id: uuid.UUID, autor: Lector, service: PreguntaServiceDep) -> PreguntaRead:
    return PreguntaRead.model_validate(service.obtener(autor, case_id, question_id))


@router.patch(
    "/questions/{question_id}",
    response_model=PreguntaRead,
    tags=_PRE,
    summary="Modificar pregunta (enunciado o criterio)",
    responses=EDICION,
)
def update_question(
    case_id: uuid.UUID, question_id: uuid.UUID, data: PreguntaUpdate, autor: Editor, service: PreguntaServiceDep
) -> PreguntaRead:
    return PreguntaRead.model_validate(service.actualizar(autor, case_id, question_id, data))


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT, tags=_PRE, summary="Eliminar pregunta", responses=EDICION)
def delete_question(case_id: uuid.UUID, question_id: uuid.UUID, autor: Editor, service: PreguntaServiceDep) -> Response:
    service.eliminar(autor, case_id, question_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Opciones -----------------------------------------------------------------


@router.post(
    "/questions/{question_id}/options",
    response_model=OpcionRead,
    status_code=status.HTTP_201_CREATED,
    tags=_OPC,
    summary="Crear opción de respuesta",
    responses=EDICION,
)
def create_option(
    case_id: uuid.UUID, question_id: uuid.UUID, data: OpcionCreate, autor: Editor, service: OpcionServiceDep
) -> OpcionRead:
    return OpcionRead.model_validate(service.crear(autor, case_id, question_id, data))


@router.get("/questions/{question_id}/options", response_model=list[OpcionRead], tags=_OPC, summary="Listar opciones", responses=LECTURA)
def list_options(case_id: uuid.UUID, question_id: uuid.UUID, autor: Lector, service: OpcionServiceDep) -> list[OpcionRead]:
    return [OpcionRead.model_validate(o) for o in service.listar(autor, case_id, question_id)]


@router.put("/questions/{question_id}/options/order", response_model=list[OpcionRead], tags=_OPC, summary="Reordenar opciones", responses=EDICION)
def reorder_options(
    case_id: uuid.UUID, question_id: uuid.UUID, data: ReordenarRequest, autor: Editor, service: OpcionServiceDep
) -> list[OpcionRead]:
    return [OpcionRead.model_validate(o) for o in service.reordenar(autor, case_id, question_id, data.ids)]


@router.patch("/questions/{question_id}/options/{option_id}", response_model=OpcionRead, tags=_OPC, summary="Modificar opción", responses=EDICION)
def update_option(
    case_id: uuid.UUID,
    question_id: uuid.UUID,
    option_id: uuid.UUID,
    data: OpcionUpdate,
    autor: Editor,
    service: OpcionServiceDep,
) -> OpcionRead:
    return OpcionRead.model_validate(service.actualizar(autor, case_id, question_id, option_id, data))


@router.delete(
    "/questions/{question_id}/options/{option_id}", status_code=status.HTTP_204_NO_CONTENT, tags=_OPC, summary="Eliminar opción", responses=EDICION
)
def delete_option(
    case_id: uuid.UUID, question_id: uuid.UUID, option_id: uuid.UUID, autor: Editor, service: OpcionServiceDep
) -> Response:
    service.eliminar(autor, case_id, question_id, option_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
