from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.public import router as public_router
from app.api.support import router as support_router
from app.api.commerce import router as commerce_router
from app.api.verification import router as verification_router
from app.api.merchant import router as merchant_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(public_router)
api_router.include_router(admin_router)
api_router.include_router(support_router)
api_router.include_router(commerce_router)
api_router.include_router(verification_router)
api_router.include_router(merchant_router)
