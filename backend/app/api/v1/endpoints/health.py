"""
健康检查路由

Phase 2 学习要点：
- FastAPI Router 的用法
- 依赖注入（Depends）
- 响应模型（response_model）
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, settings

router = APIRouter()


class HealthResponse(BaseModel):
    """健康检查响应模型"""
    status: str
    app_name: str
    version: str
    debug: bool


@router.get("", response_model=HealthResponse)
async def health_check():
    """
    详细健康检查
    
    返回应用运行状态、版本信息等
    """
    return HealthResponse(
        status="healthy",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
    )
