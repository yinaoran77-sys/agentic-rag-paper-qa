"""
OpenSearch 异步客户端与索引管理

三大职责：
1. 连接管理（异步客户端创建、连接检查）
2. 索引管理（创建 / 删除 / 重建 / 检查状态）
3. 文档 CRUD（批量索引文本块、按文档 ID 删除）

设计考虑：
- 为什么用 raw opensearch-py 而不是 langchain-opensearch？
  langchain-opensearch 封了一层 LangChain VectorStore 抽象，
  但我们做混合检索时需要同时用 BM25 和向量检索，
  VectorStore 的接口不够灵活，直接操作 OpenSearch 更可控。
- 为什么用 AsyncOpenSearch？
  FastAPI 是异步框架，同步客户端会阻塞事件循环。
"""

import logging
from typing import Any, Dict, List, Optional

from opensearchpy import AsyncOpenSearch, OpenSearchException
from opensearchpy.helpers import async_bulk as opensearch_async_bulk

from app.core.config import settings

logger = logging.getLogger(__name__)

# OpenSearch 连接状态（全局标记，避免重复尝试连接）
_opensearch_available = None  # None=未检测, True=可用, False=不可用


def is_opensearch_available() -> bool:
    """检查 OpenSearch 是否可用（带缓存）"""
    global _opensearch_available
    if _opensearch_available is None:
        # 未检测过，尝试连接
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 已有事件循环在运行，创建一个任务
                loop.create_task(_check_opensearch())
            else:
                # 没有事件循环，直接运行
                _opensearch_available = loop.run_until_complete(_check_opensearch_sync())
        except:
            _opensearch_available = False
    return _opensearch_available or False


async def _check_opensearch() -> bool:
    """异步检查 OpenSearch 连接"""
    global _opensearch_available
    try:
        client = AsyncOpenSearch(
            hosts=[settings.OPENSEARCH_URL],
            request_timeout=3,  # 3秒超时
        )
        await client.info()
        await client.close()
        _opensearch_available = True
        logger.info("✅ OpenSearch 连接成功")
    except:
        _opensearch_available = False
        logger.warning("⚠️ OpenSearch 不可用，将使用 PostgreSQL 存储")
    return _opensearch_available or False


async def _check_opensearch_sync() -> bool:
    """同步检查 OpenSearch 连接（用于导入时）"""
    try:
        client = AsyncOpenSearch(
            hosts=[settings.OPENSEARCH_URL],
            request_timeout=3,
        )
        info = await client.info()
        await client.close()
        return True
    except:
        return False

# ----------------------------------------------------------------
# 索引映射 (Index Mapping)
# ----------------------------------------------------------------
# 一张"表结构"定义了哪些字段以及各自的类型
#
# content          → text  类型  → BM25 全文检索（存原始文本）
# content_vector   → knn_vector → 向量检索（存 embedding 后的向量）
# document_id      → keyword    → 精确匹配（不分析）
# chunk_index      → integer   → 排序用
# metadata         → object    → JSON 灵活存储
PAPER_CHUNKS_MAPPING: Dict[str, Any] = {
    "settings": {
        "index": {
            "knn": True,                          # 开启 KNN 支持
            "knn.algo_param.ef_construction": 512, # 构建时的搜索参数
            "number_of_shards": 1,                 # 单节点集群，不分区
            "number_of_replicas": 0,               # 不需要副本（开发环境）
        }
    },
    "mappings": {
        "properties": {
            # ---- 文本字段（BM25 检索） ----
            "content": {
                "type": "text",
                "analyzer": "standard",  # 分词器：目前用英文标准，中文需要换
                # TODO: 换 ik_max_word 中文分词器 → 安装 ik 插件
            },

            # ---- 向量字段（语义检索） ----
            "content_vector": {
                "type": "knn_vector",
                "dimension": 768,  # nomic-embed-text 输出 768 维
                "method": {
                    "name": "hnsw",    # HNSW 算法，检索速度快
                    "space_type": "cosinesimil",  # 余弦相似度
                    "engine": "nmslib",
                    "parameters": {
                        "ef_construction": 512,
                        "m": 16,  # 每个节点最多 16 条边
                    },
                },
            },

            # ---- 元数据字段 ----
            "document_id": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "opensearch_doc_id": {"type": "keyword"},

            # ---- 动态元数据（JSON） ----
            "meta_data": {
                "type": "object",
                "enabled": True,
            },
        }
    },
}


