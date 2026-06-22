from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.public import router as public_router
from app.api.support import router as support_router

api_router = APIRouter()
api_router.include_router(public_router)
api_router.include_router(admin_router)
api_router.include_router(support_router)
