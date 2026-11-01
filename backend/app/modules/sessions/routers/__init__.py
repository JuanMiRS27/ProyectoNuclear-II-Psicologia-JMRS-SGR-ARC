from fastapi import APIRouter

from app.modules.sessions.routers.espacios_router import router as espacios_router
from app.modules.sessions.routers.estudiante_router import router as estudiante_router
from app.modules.sessions.routers.sesiones_router import router as sesiones_router

router = APIRouter()
router.include_router(espacios_router)
router.include_router(sesiones_router)
router.include_router(estudiante_router)

__all__ = ["router"]