class OpenSearchService:
    """OpenSearch 异步服务"""

    def __init__(self):
        self._client: Optional[AsyncOpenSearch] = None
        self.index_name = settings.OPENSEARCH_INDEX  # "paper_chunks"

    # ---- 客户端管理 ----

    async def get_client(self) -> AsyncOpenSearch:
        """
        获取 OpenSearch 异步客户端（懒加载+单例）

        为什么用懒加载？
        - 不在模块加载时就连接（启动时 OpenSearch 可能还没就绪）
        - 第一次使用时才建立连接
        """
        if self._client is None:
            host = settings.OPENSEARCH_HOST
            port = settings.OPENSEARCH_PORT
            self._client = AsyncOpenSearch(
                hosts=[{"host": host, "port": port}],
                http_compress=True,       # 压缩传输，省带宽
                timeout=30,               # 请求超时 30 秒
                max_retries=3,            # 失败重试 3 次
                retry_on_timeout=True,    # 超时也重试
            )
            logger.info(f"OpenSearch 客户端已创建 → {host}:{port}")
        return self._client

    async def close(self) -> None:
        """关闭客户端连接"""
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("OpenSearch 客户端已关闭")

    async def ping(self) -> bool:
        """检查 OpenSearch 是否可用"""
        try:
            client = await self.get_client()
            return await client.ping()
        except OpenSearchException:
            return False

    # ---- 索引管理 ----

    async def index_exists(self) -> bool:
        """检查索引是否存在"""
        client = await self.get_client()
        return await client.indices.exists(index=self.index_name)

    async def create_index(self) -> Dict[str, Any]:
        """
        创建索引（幂等操作）

        如果索引已经存在，什么都不做。
        这保证了反复调用也不会报错。
        """
        client = await self.get_client()

        if await self.index_exists():
            logger.info(f"索引已存在，跳过创建: {self.index_name}")
            return {"acknowledged": True, "index": self.index_name, "status": "already_exists"}

        logger.info(f"创建索引: {self.index_name}")
        response = await client.indices.create(
            index=self.index_name,
            body=PAPER_CHUNKS_MAPPING,
        )
        logger.info(f"索引创建成功: {self.index_name}")
        return {"acknowledged": True, "index": self.index_name, "status": "created"}

    async def delete_index(self) -> Dict[str, Any]:
        """删除索引（危险操作，谨慎使用）"""
        client = await self.get_client()

        if not await self.index_exists():
            return {"acknowledged": True, "index": self.index_name, "status": "not_found"}

        logger.warning(f"删除索引: {self.index_name}")
        response = await client.indices.delete(index=self.index_name)
        logger.warning(f"索引已删除: {self.index_name}")
        return {"acknowledged": True, "index": self.index_name, "status": "deleted"}

    async def rebuild_index(self) -> Dict[str, Any]:
        """重建索引（先删后建）"""
        await self.delete_index()
        return await self.create_index()

    async def get_index_stats(self) -> Optional[Dict[str, Any]]:
        """获取索引统计信息（文档数、占用大小）"""
        client = await self.get_client()

        if not await self.index_exists():
            return None

        stats = await client.indices.stats(index=self.index_name)
        doc_count = stats["indices"][self.index_name]["primaries"]["docs"]["count"]
        store_size = stats["indices"][self.index_name]["primaries"]["store"]["size_in_bytes"]

        return {
            "index": self.index_name,
            "document_count": doc_count,
            "store_size_bytes": store_size,
            "store_size_mb": round(store_size / 1024 / 1024, 2),
        }

    # ---- 文档 CRUD ----

    async def index_documents(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量索引文档（文本块）

        参数：
          documents: [{"_id": "chunk-uuid", "_source": {content, content_vector, ...}}, ...]

        返回：
          {"ok": True, "count": N, "errors": False}
        """
        client = await self.get_client()

        if not documents:
            return {"ok": True, "count": 0, "errors": False}

        # 确保索引存在
        await self.create_index()

        actions = [
            {
                "_index": self.index_name,
                "_id": doc["_id"],
                "_source": doc["_source"],
            }
            for doc in documents
        ]

        # bulk 批量写入
        success, errors = await opensearch_async_bulk(client, actions, raise_on_error=False)

        if errors:
            logger.warning(f"批量索引: {success} 成功, {len(errors)} 个错误")
            logger.warning(f"首个错误: {errors[0] if errors else 'none'}")
        else:
            logger.info(f"批量索引完成: {success} 个文档")

        return {"ok": True, "count": success, "errors": len(errors) > 0}

    async def delete_by_document(self, document_id: str) -> Dict[str, Any]:
        """
        按文档 ID 删除所有文本块

        场景：删除一篇论文时，连它的所有 chunk 一起删。
        """
        client = await self.get_client()

        response = await client.delete_by_query(
            index=self.index_name,
            body={
                "query": {
                    "term": {"document_id": document_id}
                }
            },
            refresh=True,  # 立即刷新，保证后续搜索不会看到已删除的文档
        )

        deleted_count = response.get("deleted", 0)
        logger.info(f"按文档 ID 删除: {document_id} → {deleted_count} 个 chunks")
        return {"ok": True, "deleted": deleted_count}


# ----------------------------------------------------------------
# 全局服务单例
# ----------------------------------------------------------------
# FastAPI 用依赖注入，但 OpenSearch 客户端是有状态的重资源
# 全局单例避免反复创建连接
opensearch_service = OpenSearchService()
