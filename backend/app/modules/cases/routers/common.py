from typing import Any

from app.modules.auth.schemas import MessageResponse
from app.modules.cases.schemas import ResultadoValidacionRead


class PublicacionFallidaResponse(ResultadoValidacionRead):
    detail: str


def responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    descripciones = {
        400: "Regla de negocio inválida.",
        401: "Token ausente, inválido o expirado.",
        403: "Se requiere un docente (y, para modificar, el permiso de crear casos).",
        404: "Recurso inexistente o perteneciente a otro docente.",
        409: "Conflicto con el estado del caso o con un orden ya ocupado.",
    }
    return {code: {"model": MessageResponse, "description": descripciones[code]} for code in codes}


AUTH = responses(401, 403)
LECTURA = {**AUTH, **responses(404)}
EDICION = {**AUTH, **responses(400, 404, 409)}
