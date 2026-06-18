"""
模型统一导出

为什么要有这个文件？
- 其他地方想用模型时，不用关心文件结构
- 统一入口：from app.models import Document, Chunk, Session, Message
- Alembic 迁移工具需要扫描所有模型，从这个文件导入最方便
"""

from app.models.document import Chunk, Document
from app.models.session import Message, Session

__all__ = [
    "Document",
    "Chunk",
    "Session",
    "Message",
]
