from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.cache import close_cache, ensure_cache_available
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.migrations import ensure_database_current
from app.core.storage import cull_thumb_cache


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    cull_thumb_cache()
    await ensure_cache_available()
    await ensure_database_current()
    yield
    await close_cache()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="学徒行用户端、管理端与客服中心统一 REST API",
    lifespan=lifespan,
)
register_exception_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_origins.strip() != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/health", tags=["system"])
async def health():
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.environment,
        "version": settings.app_version,
        "commit": settings.build_commit,
    }
