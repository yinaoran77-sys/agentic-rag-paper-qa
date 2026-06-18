"""
文档与文本块模型

为什么要把论文切成 chunks（文本块）？
- LLM 有上下文窗口限制（就像你一次最多能读多少字）
- 切小块后可以做精准检索（只喂给 AI 最相关的段落）
- chunk_size=512 是经验值（约 400 个中文字）
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


# ============================================================
# 文档状态枚举
# ============================================================
class DocumentStatus(str, Enum):
    """文档处理状态"""

    UPLOADED = "uploaded"    # 已上传
    PROCESSING = "processing"   # 处理中
    COMPLETED = "completed"    # 处理完成
    FAILED = "failed"          # 处理失败


# ============================================================
# 文档表（documents）
# ============================================================
class Document(Base):
    """
    存储上传的论文元数据
    
    对应物理世界：图书馆的"书目登记表"
    - 记录书名（filename）、类型（file_type）、登记时间（created_at）
    - 不包含文件内容（内容存在 OpenSearch 里，做向量检索）
    """
    
    __tablename__ = "documents"
    
    # ---- 主键 ----
    # UUID vs 自增 ID：
    # - UUID 更安全（不能猜到别人的 document_id）
    # - 分布式环境不会冲突
    # - PostgreSQL 原生支持 UUID 类型，性能很好
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # ---- 基本信息 ----
    filename = Column(String(255), nullable=False, comment="原始文件名")
    file_type = Column(String(10), nullable=False, comment="文件类型：pdf/docx/txt")
    file_size = Column(Integer, nullable=True, comment="文件大小（字节）")
    
    # ---- 处理状态 ----
    # 为什么需要状态机？
    # 文档上传 → 解析中 → 切片中 → 向量化中 → 完成
    #                             ↓
    #                          解析失败（要记录原因）
    status = Column(
        String(20),
        nullable=False,
        default="uploaded",
        comment="处理状态：uploaded/parsing/chunking/embedding/ready/error",
    )
    error_message = Column(Text, nullable=True, comment="失败时的错误信息")
    
    # ---- 扩展元数据 ----
    # JSON 类型：存任意结构化数据
    # 例如：{"author": "张三", "year": 2024, "keywords": ["RAG", "LLM"]}
    meta_data = Column(JSON, nullable=True, comment="额外元数据（作者、年份等）")
    
    # ---- 时间戳 ----
    # timezone.utc：统一用 UTC 时间存储，展示时再转本地时区
    # 为什么？避免时区混乱（用户在中国，服务器可能在国外）
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    
    # ---- 关系（Relationships）----
    # back_populates：SQLAlchemy 的"双向导航"
    # - doc.chunks          → 拿到这篇文档的所有文本块
    # - chunk.document      → 拿到这个文本块属于哪篇文档
    # cascade="all, delete-orphan"：
    #   删掉 Document，它下面的所有 Chunk 自动被删掉（数据库里的"连坐"）
    chunks = relationship(
        "Chunk",
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="selectin",  # 避免 N+1 查询问题
    )
    
    def __repr__(self) -> str:
        return f"<Document {self.filename} ({self.status})>"


# ============================================================
# 文本块表（chunks）
# ============================================================
class Chunk(Base):
    """
    存储论文切分后的文本块
    
    对应物理世界：图书馆的"内容索引卡"
    - 一篇 10 页的论文切成 50 张卡片
    - 每张卡片有一段文字 + 对应的向量 ID（存在 OpenSearch 里）
    - 用户提问时，系统找到最相关的 5 张卡片，喂给 AI
    """
    
    __tablename__ = "chunks"
    
    # ---- 主键 ----
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # ---- 外键 ----
    # ForeignKey：建立"父子关系"的数据库约束
    # - ondelete="CASCADE"：爸爸没了，儿子自动被删掉
    # - 数据库层面保证数据一致性（比应用层更可靠）
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # 加索引！按 document_id 查询会很频繁
        comment="所属文档 ID",
    )
    
    # ---- 文本内容 ----
    content = Column(Text, nullable=False, comment="文本块内容")
    chunk_index = Column(
        Integer,
        nullable=False,
        comment="在文档中的顺序（第几个块）",
    )
    
    # ---- OpenSearch 关联 ----
    # OpenSearch 里存的是向量（embedding）
    # 这个字段存 OpenSearch 返回的文档 ID
    # 这样就能：PostgreSQL 查元数据 → 拿 opensearch_doc_id → OpenSearch 查向量
    opensearch_doc_id = Column(
        String(255),
        nullable=True,
        comment="OpenSearch 中的文档 ID（向量检索用）",
    )
    
    # ---- 扩展元数据 ----
    # 例如：{"page_number": 3, "section": "第二章"}
    meta_data = Column(JSON, nullable=True, comment="额外元数据（页码、章节等）")
    
    # ---- 时间戳 ----
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    
    # ---- 关系 ----
    document = relationship("Document", back_populates="chunks")
    
    def __repr__(self) -> str:
        return f"<Chunk #{self.chunk_index} of {self.document_id}>"
