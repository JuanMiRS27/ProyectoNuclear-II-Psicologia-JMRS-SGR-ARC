from fastapi import FastAPI

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.modules.auth.routers import router as auth_module_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="Backend del Simulador de Psicología Social. Módulo 1: Autenticación y Gestión de Usuarios.",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    register_exception_handlers(app)

    # Cada módulo aporta su router; los módulos 2-7 se registrarán aquí.
    app.include_router(auth_module_router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["Sistema"], summary="Comprobación de disponibilidad")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
