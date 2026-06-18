"""
LangGraph Agent service (Phase 5 core - fixed v3)
"""

import logging
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from typing_extensions import Annotated, TypedDict
import uuid as uuid_mod

from app.core.config import settings
from app.services.retrieval import retrieval_service

logger = logging.getLogger(__name__)

_opensearch_service = None
_opensearch_available = False


def _get_opensearch_service():
    global _opensearch_service, _opensearch_available
    if _opensearch_service is None:
        try:
            from app.services.opensearch import opensearch_service as os_svc
            _opensearch_service = os_svc
            _opensearch_available = True
        except Exception as e:
            logger.warning("OpenSearch unavailable: %s", e)
            _opensearch_available = False
    return _opensearch_service


def _get_llm():
    """根据配置返回对应的 LLM 实例（支持 ollama / dashscope / openai）"""
    provider = settings.LLM_PROVIDER.lower()

    if provider == "dashscope":
        if not settings.DASHSCOPE_API_KEY:
            raise ValueError("DASHSCOPE_API_KEY 未配置，请在 .env 文件中设置")
        return ChatOpenAI(
            model=settings.DASHSCOPE_MODEL,
            base_url=settings.DASHSCOPE_BASE_URL,
            api_key=settings.DASHSCOPE_API_KEY,
            temperature=0.1,
        )

    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY 未配置，请在 .env 文件中设置")
        return ChatOpenAI(
            model=settings.OPENAI_MODEL,
            api_key=settings.OPENAI_API_KEY,
            temperature=0.1,
        )

    else:  # 默认 ollama
        return ChatOllama(
            model=settings.OLLAMA_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=0.1,
        )


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], "messages"]
    sources: Annotated[List[Dict[str, Any]], "sources"]
    current_mode: Annotated[str, "mode"]


@tool
async def search_documents(
    query: str,
    mode: Literal["bm25", "vector", "hybrid", "rrf"] = "hybrid",
    top_k: int = 5,
) -> str:
    """Search uploaded papers for relevant passages."""
    os_svc = _get_opensearch_service()
    if not _opensearch_available or os_svc is None:
        return await _search_documents_fallback(query, top_k)

    try:
        client = await os_svc.get_client()
        if mode == "vector":
            results = await retrieval_service.dense_search(client, query, settings.OPENSEARCH_INDEX, top_k=top_k)
        elif mode == "bm25":
            results = await retrieval_service.sparse_search(client, query, settings.OPENSEARCH_INDEX, top_k=top_k)
        elif mode == "rrf":
            results = await retrieval_service.rrf_search(client, query, settings.OPENSEARCH_INDEX, top_k=top_k)
        else:
            results = await retrieval_service.hybrid_search(client, query, settings.OPENSEARCH_INDEX, top_k=top_k)

        if not results:
            return "No relevant content found. Upload papers first."
        parts = []
        for i, r in enumerate(results, 1):
            content = r.get("content", "")
            score = r.get("score", 0)
            parts.append(f"[result {i}, score {score:.3f}]\n{content[:500]}")
        return "\n\n---\n\n".join(parts)

    except Exception as e:
        logger.error("Search failed: %s", e, exc_info=True)
        return f"Search error: {e}"


async def _search_documents_fallback(query: str, top_k: int = 5) -> str:
    from sqlalchemy import select, or_
    from app.core.database import AsyncSessionLocal
    from app.models.document import Chunk, Document

    try:
        async with AsyncSessionLocal() as session:
            keywords = [kw.strip() for kw in query.split() if len(kw.strip()) > 1]
            if not keywords:
                keywords = [query.strip()]
            conditions = [Chunk.content.ilike(f"%{kw}%") for kw in keywords]
            stmt = (
                select(Chunk, Document.filename)
                .join(Document, Chunk.document_id == Document.id)
                .where(or_(*conditions))
                .order_by(Chunk.chunk_index)
                .limit(top_k)
            )
            result = await session.execute(stmt)
            rows = result.all()
            if not rows:
                return "No relevant content found. Upload papers first."
            parts = []
            for i, (chunk, filename) in enumerate(rows, 1):
                parts.append(f"[result {i}, from {filename}]\n{chunk.content[:500]}")
            return "\n\n---\n\n".join(parts)

    except Exception as e:
        logger.error("PG fallback failed: %s", e, exc_info=True)
        return f"Search error (PG): {e}"


@tool
async def get_document_summary(document_id: str) -> str:
    """Get doc summary. Supports UUID or filename fuzzy match."""
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.document import Chunk, Document

    try:
        async with AsyncSessionLocal() as session:
            is_uuid = False
            try:
                uuid_obj = uuid_mod.UUID(document_id)
                is_uuid = True
            except (ValueError, AttributeError):
                pass

            if is_uuid:
                doc = await session.get(Document, uuid_obj)
            else:
                result = await session.execute(
                    select(Document).where(Document.filename.ilike(f"%{document_id}%")).limit(5)
                )
                docs = result.scalars().all()
                if len(docs) == 1:
                    doc = docs[0]
                elif len(docs) > 1:
                    names = [d.filename for d in docs]
                    return f"Multiple matches: {', '.join(names)}. Be more specific."
                else:
                    all_docs = await session.execute(select(Document.filename).limit(20))
                    all_names = [r[0] for r in all_docs.all()]
                    if all_names:
                        return f"No doc '{document_id}'. Uploaded: {', '.join(all_names)}"
                    return "No documents in DB. Please upload first."

            if not doc:
                return f"Document '{document_id}' not found"

            result = await session.execute(
                select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index).limit(5)
            )
            chunks = result.scalars().all()
            preview = "\n".join([f"[chunk {i+1}] {c.content[:200]}" for i, c in enumerate(chunks)])
            return f"Doc: {doc.filename}\nStatus: {doc.status}\nPreview:\n{preview}"

    except Exception as e:
        logger.error("Summary failed: %s", e, exc_info=True)
        return f"Summary error: {e}"


