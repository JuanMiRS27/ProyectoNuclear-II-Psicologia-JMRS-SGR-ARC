import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.modules.auth.dependencies import DocenteUser
from app.modules.auth.schemas import MessageResponse
from app.modules.sessions.dependencies import ConvocatoriaServiceDep, SesionServiceDep
from app.modules.sessions.models import EstadoSesionEnum, Sesion
from app.modules.sessions.schemas import (
    CodigoConvocatoriaRead,
    ConvocatoriaCreate,
    ConvocatoriaRead,
    IntentosUpdate,
    RegenerarCodigoRequest,
    SesionCreate,
    SesionList,
    SesionRead,
    SesionUpdate,
)
from app.modules.sessions.services import SesionService

router = APIRouter(prefix="/sessions")

_SES = ["Sesiones"]
_CONV = ["Convocatorias"]


def _r(*codigos: int) -> dict:
    textos = {
        400: "Regla de negocio inválida.",
        401: "No autenticado.",
        403: "Se requiere rol DOCENTE.",
        404: "Recurso inexistente o de otro docente.",
        409: "Operación no permitida en el estado actual, o duplicado.",
    }
    return {c: {"model": MessageResponse, "description": textos[c]} for c in codigos}


LECTURA = _r(401, 403, 404)
ESCRITURA = _r(400, 401, 403, 404, 409)
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _leer(sesion: Sesion, service: SesionService, total: int | None = None) -> SesionRead:
    campos = {c: getattr(sesion, c) for c in SesionRead.model_fields if c != "total_convocatorias"}
    campos["total_convocatorias"] = service.total_convocatorias(sesion) if total is None else total
    return SesionRead.model_validate(campos)


# --- Sesiones ---------------------------------------------------------------------------


@router.post(
    "", response_model=SesionRead, status_code=status.HTTP_201_CREATED, tags=_SES,
    summary="Programar sesión (con sus convocatorias)", responses=ESCRITURA,
)
def schedule_session(data: SesionCreate, docente: DocenteUser, service: SesionServiceDep) -> SesionRead:
    return _leer(service.programar(docente, data), service)


