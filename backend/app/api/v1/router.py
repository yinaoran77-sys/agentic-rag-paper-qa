"""
API 路由聚合

为什么用多级 router？
- v1/v2 版本隔离
- 模块化：每个 endpoint 文件独立
- 方便单元测试时只加载部分路由
"""

from fastapi import APIRouter

from app.api.v1.endpoints import chat, health, search, upload

api_router = APIRouter(prefix="/v1")

# ---- 注册子路由 ----
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(upload.router, prefix="/documents", tags=["documents"])
