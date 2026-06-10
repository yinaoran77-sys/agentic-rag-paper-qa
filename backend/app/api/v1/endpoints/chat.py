"""
问答路由（Phase 7 升级版 - 接入语义缓存）

新增功能：
- 问答前先查 Redis 语义缓存（命中则直接返回，~4ms）
- 问答后写入缓存（下次相似问题命中）
- 支持关闭缓存（请求参数里传 use_cache=false）
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services.agent import agent_service
from app.services.cache import semantic_cache

logger = logging.getLogger(__name__)
router = APIRouter()


# ================================================================
# 请求/响应模型
# ================================================================


class ChatRequest(BaseModel):
    """问答请求体"""

    message: str
    session_id: Optional[str] = None
    mode: str = "agentic"  # "agentic" or "standard"
    top_k: int = 5
    use_cache: bool = True  # 是否使用语义缓存


class ChatResponse(BaseModel):
    """问答响应体"""

    answer: str
    sources: List[Dict[str, Any]]
    mode: str
    session_id: Optional[str] = None
    tokens_usage: Dict[str, Any] = {}
    from_cache: bool = False  # 是否来自缓存
    similarity: Optional[float] = None  # 缓存命中时的相似度


# ================================================================
# 核心端点
# ================================================================


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """
    发送消息并获取 AI 回答（带语义缓存）

    流程（大白话）：
    1. 如果开了缓存 → 先去 Redis 查有没有相似问题
    2. 缓存命中 → 直接返回（飞快，~4ms）
    3. 缓存未命中 → 正常跑 RAG/Agent → 把结果写进缓存
    """
    # Step 1：查缓存
    if req.use_cache and semantic_cache.enabled:
        cached = await semantic_cache.get(req.message)
        if cached:
            return ChatResponse(
                answer=cached["answer"],
                sources=cached.get("sources", []),
                mode=cached.get("mode", req.mode),
                session_id=req.session_id,
                from_cache=True,
                similarity=cached.get("similarity"),
            )

    # Step 2：缓存未命中，正常处理
    try:
        if req.mode == "standard":
            result = await agent_service.standard_rag(req.message, top_k=req.top_k)
        else:
            result = await agent_service.chat(
                message=req.message,
                session_id=req.session_id,
            )

        answer = result["answer"]
        sources = result["sources"]
        mode = result["mode"]

        # Step 3：写入缓存（过滤掉错误答案）
        if req.use_cache and semantic_cache.enabled:
            # 检查答案是否包含错误信息，避免缓存报错
            error_keywords = ["llama-server", "process has terminated", "signal: killed",
                              "Connection refused", "timeout", "HTTP Error"]
            is_error = any(kw in answer for kw in error_keywords)
            if not is_error:
                await semantic_cache.set(
                    question=req.message,
                    answer=answer,
                    sources=sources,
                    mode=mode,
                )
            else:
                logger.warning(f"答案包含错误信息，跳过缓存: {answer[:100]}")

        return ChatResponse(
            answer=answer,
            sources=sources,
            mode=mode,
            session_id=req.session_id,
            from_cache=False,
        )

    except Exception as e:
        logger.error(f"问答失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"问答处理失败：{e}")


@router.post("/standard", response_model=ChatResponse)
async def chat_standard(req: ChatRequest) -> ChatResponse:
    """标准 RAG 模式（专用端点）"""
    req.mode = "standard"  # 强制 standard 模式
    # 启用缓存（避免每次都调 LLM）
    return await chat(req)


@router.post("/{session_id}", response_model=ChatResponse)
async def chat_with_session(
    session_id: str,
    req: ChatRequest,
) -> ChatResponse:
    """基于历史会话继续对话"""
    req.session_id = session_id
    return await chat(req)


# ================================================================
# 缓存管理端点（Phase 7 新增）
# ================================================================


@router.get("/cache/stats")
async def get_cache_stats():
    """查看缓存统计"""
    return await semantic_cache.stats()


@router.delete("/cache/clear")
async def clear_cache(question: Optional[str] = None):
    """
    清空缓存

    参数（Query）：
      question: 可选，指定要删除的问题（MD5 匹配）
                 如果不传 → 清空所有语义缓存
    """
    deleted = await semantic_cache.clear(question)
    return {"deleted": deleted, "message": "缓存已清理" if not question else f"已删除：{question}"}
