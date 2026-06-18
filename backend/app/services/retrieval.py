"""
混合检索引擎：向量检索 + BM25 全文检索 + 混合融合

四种检索策略：
1. dense  → K 近邻向量检索（语义匹配，找"意思相近"的）
2. sparse → BM25 全文检索（关键词匹配，找"字面对得上的"）
3. hybrid → 加权混合（默认：向量 0.6 + BM25 0.4）
4. rrf    → 倒数排序融合（不依赖绝对分数，更鲁棒）

为什么做混合？
- 向量检索擅长语义（"机器学习"也能找到"深度学习"）
- BM25 擅长精确匹配（"Transformer 架构"就是"Transformer 架构"）
- 两者互补，混合后检索效果 >> 单一方法

Embedding 模型选择（按优先级）：
- LLM_PROVIDER=dashscope → 用 DashScope embedding API（text-embedding-v2，1024 维）
- LLM_PROVIDER=openai    → 用 OpenAI embedding API
- 其他（ollama）          → 用本地 Ollama embedding API
"""

import logging
from typing import Any, Dict, List, Optional

import aiohttp

from app.core.config import settings

logger = logging.getLogger(__name__)

# Ollama embedding API 端点
OLLAMA_EMBEDDINGS_URL = f"{settings.OLLAMA_BASE_URL}/api/embeddings"


