"""
文档管理路由（Phase 6 实现）

文档上传、列表、删除
"""

from fastapi import APIRouter, UploadFile, File

router = APIRouter()


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    上传文档（PDF/Word/TXT）
    
    上传后异步处理：
    1. 文档解析
    2. 文本分块
    3. 生成嵌入向量
    4. 写入 OpenSearch
    """
    return {
        "filename": file.filename,
        "status": "uploaded",
        "document_id": "TODO",
        "message": "Phase 6 实现文档处理流水线",
    }


@router.get("")
async def list_documents():
    """列出已上传的文档"""
    return {
        "documents": [],
        "total": 0,
    }


@router.delete("/{document_id}")
async def delete_document(document_id: str):
    """删除文档及其所有分块"""
    return {
        "document_id": document_id,
        "status": "deleted",
    }
