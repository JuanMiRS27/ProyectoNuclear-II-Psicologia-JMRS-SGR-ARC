import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.core.database import OFFSET_MAXIMO
from app.modules.cases.dependencies import CasoServiceDep, Editor, Lector
from app.modules.cases.dsl import CaseDSLDocument
from app.modules.cases.models import EstadoCasoEnum
from app.modules.cases.routers.common import AUTH, EDICION, LECTURA, PublicacionFallidaResponse
from app.modules.cases.schemas import CasoCreate, CasoDetalle, CasoList, CasoResumen, CasoUpdate, ResultadoValidacionRead

router = APIRouter(prefix="/cases", tags=["Casos académicos"])


@router.post("", response_model=CasoDetalle, status_code=status.HTTP_201_CREATED, summary="Crear un caso en borrador", responses=AUTH)
def create_case(data: CasoCreate, autor: Editor, service: CasoServiceDep) -> CasoDetalle:
    return CasoDetalle.model_validate(service.crear_borrador(autor, data))


@router.post(
    "/full",
    response_model=CasoDetalle,
    status_code=status.HTTP_201_CREATED,
    summary="Construir un caso completo desde CaseDSL (Builder)",
    description="Crea el caso, sus escenarios, preguntas, opciones y rúbrica en una sola transacción. "
    "El caso queda en BORRADOR; el campo `caso.estado` del documento se ignora.",
    responses=AUTH,
)
def create_full_case(documento: CaseDSLDocument, autor: Editor, service: CasoServiceDep) -> CasoDetalle:
    return CasoDetalle.model_validate(service.crear_completo(autor, documento))


@router.get("", response_model=CasoList, summary="Listar mis casos", responses=AUTH)
def list_cases(
    autor: Lector,
    service: CasoServiceDep,
    estado: EstadoCasoEnum | None = None,
    skip: Annotated[int, Query(ge=0, le=OFFSET_MAXIMO)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CasoList:
    casos, total = service.listar(autor, estado=estado, skip=skip, limit=limit)
    return CasoList(items=[CasoResumen.model_validate(c) for c in casos], total=total, skip=skip, limit=limit)


@router.get("/{case_id}", response_model=CasoDetalle, summary="Consultar un caso completo", responses=LECTURA)
def get_case(case_id: uuid.UUID, autor: Lector, service: CasoServiceDep) -> CasoDetalle:
    return CasoDetalle.model_validate(service.obtener(autor, case_id))


@router.patch("/{case_id}", response_model=CasoDetalle, summary="Modificar la información general (solo borrador)", responses=EDICION)
def update_case(case_id: uuid.UUID, data: CasoUpdate, autor: Editor, service: CasoServiceDep) -> CasoDetalle:
    return CasoDetalle.model_validate(service.actualizar(autor, case_id, data))


@router.delete(
    "/{case_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Eliminar un borrador", responses=EDICION
)
def delete_case(case_id: uuid.UUID, autor: Editor, service: CasoServiceDep) -> Response:
    service.eliminar(autor, case_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{case_id}/validate",
    response_model=ResultadoValidacionRead,
    summary="Validar el caso sin cambiar su estado",
    responses=LECTURA,
)
def validate_case(case_id: uuid.UUID, autor: Lector, service: CasoServiceDep) -> ResultadoValidacionRead:
    return ResultadoValidacionRead.model_validate(service.validar(autor, case_id))


@router.post(
    "/{case_id}/publish",
    response_model=CasoDetalle,
    summary="Validar y publicar el caso",
    responses={
        **EDICION,
        422: {"model": PublicacionFallidaResponse, "description": "El caso no supera la validación; sigue en BORRADOR."},
    },
)
def publish_case(case_id: uuid.UUID, autor: Editor, service: CasoServiceDep) -> CasoDetalle:
    return CasoDetalle.model_validate(service.publicar(autor, case_id))


@router.post("/{case_id}/archive", response_model=CasoDetalle, summary="Archivar un caso publicado", responses=EDICION)
def archive_case(case_id: uuid.UUID, autor: Editor, service: CasoServiceDep) -> CasoDetalle:
    return CasoDetalle.model_validate(service.archivar(autor, case_id))


@router.get(
    "/{case_id}/dsl",
    response_model=CaseDSLDocument,
    response_model_exclude_none=False,
    summary="Exportar el caso en CaseDSL 1.0",
    responses=LECTURA,
)
def export_case_dsl(case_id: uuid.UUID, autor: Lector, service: CasoServiceDep) -> CaseDSLDocument:
    return service.exportar_dsl(autor, case_id)
