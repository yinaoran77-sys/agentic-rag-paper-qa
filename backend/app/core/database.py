"""
数据库连接池配置

为什么需要连接池？
- 每个 HTTP 请求都要读写数据库
- 频繁建立/断开连接非常慢（TCP 握手 × N）
- 连接池复用连接，性能提升 10x+

为什么用异步（async）？
- FastAPI 是异步框架，数据库操作不阻塞其他请求
- asyncpg 是 PostgreSQL 最快的异步驱动
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

# ============================================================
# 异步引擎（Engine）
# ============================================================
# AsyncEngine：管理数据库连接池的核心对象
# - pool_size=10：同时保持 10 个连接
# - max_overflow=20：高峰期最多再开 20 个临时连接
# - pool_pre_ping=True：每次取连接前先 ping 一下，防止用断开的连接
# - echo=False：不打印所有 SQL（调试时可以打开）
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)

# ============================================================
# 会话工厂（SessionMaker）
# ============================================================
# AsyncSession：每次 HTTP 请求拿一个会话，用完关闭
# - autocommit=False：必须手动 commit（安全，防止误提交）
# - autoflush=True：查询前自动把待写入的数据刷到数据库
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=True,
    expire_on_commit=False,  # commit 后对象仍然可用（避免懒加载报错）
)

# ============================================================
# 模型基类（Base）
# ============================================================
# declarative_base：所有模型类都要继承这个 Base
# - 自动把 Python 类 → 数据库表
# - 自动把类属性 → 表字段
# - 支持类型检查（SQLAlchemy 2.0 新特性）
Base = declarative_base()


# ============================================================
# 依赖注入：获取数据库会话
# ============================================================
# FastAPI 的依赖注入系统：每个请求自动拿到一个 db session
# - yield：请求结束时自动关闭 session（即使中途报错也会关闭）
# - 这是 FastAPI + SQLAlchemy 的标准写法
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库会话（FastAPI 依赖注入）
    
    用法：
        @app.get("/documents")
        async def list_docs(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
