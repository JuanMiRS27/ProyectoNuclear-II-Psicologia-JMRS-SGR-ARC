from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import SesionLocal
from app.core.errores import ErrorDominio, manejar_error_dominio
from app.notificaciones.correo import obtener_servicio_correo
from app.notificaciones.eventos import publicador_eventos
from app.notificaciones.observadores import registrar_observadores
from app.usuarios.router import router_auth, router_espacios, router_usuarios


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    registrar_observadores(publicador_eventos, SesionLocal, obtener_servicio_correo())
    yield
    publicador_eventos.limpiar()


app = FastAPI(
    title="Simulador de Psicología Social",
    description="API del Proyecto Nuclear II",
    version="0.1.0",
    lifespan=ciclo_de_vida,
)

app.add_exception_handler(ErrorDominio, manejar_error_dominio)

app.include_router(router_auth)
app.include_router(router_usuarios)
app.include_router(router_espacios)


@app.get("/health", tags=["sistema"])
def health() -> dict[str, str]:
    return {"estado": "ok"}
