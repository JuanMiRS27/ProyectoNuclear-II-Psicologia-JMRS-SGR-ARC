from fastapi import APIRouter

from app.modules.auth.routers.auth_router import router as auth_router
from app.modules.auth.routers.users_router import router as users_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(users_router)

__all__ = ["router"]
