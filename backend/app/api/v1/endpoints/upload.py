"""
文档上传与处理 API

端点：
- POST /api/v1/documents/upload  → 上传文件 + 触发后台处理
- GET  /api/v1/documents        → 列出所有文档
- GET  /api/v1/documents/{id}   → 查看单个文档详情
- DELETE /api/v1/documents/{id}   → 删除文档（含 PostgreSQL + OpenSearch 级联）

设计考虑：
- 为什么用 BackgroundTasks【FastAPI 后台任务】而不是 Celery【分布式任务队列】？
  目前是单节点开发环境，后台任务够用；生产环境后面换成 Airflow（Phase 6）。
- 为什么先写 PostgreSQL 再处理？
  先占坑（status=processing），处理失败了也能查到记录。
"""

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.document import Document, DocumentStatus
from app.services.document import process_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["documents"])

# ----------------------------------------------------------------
# 上传目录（跟 Docker volume 对应）
# ----------------------------------------------------------------
UPLOAD_DIR = Path(settings.UPLOAD_DIR)  # "data/papers"


# ----------------------------------------------------------------
# 响应模型（Pydantic v2）
# ----------------------------------------------------------------


class DocumentResponse(BaseModel):
    """单个文档的响应模型"""

    id: str
    filename: str
    file_type: str
    file_size: Optional[int] = None
    status: str
    error_message: Optional[str] = None
    created_at: str
    updated_at: str
    meta_data: Optional[Dict[str, Any]] = None
    chunks_count: int = 0


class DocumentsListResponse(BaseModel):
    """文档列表响应"""

    total: int
    documents: List[DocumentResponse]


# ----------------------------------------------------------------
# 后台任务包装）
# ----------------------------------------------------------------


async def _process_in_background(document_id: str, file_path: str, filename: str) -> None:
    """
    后台任务入口（异步版本）

    注意：用 asyncio.create_task 在同一个事件循环中执行，
    不能用 asyncio.run()（会新建事件循环，跟 uvicorn 冲突）。
    """
    import asyncio

    logger.info(f"后台任务启动: {filename} ({document_id})")
    try:
        # 创建异步任务，不阻塞请求响应
        task = asyncio.create_task(process_document(document_id, file_path, filename))
        # 等待完成（实际上这里可以直接 await）
        result = await task
        logger.info(f"后台任务完成: {filename} → {result}")
    except Exception:
        logger.error(f"后台任务失败: {filename}", exc_info=True)


# ----------------------------------------------------------------
# 端点实现
# ----------------------------------------------------------------


@router.post("/upload", response_model=Dict[str, Any])
async def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
):
    """
    上传文档 + 触发后台处理

    流程：
      1. 保存文件到 data/papers/
      2. 在 PostgreSQL 插入 Document 记录（status=uploaded）
      3. 加入后台任务队列（process_document）
    """
    # 校验文件类型
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".pdf", ".txt", ".md"):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{suffix}（仅支持 PDF/TXT/MD）")

    # 1. 保存文件
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{file_id}{suffix}"

    try:
        # 先把 UploadFile 的内容写到磁盘
        with open(save_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败：{e}") from e

    file_size = os.path.getsize(save_path)

    # 2. 插入 PostgreSQL
    document_id = str(uuid.uuid4())
    doc = Document(
        id=document_id,
        filename=file.filename,
        file_type=suffix.lstrip("."),
        file_size=file_size,
        status=DocumentStatus.UPLOADED,
        meta_data={"saved_as": f"{file_id}{suffix}", "upload_path": str(save_path)},
    )

    async with AsyncSessionLocal() as session:
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

    logger.info(f"文档已入库: {doc.filename} ({doc.id}, {file_size} bytes)")

    # 3. 加入后台任务
    background_tasks.add_task(_process_in_background, document_id, str(save_path), file.filename)

    return {
        "ok": True,
        "document_id": document_id,
        "filename": file.filename,
        "file_size": file_size,
        "status": DocumentStatus.UPLOADED,
        "message": "文件已上传，正在后台处理（约 1-2 分钟）",
    }


@router.get("", response_model=DocumentsListResponse)
async def list_documents(skip: int = 0, limit: int = 20):
    """列出所有文档（分页）"""
    async with AsyncSessionLocal() as session:
        # 查总数
        from sqlalchemy import func
        from app.models.document import Chunk

        total_result = await session.execute(select(func.count(Document.id)))
        total = total_result.scalar()

        # 分页查询（带 chunks_count）
        docs_result = await session.execute(
            select(Document).order_by(Document.created_at.desc()).offset(skip).limit(limit)
        )
        docs = docs_result.scalars().all()

        # 批量查每个文档的 chunks 数量
        chunk_counts = {}
        if docs:
            doc_ids = [str(d.id) for d in docs]
            counts_result = await session.execute(
                select(Chunk.document_id, func.count(Chunk.id))
                .where(Chunk.document_id.in_(doc_ids))
                .group_by(Chunk.document_id)
            )
            for doc_id, count in counts_result:
                chunk_counts[str(doc_id)] = count

    return {
        "total": total,
        "documents": [
            DocumentResponse(
                id=str(d.id),
                filename=d.filename,
                file_type=d.file_type,
                file_size=d.file_size,
                status=d.status,
                error_message=d.error_message,
                created_at=d.created_at.isoformat() if d.created_at else "",
                updated_at=d.updated_at.isoformat() if d.updated_at else "",
                meta_data=d.meta_data,
                chunks_count=chunk_counts.get(str(d.id), 0),
            )
            for d in docs
        ],
    }


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str):
    """查看单个文档详情"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    return DocumentResponse(
        id=str(doc.id),
        filename=doc.filename,
        file_type=doc.file_type,
        file_size=doc.file_size,
        status=doc.status,
        error_message=doc.error_message,
        created_at=doc.created_at.isoformat() if doc.created_at else "",
        updated_at=doc.updated_at.isoformat() if doc.updated_at else "",
        meta_data=doc.meta_data,
    )


@router.delete("/{document_id}")
async def delete_document(document_id: str):
    """
    删除文档（PostgreSQL + OpenSearch 级联删除）

    步骤：
      1. 查文档是否存在
      2. 删 PostgreSQL 的 Document + Chunks（CASCADE）
      3. 删 OpenSearch 里对应的 chunks
      4. 删磁盘文件
    """
    from app.services.opensearch import opensearch_service

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()

        if not doc:
            raise HTTPException(status_code=404, detail="文档不存在")

        # 1. 删 PostgreSQL（Chunks 会 CASCADE 自动删）
        await session.delete(doc)
        await session.commit()
        logger.info(f"PostgreSQL 删除: {doc.filename} ({document_id})")

    # 2. 删 OpenSearch
    try:
        os_result = await opensearch_service.delete_by_document(document_id)
        logger.info(f"OpenSearch 删除: {document_id} → {os_result}")
    except Exception as e:
        logger.warning(f"OpenSearch 删除失败（可忽略）: {e}")

    # 3. 删磁盘文件
    meta_data = doc.meta_data or {}
    saved_as = meta_data.get("saved_as")
    if saved_as:
        file_path = UPLOAD_DIR / saved_as
        if file_path.exists():
            file_path.unlink()
            logger.info(f"磁盘文件已删: {file_path}")

    return {"ok": True, "deleted": document_id}
