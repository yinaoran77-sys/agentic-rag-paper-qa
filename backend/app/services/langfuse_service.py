"""
Langfuse 可观测服务（Phase 7）

Langfuse 是什么？
→ 一个开源的 LLM 可观测平台（类似 LangSmith）
→ 能记录每一次 LLM 调用、工具调用、检索结果
→ 有 Web UI（docker-compose 里已经配了，端口 3000）

为什么要接入？
1. 调试方便：看到每轮对话 LLM 收到了什么、回了什么
2. 性能分析：哪一步慢（检索？LLM？）、token 用了多少
3. 质量分析：哪个问题答得好、哪个答得差（可以标注）
4. 成本追踪：token 用量 → 算钱

接入方式（大白话）：
- 用 langfuse Python SDK 的 CallbackHandler
- 把它传给 LangChain/LangGraph 的每次调用
- Langfuse 自动记录所有 LLM 交互
"""

import logging
import os
from typing import Any, Dict, Optional

from langfuse import Langfuse

from app.core.config import settings

logger = logging.getLogger(__name__)

# Langfuse 支持的环境变量（优先级最高）：
#   LANGFUSE_PUBLIC_KEY=pk-lf-xxx
#   LANGFUSE_SECRET_KEY=sk-lf-xxx
#   LANGFUSE_HOST=http://localhost:3000


class LangfuseService:
    """
    Langfuse 服务（单例）

    核心功能：
    1. 初始化 Langfuse 客户端（应用启动时调用）
    2. 提供 CallbackHandler（传给 LangChain/LangGraph）
    3. 手动记录事件（可选，高级用法）

    如果 Langfuse 没配置（没填 key）→ 自动禁用，不影响主流程
    """

    def __init__(self):
        self.enabled = False
        self._client: Optional[Langfuse] = None

    def init(self) -> None:
        """
        初始化 Langfuse 客户端

        在 FastAPI 的 lifespan startup里调用。
        如果环境变量没配或用了占位 key → 自动禁用，打警告日志。
        """
        secret_key = settings.LANGFUSE_SECRET_KEY
        public_key = settings.LANGFUSE_PUBLIC_KEY
        host = settings.LANGFUSE_HOST

        # 检查是否是占位 key（未真实配置）
        if not secret_key or not public_key or "xxxx" in secret_key:
            logger.info("ℹ️  Langfuse 未配置（占位 key），可观测功能停用")
            self.enabled = False
            return

        try:
            self._client = Langfuse(
                secret_key=secret_key,
                public_key=public_key,
                host=host,
            )
            self.enabled = True
            logger.info(f"✅ Langfuse 客户端已初始化（host={host}）")

        except Exception as e:
            logger.warning(f"⚠️  Langfuse 初始化失败：{e}，可观测功能停用")
            self.enabled = False
            self._client = None

    def get_callback_handler(self, session_id: Optional[str] = None):
        """
        获取 LangChain CallbackHandler

        用法（大白话）：
        ```python
        handler = langfuse_service.get_callback_handler(session_id)
        response = await llm.ainvoke(messages, config={"callbacks": [handler]})
        ```

        返回的 handler 会自动：
        - 记录 LLM 输入/输出
        - 记录 token 用量
        - 记录耗时
        - 关联到 session_id（方便按会话查看）
        """
        if not self.enabled or self._client is None:
            return None

        try:
            from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

            handler = LangfuseCallbackHandler(
                secret_key=settings.LANGFUSE_SECRET_KEY,
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                host=settings.LANGFUSE_HOST,
                session_id=session_id or "default",
                trace_name="agentic-rag-chat",
                tags=["agentic-rag", settings.APP_VERSION],
            )
            return handler
        except Exception as e:
            logger.error(f"创建 Langfuse CallbackHandler 失败: {e}")
            return None

    async def trace_chat(
        self,
        session_id: str,
        question: str,
        answer: str,
        sources: list,
        mode: str,
        tokens_usage: Dict,
    ) -> None:
        """
        手动记录一次聊天（如果不通过 LangChain Callback 的话）

        参数：
          session_id: 会话 ID
          question: 用户问题
          answer: AI 回答
          sources: 引用来源
          mode: "standard" or "agentic"
          tokens_usage: token 用量
        """
        if not self.enabled or self._client is None:
            return

        try:
            self._client.trace(
                name="chat",
                session_id=session_id,
                input={ "question": question, "mode": mode},
                output={"answer": answer, "sources_count": len(sources)},
                metadata={
                    "tokens": tokens_usage,
                    "sources_count": len(sources),
                    "mode": mode,
                },
            )
        except Exception as e:
            logger.error(f"Langfuse trace 写入失败: {e}")

    def shutdown(self) -> None:
        """关闭 Langfuse 客户端（应用 shutdown 时调用）"""
        if self._client:
            try:
                self._client.flush()
                logger.info("✅ Langfuse 数据已 flush")
            except Exception as e:
                logger.warning(f"Langfuse flush 失败: {e}")
            self._client = None


# ----------------------------------------------------------------
# 全局服务单例
# ----------------------------------------------------------------
langfuse_service = LangfuseService()