class RetrievalService:
    """混合检索服务"""

    def __init__(self):
        self.embedding_model = settings.EMBEDDING_MODEL
        self.top_k = settings.TOP_K
        self.bm25_weight = settings.BM25_WEIGHT
        self.vector_weight = settings.VECTOR_WEIGHT

    # ================================================================
    # 1. Embedding 生成
    # ================================================================

    async def embed_text(self, text: str) -> List[float]:
        """
        文字 → 向量
        
        根据 LLM_PROVIDER 配置选择嵌入提供商：
        - dashscope: 调用阿里云 DashScope embedding API（text-embedding-v2，1024 维）
        - openai:    调用 OpenAI embedding API
        - 其他:      调用本地 Ollama embedding API（nomic-embed-text 等）
        
        参数：
          text: 待向量化的文字
        返回：
          [0.123, -0.456, ...] 1024 维浮点数列表
        """
        provider = settings.LLM_PROVIDER.lower()

        # ---- DashScope embedding API（OpenAI 兼容模式）----
        if provider == "dashscope" and settings.DASHSCOPE_API_KEY:
            return await self._embed_dashscope(text)

        # ---- OpenAI embedding API ----
        if provider == "openai" and settings.OPENAI_API_KEY:
            return await self._embed_openai(text)

        # ---- Ollama embedding API（默认/fallback）----
        return await self._embed_ollama(text)

    async def _embed_dashscope(self, text: str) -> List[float]:
        """调用 DashScope embedding API（OpenAI 兼容模式）"""
        url = f"{settings.DASHSCOPE_BASE_URL}/embeddings"
        headers = {
            "Authorization": f"Bearer {settings.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "text-embedding-v2",
            "input": text,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    # OpenAI 兼容格式: {"data": [{"embedding": [...]}]}
                    embedding = data["data"][0]["embedding"]
                    return embedding

        except aiohttp.ClientError as e:
            logger.error(f"DashScope embedding 请求失败: {e}")
            raise
        except Exception as e:
            logger.error(f"DashScope embedding 异常: {e}")
            raise

    async def _embed_openai(self, text: str) -> List[float]:
        """调用 OpenAI embedding API"""
        import openai
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
        )
        return response.data[0].embedding

    async def _embed_ollama(self, text: str) -> List[float]:
        """调用 Ollama embedding API（原始实现）"""
        payload = {
            "model": self.embedding_model,
            "prompt": text,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(OLLAMA_EMBEDDINGS_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    data = await resp.json()
                    embedding = data.get("embedding", [])

                    if not embedding:
                        raise ValueError(f"Ollama 返回空 embedding: {data}")

                    return embedding

        except aiohttp.ClientError as e:
            logger.error(f"Ollama embedding 请求失败: {e}")
            raise
        except Exception as e:
            logger.error(f"Embedding 生成异常: {e}")
            raise

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        批量向量化（逐个调 API，Ollama 暂不支持 batch embedding）
        """
        embeddings = []
        for text in texts:
            emb = await self.embed_text(text)
            embeddings.append(emb)
        return embeddings

    # ================================================================
    # 2. Dense 检索 - KNN 向量检索
    # ================================================================

    async def dense_search(
        self,
        client,
        query: str,
        index_name: str,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        K 近邻向量检索

        流程：
        1. 把用户问题变成向量（embedding）
        2. 在 OpenSearch 里找最相似的 K 个向量
        3. 返回对应的文本块 + 相似度分数

        参数：
          client: AsyncOpenSearch 客户端
          query:  用户的问题（文字）
          index_name: 索引名
          top_k: 返回几个结果
        """
        if top_k is None:
            top_k = self.top_k

        # Step 1: 文本 → 向量
        query_vector = await self.embed_text(query)

        # Step 2: KNN 检索
        search_body = {
            "size": top_k,
            "_source": ["content", "document_id", "chunk_index", "meta_data"],
            "query": {
                "knn": {
                    "content_vector": {
                        "vector": query_vector,
                        "k": top_k,
                    }
                }
            },
        }

        response = await client.search(index=index_name, body=search_body)

        # Step 3: 格式化结果
        results = self._format_search_results(response)
        return results

    # ================================================================
    # 3. Sparse 检索 - BM25 全文检索
    # ================================================================

    async def sparse_search(
        self,
        client,
        query: str,
        index_name: str,
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        BM25 全文检索

        BM25 是经典的关键词匹配算法：
        - 词频（TF）：关键词出现越多，分数越高
        - 逆文档频率（IDF）：常见的词权重低（"的"、"是"），罕见的词权重高
        - 文档长度：短文档里出现关键词比分匀到长文档更重要

        和 Google 搜索的原理类似，但不用向量，而是分析文字本身。
        """
        if top_k is None:
            top_k = self.top_k

        search_body = {
            "size": top_k,
            "_source": ["content", "document_id", "chunk_index", "meta_data"],
            "query": {
                "match": {
                    "content": {
                        "query": query,
                        "operator": "or",  # or = 匹配任一关键词; and = 必须全匹配
                    }
                }
            },
        }

        response = await client.search(index=index_name, body=search_body)

        results = self._format_search_results(response)
        return results

    # ================================================================
    # 4. Hybrid 混合检索
    # ================================================================

    async def hybrid_search(
        self,
        client,
        query: str,
        index_name: str,
        top_k: Optional[int] = None,
        dense_weight: Optional[float] = None,
        sparse_weight: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        加权混合检索

        策略：同时跑向量检索和 BM25 检索，然后把两个分数线性加权。

        公式：
          final_score = dense_weight × norm_dense_score + sparse_weight × norm_sparse_score

        为什么需要 norm（归一化）？
        - 向量检索分数范围是 [0, 1]（余弦相似度）
        - BM25 分数可能几十甚至几百
        - 不归一化的话，BM25 分数会碾压向量分数
        """
        if top_k is None:
            top_k = self.top_k
        if dense_weight is None:
            dense_weight = self.vector_weight  # 默认 0.6
        if sparse_weight is None:
            sparse_weight = self.bm25_weight   # 默认 0.4

        # 并行跑两种检索
        dense_results = await self.dense_search(client, query, index_name, top_k=top_k * 2)
        sparse_results = await self.sparse_search(client, query, index_name, top_k=top_k * 2)

        # 归一化分数
        dense_norm = self._normalize_scores(dense_results)
        sparse_norm = self._normalize_scores(sparse_results)

        # 加权合并
        merged: Dict[str, Dict[str, Any]] = {}

        # 加入向量检索结果
        for r in dense_norm:
            doc_id = r["id"]
            merged[doc_id] = {
                **r["doc"],
                "dense_score": r["score_raw"],
                "sparse_score": 0.0,
                "score": r["score_norm"] * dense_weight,
            }

        # 加入 BM25 结果，如果已存在则叠加分数
        for r in sparse_norm:
            doc_id = r["id"]
            if doc_id in merged:
                merged[doc_id]["sparse_score"] = r["score_raw"]
                merged[doc_id]["score"] += r["score_norm"] * sparse_weight
            else:
                merged[doc_id] = {
                    **r["doc"],
                    "dense_score": 0.0,
                    "sparse_score": r["score_raw"],
                    "score": r["score_norm"] * sparse_weight,
                }

        # 按混合分数排序，取 top_k
        sorted_results = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
        return sorted_results[:top_k]

    # ================================================================
    # 5. RRF (Reciprocal Rank Fusion) 倒数排序融合
    # ================================================================

    async def rrf_search(
        self,
        client,
        query: str,
        index_name: str,
        top_k: Optional[int] = None,
        k: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        RRF 排序融合

        和加权混合不同，RRF 不关心绝对分数，只关心排名。
        适合分数不可比较的场景（比如向量是余弦相似度，BM25 是 TF-IDF）。

        公式：
          RRF_score(doc) = Σ 1 / (k + rank_of_doc_in_method)

        k=60 是经典值，保证排名第一的贡献度约为 k=0 时的 60 倍差距。

        优点：
        - 不依赖分数归一化
        - 对各种检索器同样公平
        - 在 TREC 评测中表现很好
        """
        if top_k is None:
            top_k = self.top_k

        # 跑两种检索，多取一些候选
        dense_results = await self.dense_search(client, query, index_name, top_k=top_k * 3)
        sparse_results = await self.sparse_search(client, query, index_name, top_k=top_k * 3)

        # 计算 RRF 分数
        rrf_scores: Dict[str, Dict[str, Any]] = {}

        # 向量排名的 RRF 贡献
        for rank, r in enumerate(dense_results, start=1):
            doc_id = r["id"]
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {**r, "rrf_score": 0.0, "dense_rank": rank, "sparse_rank": None}
            rrf_scores[doc_id]["rrf_score"] += 1.0 / (k + rank)

        # BM25 排名的 RRF 贡献
        for rank, r in enumerate(sparse_results, start=1):
            doc_id = r["id"]
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {**r, "rrf_score": 0.0, "dense_rank": None, "sparse_rank": rank}
            else:
                rrf_scores[doc_id]["sparse_rank"] = rank
            rrf_scores[doc_id]["rrf_score"] += 1.0 / (k + rank)

        # 按 RRF 分数排序
        sorted_results = sorted(rrf_scores.values(), key=lambda x: x["rrf_score"], reverse=True)
        return sorted_results[:top_k]

    # ================================================================
    # 工具方法
    # ================================================================

    def _format_search_results(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        把 OpenSearch 原始响应格式化成统一的检索结果

        输入：
          OpenSearch 的 search() 返回值
        输出：
          [
            {"id": "chunk-uuid", "score": 0.95, "content": "...", "document_id": "...", ...},
            ...
          ]
        """
        results = []
        hits = response.get("hits", {}).get("hits", [])

        for hit in hits:
            results.append({
                "id": hit["_id"],
                "score": hit["_score"],
                "content": hit["_source"].get("content", ""),
                "document_id": hit["_source"].get("document_id", ""),
                "chunk_index": hit["_source"].get("chunk_index", 0),
                "meta_data": hit["_source"].get("meta_data", {}),
            })

        return results

    def _normalize_scores(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        分数归一化（Min-Max 缩放）

        把分数映射到 [0, 1] 区间：
          norm_score = (score - min) / (max - min)

        如果是空列表或只有一个结果，返回原始分数。
        """
        if not results:
            return []

        scores = [r["score"] for r in results]
        min_score = min(scores)
        max_score = max(scores)

        # 所有分数一样 → 全部给 1.0
        if max_score == min_score:
            return [
                {"id": r["id"], "doc": r, "score_raw": r["score"], "score_norm": 1.0}
                for r in results
            ]

        normalized = []
        for r in results:
            norm = (r["score"] - min_score) / (max_score - min_score)
            normalized.append({
                "id": r["id"],
                "doc": r,
                "score_raw": r["score"],
                "score_norm": round(norm, 4),
            })

        return normalized


# ----------------------------------------------------------------
# 全局服务单例
# ----------------------------------------------------------------
retrieval_service = RetrievalService()
