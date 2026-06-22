from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.cache import close_cache
from app.core.config import settings
from app.core.database import create_tables
from app.seed import seed_data


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_tables()
    await seed_data()
    yield
    await close_cache()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="学徒行用户端、装修管理后台统一 REST API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=settings.cors_origins.strip() != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/health", tags=["系统"])
async def health():
    return {"status": "ok", "service": settings.app_name, "environment": settings.environment}
