"""
文档管理路由（Phase 6 实现）

文档上传、列表、删除
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.document import Document

logger = logging.getLogger(__name__)
router = APIRouter()


class DocumentResponse(BaseModel):
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
    total: int
    documents: List[DocumentResponse]


@router.get("", response_model=DocumentsListResponse)
async def list_documents(skip: int = 0, limit: int = 20):
    """列出已上传的文档（从数据库查询）"""
    async with AsyncSessionLocal() as session:
        from sqlalchemy import func

        total_result = await session.execute(select(func.count(Document.id)))
        total = total_result.scalar()

        docs_result = await session.execute(
            select(Document).order_by(Document.created_at.desc()).offset(skip).limit(limit)
        )
        docs = docs_result.scalars().all()

    return {
        "total": total,
        "documents": [
            DocumentResponse(
                id=str(d.id),
                filename=d.filename,
                file_type=getattr(d, 'file_type', 'pdf'),
                file_size=d.file_size,
                status=d.status,
                error_message=d.error_message,
                created_at=d.created_at.isoformat() if d.created_at else "",
                updated_at=d.updated_at.isoformat() if d.updated_at else "",
                meta_data=d.meta_data,
            )
            for d in docs
        ],
    }


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str):
    """获取单个文档详情"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    return DocumentResponse(
        id=str(doc.id),
        filename=doc.filename,
        file_type=getattr(doc, 'file_type', 'pdf'),
        file_size=doc.file_size,
        status=doc.status,
        error_message=doc.error_message,
        created_at=doc.created_at.isoformat() if doc.created_at else "",
        updated_at=doc.updated_at.isoformat() if doc.updated_at else "",
        meta_data=doc.meta_data,
    )


@router.delete("/{document_id}")
async def delete_document(document_id: str):
    """删除文档及其所有分块"""
    import os
    from pathlib import Path

    from app.core.config import settings
    from app.models.document import Chunk

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()

        if not doc:
            raise HTTPException(status_code=404, detail="文档不存在")

        # 删除关联的 chunks
        await session.execute(
            select(Chunk).where(Chunk.document_id == document_id)
        )
        chunks_result = await session.execute(
            select(Chunk).where(Chunk.document_id == document_id)
        )
        for chunk in chunks_result.scalars().all():
            await session.delete(chunk)

        await session.delete(doc)
        await session.commit()
        logger.info(f"文档已删除: {doc.filename} ({document_id})")

        # 删除磁盘文件
        meta_data = doc.meta_data or {}
        saved_as = meta_data.get("saved_as")
        if saved_as:
            file_path = Path(settings.UPLOAD_DIR) / saved_as
            if file_path.exists():
                file_path.unlink()
                logger.info(f"磁盘文件已删: {file_path}")

    return {"ok": True, "deleted": document_id}
