"""
检索路由（Phase 4 实现）

混合检索接口：支持 BM25 / Vector / Hybrid / RRF 四种模式
"""

import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.opensearch import OpenSearchService, opensearch_service
from app.services.retrieval import RetrievalService, retrieval_service

router = APIRouter()


# ================================================================
# Pydantic 模型
# ================================================================

class SearchResult(BaseModel):
    """单条检索结果"""
    id: str = Field(..., description="Chunk UUID")
    score: float = Field(..., description="相关性分数")
    content: str = Field(..., description="文本块内容")
    document_id: str = Field(..., description="所属文档 ID")
    chunk_index: int = Field(0, description="文本块序号")
    dense_score: float | None = Field(None, description="向量检索分数（仅 hybrid 模式）")
    sparse_score: float | None = Field(None, description="BM25 分数（仅 hybrid 模式）")
    meta_data: dict = Field(default_factory=dict, description="额外元数据")


class SearchResponse(BaseModel):
    """检索响应"""
    query: str
    mode: str
    top_k: int
    total: int
    time_ms: float
    results: List[SearchResult]


class IndexInfo(BaseModel):
    """索引状态"""
    index: str
    exists: bool
    document_count: int | None = None
    store_size_mb: float | None = None
    message: str = ""


# ================================================================
# 核心检索接口
# ================================================================

@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, description="检索关键词 / 问题"),
    top_k: int = Query(5, ge=1, le=50, description="返回结果数"),
    mode: str = Query(
        "hybrid",
        pattern="^(bm25|vector|hybrid|rrf)$",
        description="检索模式: bm25 / vector / hybrid / rrf",
    ),
    os_service: OpenSearchService = Depends(lambda: opensearch_service),
    ret_service: RetrievalService = Depends(lambda: retrieval_service),
):
    """
    混合检索接口

    四种检索模式：
    - **bm25** — 传统全文检索，基于关键词匹配
    - **vector** — 语义向量检索，基于意思相似度
    - **hybrid** — 加权混合（向量 0.6 + BM25 0.4），综合最优
    - **rrf** — 倒数排序融合，不依赖绝对分数
    """
    client = await os_service.get_client()

    # 检查索引是否存在
    if not await os_service.index_exists():
        return SearchResponse(
            query=q, mode=mode, top_k=top_k, total=0, time_ms=0, results=[]
        )

    start_time = time.perf_counter()

    # 按模式分发
    index_name = os_service.index_name
    if mode == "bm25":
        raw_results = await ret_service.sparse_search(client, q, index_name, top_k)
    elif mode == "vector":
        raw_results = await ret_service.dense_search(client, q, index_name, top_k)
    elif mode == "hybrid":
        raw_results = await ret_service.hybrid_search(client, q, index_name, top_k)
    elif mode == "rrf":
        raw_results = await ret_service.rrf_search(client, q, index_name, top_k)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的检索模式: {mode}")

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    # 格式化成 SearchResult
    results = [
        SearchResult(
            id=r.get("id", ""),
            score=r.get("score", 0.0),
            content=r.get("content", ""),
            document_id=r.get("document_id", ""),
            chunk_index=r.get("chunk_index", 0),
            dense_score=r.get("dense_score"),
            sparse_score=r.get("sparse_score"),
            meta_data=r.get("meta_data", {}),
        )
        for r in raw_results
    ]

    return SearchResponse(
        query=q,
        mode=mode,
        top_k=top_k,
        total=len(results),
        time_ms=round(elapsed_ms, 2),
        results=results,
    )


# ================================================================
# 索引管理接口（仅开发用）
# ================================================================

@router.get("/status", response_model=IndexInfo)
async def index_status(
    os_service: OpenSearchService = Depends(lambda: opensearch_service),
):
    """查看索引状态"""
    exists = await os_service.index_exists()

    info = IndexInfo(index=os_service.index_name, exists=exists)

    if exists:
        stats = await os_service.get_index_stats()
        if stats:
            info.document_count = stats["document_count"]
            info.store_size_mb = stats["store_size_mb"]
            info.message = "索引正常"
        else:
            info.message = "索引存在但无法获取统计信息"
    else:
        info.message = "索引不存在，文档上传后会自动创建"

    return info


@router.post("/create", response_model=IndexInfo)
async def create_index(
    os_service: OpenSearchService = Depends(lambda: opensearch_service),
):
    """创建索引（幂等操作）"""
    response = await os_service.create_index()

    return IndexInfo(
        index=os_service.index_name,
        exists=True,
        message=str(response.get("status", "created")),
    )


@router.delete("/delete", response_model=IndexInfo)
async def delete_index(
    os_service: OpenSearchService = Depends(lambda: opensearch_service),
):
    """删除索引（危险操作）"""
    response = await os_service.delete_index()

    return IndexInfo(
        index=os_service.index_name,
        exists=False,
        message=str(response.get("status", "deleted")),
    )
