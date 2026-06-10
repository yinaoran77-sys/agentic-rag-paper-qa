"""
Alembic 迁移环境配置

这是 Alembic 的"大脑"：
- 告诉 Alembic 数据库地址在哪
- 告诉 Alembic 模型定义在哪（用来对比表结构变化）
- 定义迁移是"在线跑"（连真实数据库）还是"离线生成"（只输出 SQL）

为什么需要这个文件？
- Alembic 不知道你的项目结构
- 每次运行 `alembic revision` 或 `alembic upgrade`，都会加载这个文件
"""

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# ============================================================
# 把 backend/ 目录加入 Python 路径
# ============================================================
# 为什么？Alembic 运行时的工作目录可能是 backend/，也可能不是
# 写绝对路径最稳：不管在哪运行，都能找到 app/ 目录
# Python 的 import 系统需要知道"去哪找模块"
sys_path = Path(__file__).resolve().parent.parent  # backend/
if str(sys_path) not in os.sys.path:
    os.sys.path.insert(0, str(sys_path))

# ============================================================
# 导入配置和模型（关键！）
# ============================================================
# 如果不导入模型，Alembic 不知道有 Document/Chunk/Session/Message
# 结果：生成的迁移脚本是空的（Alembic 以为你没定义任何表）
from app.core.config import settings
from app.core.database import Base

# 必须导入模型类，否则 Base.metadata 是空的，Alembic 检测不到任何表
from app import models  # noqa: F401

# Alembic 内置的日志配置（读取 alembic.ini 里的 [loggers] 段）
# 作用：迁移时打印出正在执行哪条 SQL（方便调试）
if context.config is not None:
    fileConfig(context.config.config_file_name)

# target_metadata：Alembic 用来"对比"的基准
# - Alembic 读取 Base.metadata（所有表的定义）
# - 和数据库里真实的表结构对比
# - 差异 → 生成迁移脚本
target_metadata = Base.metadata


# ============================================================
# 离线迁移（生成 SQL 但不执行）
# ============================================================
def run_migrations_offline() -> None:
    """
    离线模式：只生成 SQL，不连数据库
    
    用途：
    - 审查 SQL（看看 Alembic 准备干什么）
    - CI/CD 环境（没权限连生产库，但想检查迁移脚本对不对）
    """
    if context.config is None:
        return
    
    url = settings.DATABASE_URL_SYNC  # 离线模式用同步 URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    
    with context.begin_transaction():
        context.run_migrations()


# ============================================================
# 在线迁移（连真实数据库并执行）
# ============================================================
def do_run_migrations(connection) -> None:
    """真正执行迁移的内部函数"""
    if context.config is None:
        return
    
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    异步版本的在线迁移
    
    为什么要用异步？
    - 我们的 engine 是 asyncpg（异步驱动）
    - Alembic 默认是同步的，需要用 async_engine_from_config 包一层
    """
    # 从 alembic.ini 里读取 sqlalchemy.url 配置
    if context.config is None:
        return
    
    configuration = context.config.get_section(context.config.config_ini_section)
    if configuration is None:
        configuration = {}
    
    # 覆盖 URL：用我们 settings 里的（更灵活，支持环境变量）
    # 为什么用 DATABASE_URL 而不是 DATABASE_URL_SYNC？
    # run_async_migrations() 用 async_engine_from_config()，需要异步驱动
    configuration["sqlalchemy.url"] = settings.DATABASE_URL
    
    # 创建异步引擎
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # 迁移时不需要连接池（只跑一次）
    )
    
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    
    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式入口"""
    asyncio.run(run_async_migrations())


# ============================================================
# Alembic 入口（判断是在线还是离线）
# ============================================================
# 这个 if 是 Alembic 的"main 函数"
# - 命令行运行 `alembic upgrade head` → 走在线模式
# - 命令行运行 `alembic upgrade head --sql` → 走离线模式
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
