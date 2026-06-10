"""
LangGraph Agent 服务（Phase 5 核心 - 修复版）

这个文件实现了 Agentic RAG 的智能体（Agent）：
- 用 LangGraph 构建多轮对话 Agent
- Agent 可以自主决定要不要检索文档（tool calling）
- 支持两种模式：standard RAG（直接检索）和 agentic（多轮推理）

为什么用 LangGraph 而不是直接调 LLM？
普通 RAG：用户问 → 搜文档 → 回答（固定流程）
Agentic RAG：用户问 → Agent 思考 → 决定搜不搜、怎么搜 → 回答（灵活流程）

核心概念（大白话）：
- State【状态】：Agent 的"工作记忆"，每次对话更新一次
- Node【节点】：一个处理步骤（比如"让 LLM 思考"、"调用搜索工具"）
- Edge【边】：节点之间的跳转规则（比如"如果需要搜索，跳到搜索节点"）
- Tool【工具】：Agent 可以调用的外部函数（比如 search_documents）
"""

import logging
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langgraph.graph import END, StateGraph
from typing_extensions import Annotated, TypedDict

from app.core.config import settings
from app.services.opensearch import opensearch_service
from app.services.retrieval import retrieval_service

logger = logging.getLogger(__name__)

# ================================================================
# 1. 定义 Agent 的状态（State）
# ================================================================


class AgentState(TypedDict):
    """
    Agent 的状态（工作记忆）

    每次 Agent 处理一轮对话，这个状态就会更新一次。
    它记录了：
    - messages：对话历史（用户说了什么、Agent 回了什么）
    - sources：引用来源（哪篇论文的哪一段）
    - current_mode：当前是 standard 还是 agentic 模式
    """
    messages: Annotated[List[BaseMessage], "对话消息列表"]
    sources: Annotated[List[Dict[str, Any]], "引用来源"]
    current_mode: Annotated[str, "当前模式"]


# ================================================================
# 2. 定义 Agent 的工具（Tools）
# ================================================================


@tool
async def search_documents(query: str, mode: Literal["bm25", "vector", "hybrid", "rrf"] = "hybrid", top_k: int = 5) -> str:
    """
    搜索已上传论文的相关段落。

    当用户问题中需要查询论文内容时使用这个工具。
    支持四种检索模式：
    - vector：语义检索（找意思相近的）
    - bm25：关键词检索（找字面匹配的）
    - hybrid：混合检索（两者结合，推荐）
    - rrf：倒数排序融合（不依赖分数，更公平）

    参数：
      query: 搜索关键词或问题
      mode: 检索模式（默认 hybrid）
      top_k: 返回几个结果（默认 5）

    返回：
      找到的相关文本段落，用分隔符分开
    """
    try:
        client = await opensearch_service.get_client()

        # 根据 mode 调用不同的检索方法
        if mode == "vector":
            results = await retrieval_service.dense_search(client, query, settings.OPENSEARCH_INDEX, top_k=top_k)
        elif mode == "bm25":
            results = await retrieval_service.sparse_search(client, query, settings.OPENSEARCH_INDEX, top_k=top_k)
        elif mode == "rrf":
            results = await retrieval_service.rrf_search(client, query, settings.OPENSEARCH_INDEX, top_k=top_k)
        else:  # hybrid
            results = await retrieval_service.hybrid_search(client, query, settings.OPENSEARCH_INDEX, top_k=top_k)

        if not results:
            return "未找到相关文档内容，请先上传论文。"

        parts = []
        for i, r in enumerate(results, 1):
            content = r.get("content", "")
            score = r.get("score", 0)
            parts.append(f"[结果 {i}，相关度 {score:.3f}]\n{content[:500]}")

        return "\n\n---\n\n".join(parts)

    except Exception as e:
        logger.error(f"搜索工具调用失败: {e}", exc_info=True)
        return f"搜索失败：{e}"


