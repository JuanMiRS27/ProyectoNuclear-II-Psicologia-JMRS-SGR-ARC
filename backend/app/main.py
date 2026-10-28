from fastapi import FastAPI

from app.core.errores import ErrorDominio, manejar_error_dominio
from app.usuarios.router import router_auth, router_espacios, router_usuarios

app = FastAPI(
    title="Simulador de Psicología Social",
    description="API del Proyecto Nuclear II",
    version="0.1.0",
)

app.add_exception_handler(ErrorDominio, manejar_error_dominio)

app.include_router(router_auth)
app.include_router(router_usuarios)
app.include_router(router_espacios)


@app.get("/health", tags=["sistema"])
def health() -> dict[str, str]:
    return {"estado": "ok"}
