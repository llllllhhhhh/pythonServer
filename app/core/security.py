from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理端密钥无效",
        )
