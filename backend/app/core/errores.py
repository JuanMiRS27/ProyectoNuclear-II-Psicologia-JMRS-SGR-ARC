"""Errores de dominio compartidos. Los servicios los lanzan y main.py los
traduce a respuestas HTTP, así los servicios no dependen de FastAPI."""

from fastapi import Request, status
from fastapi.responses import JSONResponse


class ErrorDominio(Exception):
    codigo_http = status.HTTP_400_BAD_REQUEST

    def __init__(self, mensaje: str) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje


class NoEncontrado(ErrorDominio):
    codigo_http = status.HTTP_404_NOT_FOUND


class Conflicto(ErrorDominio):
    codigo_http = status.HTTP_409_CONFLICT


class ReglaNegocio(ErrorDominio):
    codigo_http = status.HTTP_422_UNPROCESSABLE_CONTENT


async def manejar_error_dominio(_: Request, error: ErrorDominio) -> JSONResponse:
    return JSONResponse(status_code=error.codigo_http, content={"detail": error.mensaje})
