"""Excepciones de aplicación y su traducción a respuestas HTTP.

Los servicios lanzan estas excepciones sin depender de FastAPI; el manejador
registrado en `main.py` las convierte en JSON con el código HTTP adecuado.
Los módulos posteriores pueden reutilizarlas directamente.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    default_detail: str = "Solicitud inválida."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.default_detail
        super().__init__(self.detail)


class BusinessRuleError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "La operación infringe una regla de negocio."


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "No autenticado."


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "No tiene permisos para realizar esta acción."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Recurso no encontrado."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "El recurso entra en conflicto con uno existente."


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
