"""
会话与消息模型

为什么要把"对话"拆成两张表？
- sessions：一次完整对话的"摘要"（标题、创建时间）
- messages：对话的每一条记录（用户问了啥、AI 答了啥）

就像微信聊天：
- 聊天列表页 → sessions 表
- 点进去的具体对话 → messages 表
"""

import uuid
from datetime import datetime, timezone

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
# 会话表（sessions）
# ============================================================
class Session(Base):
    """
    存储每次对话的上下文
    
    对应物理世界：微信的"聊天列表"
    - 每次打开网页开始聊天，创建一条 session
    - 可以给它起名字（"关于 RAG 的提问"、"OpenSearch 配置问题"）
    """
    
    __tablename__ = "sessions"
    
    # ---- 主键 ----
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # ---- 基本信息 ----
    title = Column(
        String(255),
        nullable=True,
        comment="会话标题（可以从第一条消息自动生成）",
    )
    
    # ---- 扩展元数据 ----
    # 例如：{"user_agent": "Mozilla/5.0", "ip": "1.2.3.4"}
    meta_data = Column(JSON, nullable=True, comment="会话额外信息")
    
    # ---- 时间戳 ----
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
    
    # ---- 关系 ----
    # 一个会话有多条消息（1:N 关系）
    # cascade="all, delete-orphan"：删掉会话，下面的消息全部自动删除
    messages = relationship(
        "Message",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    
    def __repr__(self) -> str:
        return f"<Session '{self.title or self.id}'>"


# ============================================================
# 消息表（messages）
# ============================================================
class Message(Base):
    """
    存储每轮问答记录
    
    对应物理世界：微信聊天窗口里的每条消息
    - role="user"：用户发的
    - role="assistant"：AI 回答的
    - sources：AI 回答时参考了哪些文档片段（可溯源，防止 AI 瞎编）
    """
    
    __tablename__ = "messages"
    
    # ---- 主键 ----
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # ---- 外键 ----
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # 按会话查消息是最高频操作，必须加索引
        comment="所属会话 ID",
    )
    
    # ---- 消息内容 ----
    # role 只有两种值：user / assistant
    # 为什么不用 Enum？PostgreSQL 的 CHECK 约束更靠谱（数据库层校验）
    role = Column(
        String(20),
        nullable=False,
        comment="角色：user（用户）或 assistant（AI）",
    )
    content = Column(Text, nullable=False, comment="消息内容")
    
    # ---- AI 回答的附加信息 ----
    # sources：AI 回答时参考了哪些文档片段
    # 例如：[{"document_id": "xxx", "chunk_id": "yyy", "score": 0.95}]
    # 作用：用户问"你说的依据是什么？" → 直接返回 sources
    sources = Column(JSON, nullable=True, comment="参考来源（文档片段列表）")
    
    # latency_ms：这次回答花了多久（毫秒）
    # 用途：监控 AI 性能，发现慢查询
    latency_ms = Column(Integer, nullable=True, comment="响应耗时（毫秒）")
    
    # ---- 扩展元数据 ----
    # 例如：{"model": "qwen2.5:7b", "tokens": 1234, "cost": 0.005}
    meta_data = Column(JSON, nullable=True, comment="额外信息（模型、Token 数等）")
    
    # ---- 时间戳 ----
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,  # 按时间排序展示消息，加索引加速
    )
    
    # ---- 关系 ----
    session = relationship("Session", back_populates="messages")
    
    def __repr__(self) -> str:
        # 只显示前 30 个字，避免日志太长
        preview = self.content[:30] + "..." if len(self.content) > 30 else self.content
        return f"<Message {self.role}: {preview}>"
