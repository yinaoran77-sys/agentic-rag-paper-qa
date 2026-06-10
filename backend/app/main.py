"""
Agentic RAG Paper QA - FastAPI 后端主入口

Phase 2 学习目标：
- 理解 FastAPI 的 ASGI 架构
- 掌握依赖注入系统
- 学会组织大型 FastAPI 项目
- 配置中间件（CORS、日志、异常处理）
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.logging import setup_logging
from app.core.exceptions import setup_exception_handlers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理器

    startup:  初始化连接池、加载模型、初始化缓存和观测
    shutdown:  释放资源、关闭连接、flush 观测数据
    """
    # ====== Startup ======
    setup_logging()
    print(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} 启动中...")

    # ---- Phase 3: 初始化数据库连接池 ----
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("✅ 数据库连接成功")
    except Exception as e:
        print(f"❌ 数据库连接失败：{e}")
        raise

    # ---- Phase 4: 初始化 OpenSearch 客户端 ----
    from app.services.opensearch import opensearch_service
    try:
        os_client = await opensearch_service.get_client()
        ping_ok = await opensearch_service.ping()
        if ping_ok:
            print("✅ OpenSearch 连接成功")
            await opensearch_service.create_index()
        else:
            print("⚠️  OpenSearch 无响应（索引功能不可用）")
    except Exception as e:
        print(f"⚠️  OpenSearch 连接失败：{e}（索引功能不可用）")

    # ---- Phase 5: LangGraph Agent（已通过 agent_service 单例懒加载）----
    print("✅ LangGraph Agent 服务已就绪（懒加载）")

    # ---- Phase 7: 初始化 Redis 语义缓存 ----
    from app.services.cache import semantic_cache
    try:
        _ = await semantic_cache._get_redis()
        print("✅ Redis 语义缓存已初始化")
    except Exception as e:
        print(f"⚠️  Redis 不可用，语义缓存停用：{e}")

    # ---- Phase 7: 初始化 Langfuse 可观测 ----
    from app.services.langfuse_service import langfuse_service
    langfuse_service.init()
    if langfuse_service.enabled:
        print("✅ Langfuse 可观测已初始化")
    else:
        print("ℹ️  Langfuse 未配置（可选），可观测功能停用")

    print(f"🎉 {settings.APP_NAME} 启动完成！")
    yield

    # ====== Shutdown ======
    print("👋 应用关闭，释放资源...")
    await engine.dispose()
    print("✅ 数据库连接池已释放")
    try:
        await opensearch_service.close()
        print("✅ OpenSearch 客户端已关闭")
    except Exception:
        pass
    try:
        await semantic_cache.close()
        print("✅ Redis 语义缓存已关闭")
    except Exception:
        pass
    try:
        langfuse_service.shutdown()
    except Exception:
        pass


def create_app() -> FastAPI:
    """
    应用工厂模式

    为什么用工厂模式？
    - 方便测试时创建不同配置的实例
    - 避免全局状态污染
    - 符合依赖注入的最佳实践
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="基于 Agentic RAG 的智能论文问答系统",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ---- 中间件注册（顺序很重要！）----

    # 1. CORS - 跨域支持
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # TODO Phase 7: 2. 请求日志中间件
    # TODO Phase 7: 3. 请求 ID 追踪中间件
    # TODO Phase 7: 4. 性能计时中间件

    # ---- 异常处理 ----
    setup_exception_handlers(app)

    # ---- 路由注册 ----
    app.include_router(api_router, prefix="/api")

    # ---- 前端静态文件（Phase 9，用路由方式 serve，避免 mount 劫持）----
    import os
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend")
    index_path = os.path.join(frontend_dir, "index.html")

    if os.path.exists(index_path):
        from fastapi.responses import HTMLResponse

        @app.get("/", response_class=HTMLResponse)
        async def serve_frontend():
            with open(index_path, "r", encoding="utf-8") as f:
                return f.read()

        # 也 mount 静态资源（CSS/JS 等如果有的话）
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")
        print(f"✅ 前端已挂载：{frontend_dir}")

    # ---- 健康检查 ----
    @app.get("/health", tags=["health"])
    async def health_check():
        return {
            "status": "healthy",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
        }

    @app.get("/api", tags=["root"])
    async def api_root():
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/health",
        }

    return app


# 创建应用实例
app = create_app()
