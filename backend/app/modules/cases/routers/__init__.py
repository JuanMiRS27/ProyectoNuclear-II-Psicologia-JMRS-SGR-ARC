from fastapi import APIRouter

from app.modules.cases.routers.casos_router import router as casos_router
from app.modules.cases.routers.contenido_router import router as contenido_router
from app.modules.cases.routers.rubrica_router import router as rubrica_router

router = APIRouter()
router.include_router(casos_router)
router.include_router(contenido_router)
router.include_router(rubrica_router)

__all__ = ["router"]
