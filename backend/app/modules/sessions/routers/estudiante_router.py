import uuid

from fastapi import APIRouter, Request, Response

from app.modules.auth.schemas import MessageResponse
from app.modules.sessions.dependencies import AccesoEstudiantilDep, AccesoServiceDep, LimitadorDep, RelojDep
from app.modules.sessions.schemas import AccesoEstudiantilRead, SesionEstudianteRead, StudentLoginRequest, StudentTokenResponse

router = APIRouter(tags=["Acceso estudiantil"])


@router.post(
    "/auth/student/login",
    response_model=StudentTokenResponse,
    summary="Ingreso de estudiante (correo institucional + código de convocatoria)",
    description="Devuelve un token de alcance estudiantil, válido solo para la convocatoria y sesión indicadas. "
    "Su expiración (STUDENT_TOKEN_EXPIRE_MINUTES) es de seguridad e independiente de `tiempo_max_min`. "
    "No sirve en endpoints administrativos ni docentes.",
    responses={
        401: {"model": MessageResponse, "description": "Correo o código incorrectos (mensaje genérico)."},
        403: {"model": MessageResponse, "description": "Cuenta desactivada, sesión no iniciada o finalizada, o plazo de ingreso vencido."},
        429: {"model": MessageResponse, "description": "Demasiados intentos fallidos; ver cabecera Retry-After."},
    },
)
def student_login(
    data: StudentLoginRequest, request: Request, response: Response, service: AccesoServiceDep, limitador: LimitadorDep
) -> StudentTokenResponse:
    response.headers["Cache-Control"] = "no-store"
    ip = request.client.host if request.client else "desconocida"
    return service.ingresar(data.correo, data.codigo, ip, limitador)


@router.get(
    "/student/sessions/{session_id}",
    response_model=AccesoEstudiantilRead,
    summary="Datos de la sesión a la que el estudiante tiene acceso",
    description="Comprueba que el token estudiantil siga vigente para esta sesión. "
    "El contenido académico y las participaciones corresponden al módulo 4.",
    responses={
        401: {"model": MessageResponse, "description": "Token ausente, inválido, expirado o revocado."},
        403: {"model": MessageResponse, "description": "El token fue emitido para otra sesión."},
    },
)
def student_session(session_id: uuid.UUID, acceso: AccesoEstudiantilDep, reloj: RelojDep) -> AccesoEstudiantilRead:
    sesion = acceso.sesion
    return AccesoEstudiantilRead(
        estudiante_id=acceso.estudiante.id,
        nombre_completo=acceso.estudiante.nombre_completo,
        convocatoria_id=acceso.convocatoria.id,
        intentos_autorizados=acceso.convocatoria.intentos_autorizados,
        puede_iniciar_participacion=acceso.puede_iniciar_participacion(reloj.ahora()),
        sesion=SesionEstudianteRead(
            id=sesion.id,
            caso_nombre=sesion.caso.nombre,
            inicio=sesion.inicio,
            fin=sesion.fin,
            limite_ingreso=sesion.limite_ingreso,
            tiempo_max_min=sesion.tiempo_max_min,
            lugar=sesion.lugar,
            mensaje=sesion.mensaje,
            estado=sesion.estado,
        ),
    )
