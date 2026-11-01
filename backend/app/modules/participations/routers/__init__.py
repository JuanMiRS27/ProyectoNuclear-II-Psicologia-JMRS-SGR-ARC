import uuid

from fastapi import APIRouter, Response, status

from app.modules.auth.schemas import MessageResponse
from app.modules.participations.dependencies import AccesoTokenDep, ParticipacionServiceDep, RespuestaServiceDep
from app.modules.participations.schemas import CuestionarioRead, ParticipacionRead, RespuestaRead, RespuestaRequest
from app.modules.sessions.dependencies import AccesoEstudiantilDep

router = APIRouter(prefix="/student", tags=["Participaciones"])

NO_STORE = {"Cache-Control": "no-store"}


def _r(*codigos: int) -> dict:
    textos = {
        400: "La opción no pertenece a la pregunta.",
        401: "Token estudiantil ausente, inválido, expirado o revocado.",
        403: "Ventana de ingreso cerrada, sesión no iniciada o convocatoria revocada.",
        404: "Participación o pregunta inexistente o fuera del alcance del token.",
        409: "Intento cerrado, tiempo agotado, intentos agotados o sesión no en curso.",
    }
    return {c: {"model": MessageResponse, "description": textos[c]} for c in codigos}


@router.post(
    "/sessions/{session_id}/participations",
    response_model=ParticipacionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Iniciar un intento o recuperar el que está en progreso",
    description=(
        "**201 Created**: se creó un intento nuevo (ventana de ingreso abierta e intentos disponibles). "
        "**200 OK**: ya había un intento EN_PROGRESO con tiempo; se devuelve el mismo, sin consumir otro "
        "intento ni reiniciar el temporizador (también fuera de la ventana de ingreso)."
    ),
    responses={200: {"model": ParticipacionRead, "description": "Intento en progreso recuperado."}, **_r(401, 403, 409)},
)
def start_participation(
    session_id: uuid.UUID, acceso: AccesoEstudiantilDep, service: ParticipacionServiceDep, response: Response
) -> ParticipacionRead:
    participacion, creada = service.iniciar_o_recuperar(acceso)
    response.status_code = status.HTTP_201_CREATED if creada else status.HTTP_200_OK
    response.headers.update(NO_STORE)
    return participacion


@router.get("/participations", response_model=list[ParticipacionRead], summary="Mis intentos en esta convocatoria", responses=_r(401))
def list_participations(acceso: AccesoTokenDep, service: ParticipacionServiceDep) -> list[ParticipacionRead]:
    return service.listar(acceso)


@router.get(
    "/participations/{participation_id}", response_model=ParticipacionRead,
    summary="Estado, tiempo restante y progreso de un intento", responses=_r(401, 404),
)
def get_participation(participation_id: uuid.UUID, acceso: AccesoTokenDep, service: ParticipacionServiceDep, response: Response) -> ParticipacionRead:
    response.headers.update(NO_STORE)
    return service.obtener(acceso, participation_id)


@router.get(
    "/participations/{participation_id}/questionnaire", response_model=CuestionarioRead,
    summary="Cuestionario seguro (sin respuestas correctas ni justificaciones)", responses=_r(401, 404),
)
def get_questionnaire(participation_id: uuid.UUID, acceso: AccesoTokenDep, service: ParticipacionServiceDep, response: Response) -> CuestionarioRead:
    response.headers.update(NO_STORE)
    return service.cuestionario(acceso, participation_id)


@router.put(
    "/participations/{participation_id}/answers/{question_id}", response_model=RespuestaRead,
    summary="Guardar o cambiar la respuesta de una pregunta (idempotente)", responses=_r(400, 401, 404, 409),
)
def save_answer(
    participation_id: uuid.UUID, question_id: uuid.UUID, data: RespuestaRequest, acceso: AccesoTokenDep, service: RespuestaServiceDep
) -> RespuestaRead:
    return service.guardar(acceso, participation_id, question_id, data.opcion_id)


@router.get(
    "/participations/{participation_id}/answers", response_model=list[RespuestaRead],
    summary="Selecciones guardadas", responses=_r(401, 404),
)
def list_answers(participation_id: uuid.UUID, acceso: AccesoTokenDep, service: RespuestaServiceDep) -> list[RespuestaRead]:
    return service.listar(acceso, participation_id)


@router.delete(
    "/participations/{participation_id}/answers/{question_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary="Dejar una pregunta sin responder", responses=_r(401, 404, 409),
)
def delete_answer(participation_id: uuid.UUID, question_id: uuid.UUID, acceso: AccesoTokenDep, service: RespuestaServiceDep) -> Response:
    service.eliminar(acceso, participation_id, question_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/participations/{participation_id}/finish", response_model=ParticipacionRead,
    summary="Finalizar el intento (idempotente; no exige responder todo)", responses=_r(401, 404),
)
def finish_participation(participation_id: uuid.UUID, acceso: AccesoTokenDep, service: ParticipacionServiceDep) -> ParticipacionRead:
    return service.finalizar(acceso, participation_id)
