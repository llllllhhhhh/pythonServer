from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.cache import close_cache
from app.core.config import settings
from app.core.database import create_tables
from app.seed import seed_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    await create_tables()
    await seed_data()
    yield
    await close_cache()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="学徒行用户端、管理端与客服中心统一 REST API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_origins.strip() != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}