@tool
async def get_document_summary(document_id: str) -> str:
    """
    获取某篇论文的摘要信息。

    参数：
      document_id: 论文的 ID

    返回：
      论文的基本信息和前几段内容
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.document import Chunk, Document

    try:
        async with AsyncSessionLocal() as session:
            doc = await session.get(Document, document_id)
            if not doc:
                return f"找不到 ID 为 {document_id} 的论文"

            result = await session.execute(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index).limit(3)
            )
            chunks = result.scalars().all()
            preview = "\n".join([c.content[:200] for c in chunks])
            return f"论文: {doc.filename}\n上传时间: {doc.created_at}\n内容预览:\n{preview}"

    except Exception as e:
        logger.error(f"获取摘要失败: {e}", exc_info=True)
        return f"获取摘要失败：{e}"


# 工具列表（注册给 Agent）
TOOLS = [search_documents, get_document_summary]

# ================================================================
# 3. 构建 LangGraph 图
# ================================================================


def _build_agent_graph():
    """
    构建 LangGraph 的 Agent 图（状态流转图）

    流程图（大白话）：
    1. agent 节点：LLM 看用户问题，决定是直接回答还是调用工具
    2. 条件边：如果 LLM 调用了工具 → 跳到 tools 节点
                 如果 LLM 直接回答了 → 跳到 END（结束）
    3. tools 节点：执行工具（比如搜索文档），把结果还给 LLM
    4. 回到步骤 1（循环，直到 LLM 给出最终答案）
    """
    from langgraph.prebuilt import ToolNode

    # 初始化 LLM（大语言模型）
    llm = ChatOllama(
        model=settings.OLLAMA_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0.1,
    )
    llm_with_tools = llm.bind_tools(TOOLS)

    # 构建图
    workflow = StateGraph(AgentState)

    # ---- 节点 1：Agent（LLM 思考）----
    async def agent_node(state: AgentState) -> Dict:
        """让 LLM 看当前对话历史，决定下一步做什么"""
        messages = state["messages"]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    # ---- 节点 2：Tools（执行工具调用）----
    tool_node = ToolNode(TOOLS)

    # 添加节点
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    # ---- 边（跳转规则）----
    def should_continue(state: AgentState) -> str:
        """
        判断下一步去哪：
        - 如果最后一条消息有 tool_calls（调用工具的请求）→ 去 "tools"
        - 否则 → 去 END（结束）
        """
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")

    return workflow.compile()


# ================================================================
# 4. Agent 服务类（对外接口）
# ================================================================


class AgentService:
    """
    Agent 服务（单例模式）

    对外提供两个核心方法：
    1. chat()：Agentic 模式（Agent 自主决定是否检索）
    2. standard_rag()：标准 RAG 模式（强制检索 → 回答）
    """

    def __init__(self):
        self.llm = ChatOllama(
            model=settings.OLLAMA_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=0.1,
        )
        self.agent_graph = _build_agent_graph()

    async def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        history: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """
        Agentic 模式：多轮对话，Agent 自主决定检索策略
        """
        messages: List[BaseMessage] = [
            SystemMessage(
                content=(
                    "你是一个专业的论文问答助手。"
                    "请根据用户的问题，使用提供的工具搜索相关论文内容，并给出准确的回答。"
                    "如果搜索结果不足以回答问题，请明确说明。"
                    "回答时请引用具体的论文内容，并注明来源。"
                )
            )
        ]

        if history:
            for msg in history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))

        messages.append(HumanMessage(content=message))

        initial_state = AgentState(
            messages=messages,
            sources=[],
            current_mode="agentic",
        )

        try:
            result = await self.agent_graph.ainvoke(initial_state)

            final_messages = result["messages"]
            answer = ""
            sources = []

            for msg in final_messages:
                if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                    answer = msg.content
                if isinstance(msg, ToolMessage) and "结果" in msg.content:
                    sources.append({"content": msg.content[:300], "type": "tool_result"})

            return {
                "answer": answer,
                "sources": sources,
                "mode": "agentic",
                "tokens_usage": {},
            }

        except Exception as e:
            logger.error(f"Agent 对话失败: {e}", exc_info=True)
            return {
                "answer": f"抱歉，Agent 处理失败：{e}",
                "sources": [],
                "mode": "agentic",
                "error": str(e),
            }

    async def standard_rag(
        self,
        message: str,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        标准 RAG 模式：固定流程，直接检索 → 回答
        """
        client = await opensearch_service.get_client()
        results = await retrieval_service.hybrid_search(
            client, message, settings.OPENSEARCH_INDEX, top_k=top_k
        )

        if not results:
            return {
                "answer": "未找到相关论文内容，请先上传论文文档。",
                "sources": [],
                "mode": "standard",
            }

        context_parts = []
        sources = []
        for i, r in enumerate(results, 1):
            content = r.get("content", "")
            score = r.get("score", 0)
            context_parts.append(f"[来源 {i}，相关度 {score:.3f}]\n{content}")
            sources.append({
                "content": content,
                "score": score,
                "document_id": r.get("document_id", ""),
                "chunk_index": r.get("chunk_index", 0),
            })

        context = "\n\n---\n\n".join(context_parts)

        prompt = f"""请根据以下参考材料回答问题。如果材料中没有相关信息，请明确说明。

## 参考材料：
{context}

## 用户问题：
{message}

## 回答要求：
1. 基于参考材料回答，不要编造信息
2. 如果引用了某段材料，请注明"来源 X"
3. 语言简洁准确，适合学术场景
"""

        try:
            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            answer = response.content

            return {
                "answer": answer,
                "sources": sources,
                "mode": "standard",
            }

        except Exception as e:
            logger.error(f"标准 RAG 回答失败: {e}", exc_info=True)
            return {
                "answer": f"抱歉，处理失败：{e}",
                "sources": sources,
                "mode": "standard",
                "error": str(e),
            }


# ----------------------------------------------------------------
# 全局服务单例
# ----------------------------------------------------------------
agent_service = AgentService()