@router.get("", response_model=SesionList, tags=_SES, summary="Listar mis sesiones", responses=_r(401, 403))
def list_sessions(
    docente: DocenteUser,
    service: SesionServiceDep,
    estado: EstadoSesionEnum | None = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SesionList:
    sesiones, total, conteos = service.listar(docente, estado=estado, skip=skip, limit=limit)
    return SesionList(
        items=[_leer(s, service, conteos.get(s.id, 0)) for s in sesiones], total=total, skip=skip, limit=limit
    )


@router.get("/{session_id}", response_model=SesionRead, tags=_SES, summary="Consultar sesión", responses=LECTURA)
def get_session(session_id: uuid.UUID, docente: DocenteUser, service: SesionServiceDep) -> SesionRead:
    return _leer(service.obtener(docente, session_id), service)


@router.patch(
    "/{session_id}", response_model=SesionRead, tags=_SES,
    summary="Modificar la programación (EN_CURSO: solo lugar y mensaje)", responses=ESCRITURA,
)
def update_session(session_id: uuid.UUID, data: SesionUpdate, docente: DocenteUser, service: SesionServiceDep) -> SesionRead:
    return _leer(service.actualizar(docente, session_id, data), service)


@router.post("/{session_id}/start", response_model=SesionRead, tags=_SES, summary="Iniciar sesión", responses=ESCRITURA)
def start_session(session_id: uuid.UUID, docente: DocenteUser, service: SesionServiceDep) -> SesionRead:
    return _leer(service.iniciar(docente, session_id), service)


@router.post("/{session_id}/finish", response_model=SesionRead, tags=_SES, summary="Finalizar sesión", responses=ESCRITURA)
def finish_session(session_id: uuid.UUID, docente: DocenteUser, service: SesionServiceDep) -> SesionRead:
    return _leer(service.finalizar(docente, session_id), service)


# --- Convocatorias ----------------------------------------------------------------------


@router.post(
    "/{session_id}/convocations", response_model=list[ConvocatoriaRead], status_code=status.HTTP_201_CREATED,
    tags=_CONV, summary="Convocar estudiantes (por correo institucional)", responses=ESCRITURA,
)
def add_convocations(
    session_id: uuid.UUID, data: ConvocatoriaCreate, docente: DocenteUser, service: ConvocatoriaServiceDep
) -> list[ConvocatoriaRead]:
    return [ConvocatoriaRead.model_validate(c) for c in service.agregar(docente, session_id, data.estudiantes)]


@router.get(
    "/{session_id}/convocations", response_model=list[ConvocatoriaRead], tags=_CONV,
    summary="Listar convocatorias (sin códigos; por defecto solo las activas)", responses=LECTURA,
)
def list_convocations(
    session_id: uuid.UUID, docente: DocenteUser, service: ConvocatoriaServiceDep, incluir_revocadas: bool = False
) -> list[ConvocatoriaRead]:
    return [ConvocatoriaRead.model_validate(c) for c in service.listar(docente, session_id, incluir_revocadas=incluir_revocadas)]


@router.get(
    "/{session_id}/convocations/{convocation_id}", response_model=ConvocatoriaRead, tags=_CONV,
    summary="Consultar convocatoria (sin código)", responses=LECTURA,
)
def get_convocation(
    session_id: uuid.UUID, convocation_id: uuid.UUID, docente: DocenteUser, service: ConvocatoriaServiceDep
) -> ConvocatoriaRead:
    return ConvocatoriaRead.model_validate(service.obtener(docente, session_id, convocation_id))


@router.delete(
    "/{session_id}/convocations/{convocation_id}", status_code=status.HTTP_204_NO_CONTENT, tags=_CONV,
    summary="Retirar estudiante: revocación lógica (pierde el acceso de inmediato; la convocatoria se conserva)",
    responses=ESCRITURA,
)
def remove_convocation(
    session_id: uuid.UUID, convocation_id: uuid.UUID, docente: DocenteUser, service: ConvocatoriaServiceDep
) -> Response:
    service.retirar(docente, session_id, convocation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{session_id}/convocations/{convocation_id}/attempts", response_model=ConvocatoriaRead, tags=_CONV,
    summary="Aumentar intentos autorizados (excepcional)", responses=ESCRITURA,
)
def update_attempts(
    session_id: uuid.UUID,
    convocation_id: uuid.UUID,
    data: IntentosUpdate,
    docente: DocenteUser,
    service: ConvocatoriaServiceDep,
) -> ConvocatoriaRead:
    convocatoria = service.modificar_intentos(docente, session_id, convocation_id, data.intentos_autorizados, data.motivo)
    return ConvocatoriaRead.model_validate(convocatoria)


@router.post(
    "/{session_id}/convocations/{convocation_id}/regenerate-code", response_model=CodigoConvocatoriaRead, tags=_CONV,
    summary="Regenerar código (invalida el anterior y sus accesos)", responses=ESCRITURA,
)
def regenerate_code(
    session_id: uuid.UUID,
    convocation_id: uuid.UUID,
    data: RegenerarCodigoRequest,
    docente: DocenteUser,
    service: ConvocatoriaServiceDep,
    response: Response,
) -> CodigoConvocatoriaRead:
    response.headers.update(NO_STORE)
    return service.regenerar_codigo(docente, session_id, convocation_id, data.motivo)


@router.get(
    "/{session_id}/convocations/{convocation_id}/code", response_model=CodigoConvocatoriaRead, tags=_CONV,
    summary="Consultar el código (solo el docente responsable; entrega provisional hasta el módulo 6)",
    responses=LECTURA,
)
def get_convocation_code(
    session_id: uuid.UUID,
    convocation_id: uuid.UUID,
    docente: DocenteUser,
    service: ConvocatoriaServiceDep,
    response: Response,
) -> CodigoConvocatoriaRead:
    response.headers.update(NO_STORE)
    return service.obtener_codigo(docente, session_id, convocation_id)