TOOLS = [search_documents, get_document_summary]


def _build_agent_graph():
    from langgraph.prebuilt import ToolNode

    llm = _get_llm()
    llm_with_tools = llm.bind_tools(TOOLS)

    workflow = StateGraph(AgentState)

    async def agent_node(state: AgentState) -> Dict:
        messages = state["messages"]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    tool_node = ToolNode(TOOLS)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")
    return workflow.compile()


class AgentService:

    def __init__(self):
        self.llm = _get_llm()
        self.agent_graph = _build_agent_graph()

    async def chat(self, message: str, session_id: Optional[str] = None, history: Optional[List[Dict]] = None) -> Dict[str, Any]:
        sys_msg = "You are a professional paper Q&A assistant. Use tools to search papers and answer accurately. Cite sources clearly."
        messages: List[BaseMessage] = [SystemMessage(content=sys_msg)]
        if history:
            for msg in history:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))
        messages.append(HumanMessage(content=message))

        initial_state = AgentState(messages=messages, sources=[], current_mode="agentic")

        try:
            result = await self.agent_graph.ainvoke(initial_state)
            final_messages = result["messages"]
            answer = ""
            sources = []
            for msg in final_messages:
                if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
                    answer = msg.content
                if isinstance(msg, ToolMessage) and msg.content:
                    sources.append({"content": msg.content[:300], "type": "tool_result"})
            return {"answer": answer, "sources": sources, "mode": "agentic", "tokens_usage": {}}
        except Exception as e:
            logger.error("Agent chat error: %s", e, exc_info=True)
            return {"answer": f"Agent error: {e}", "sources": [], "mode": "agentic", "error": str(e)}

    async def standard_rag(self, message: str, top_k: int = 5) -> Dict[str, Any]:
        os_svc = _get_opensearch_service()
        results = []
        if _opensearch_available and os_svc is not None:
            try:
                client = await os_svc.get_client()
                results = await retrieval_service.hybrid_search(client, message, settings.OPENSEARCH_INDEX, top_k=top_k)
            except Exception as e:
                logger.warn("OS fail, PG fallback: %s", e)
                results = []

        if not results:
            results = await self._pg_fallback_search(message, top_k)

        if not results:
            return {"answer": "No relevant papers found.", "sources": [], "mode": "standard"}

        ctx_parts = []
        sources = []
        for i, r in enumerate(results, 1):
            c = r.get("content", "")
            s = r.get("score", 0)
            ctx_parts.append(f"[src {i}, score {s:.3f}]\n{c}")
            sources.append({"content": c, "score": s, "doc_id": r.get("document_id", ""), "chunk_idx": r.get("chunk_index", 0)})

        context = "\n\n---\n\n".join(ctx_parts)
        prompt = (
            f"Answer based on reference materials below.\n\n## Reference:\n{context}"
            f"\n\n## Question:\n{message}\n\nRules:\n1. Use materials only 2. Cite source number 3. Concise academic style"
        )

        try:
            resp = await self.llm.ainvoke([HumanMessage(content=prompt)])
            return {"answer": resp.content, "sources": sources, "mode": "standard"}
        except Exception as e:
            logger.error("Standard RAG error: %s", e, exc_info=True)
            return {"answer": f"Error: {e}", "sources": sources, "mode": "standard", "error": str(e)}

    async def _pg_fallback_search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        from sqlalchemy import or_, select
        from app.core.database import AsyncSessionLocal
        from app.models.document import Chunk, Document

        try:
            keywords = [kw.strip() for kw in query.split() if len(kw.strip()) > 1]
            if not keywords:
                keywords = [query.strip()]

            async with AsyncSessionLocal() as session:
                conditions = [Chunk.content.ilike(f"%{kw}%") for kw in keywords]
                stmt = (
                    select(Chunk, Document.filename)
                    .join(Document, Chunk.document_id == Document.id)
                    .where(or_(*conditions)).order_by(Chunk.chunk_index).limit(top_k)
                )
                result = await session.execute(stmt)
                rows = result.all()
                out = []
                for i, (chunk, fname) in enumerate(rows, 1):
                    out.append({
                        "id": str(chunk.id), "content": chunk.content,
                        "score": round(1.0 - i * 0.1, 2),
                        "doc_id": str(chunk.document_id),
                        "chunk_idx": chunk.chunk_index, "meta": chunk.meta_data or {},
                    })
                return out
        except Exception as e:
            logger.error("PG fallback error: %s", e, exc_info=True)
            return []


agent_service = AgentService()
