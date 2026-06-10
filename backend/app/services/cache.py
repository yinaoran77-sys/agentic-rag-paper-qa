"""
Redis 语义缓存服务（Phase 7）

这是什么？
- 普通缓存：相同问题 → 直接返回缓存答案（字符完全匹配）
- 语义缓存：相似问题 → 返回缓存答案（意思差不多就行）

举个例子：
  用户问："农村空气污染有哪些健康影响？"
  缓存里有："农村地区空气污染对健康的影响有哪些？"
  → 语义相似度 > 阈值 → 直接返回缓存答案，不用再跑 LLM！

为什么这么做？
- LLM 推理很慢（~2-5秒），缓存秒回（~4ms）
- 省 token、省算力、用户体验好
- 论文 QA 场景，很多问题都是相似的

实现原理（大白话）：
1. 把用户问题变成向量（embedding）
2. 去 Redis 里找"最相似的已缓存问题"
3. 如果相似度 > 阈值（默认 0.85）→ 直接返回缓存
4. 否则：正常跑 RAG → 把结果存进 Redis
"""

import hashlib
import json
import logging
from typing import Any, Dict, Optional

import aiohttp
from redis import Redis
from redis.connection import ConnectionPool

from app.core.config import settings

logger = logging.getLogger(__name__)

# 相似度阈值（余弦相似度，1.0 = 完全相同）
# 0.85 是个比较保守的值，大多数语义相同的问题能命中
DEFAULT_SIMILARITY_THRESHOLD = 0.85

# 缓存过期时间（秒），默认 7 天
DEFAULT_TTL = 7 * 24 * 3600


class SemanticCacheService:
    """
    Redis 语义缓存服务（单例）

    核心方法：
    - get()：查缓存（相似问题 → 返回缓存）
    - set()：写缓存（问题 + 答案 + 上下文）
    - clear()：清缓存（可选）

    Redis 存储结构（大白话）：
    - 用 Redis 的 Sorted Set（有序集合）存所有问题的向量
    - 用 Redis 的 String 存问题和答案的映射
    - 用 aiohttp 调 Ollama embedding API 算相似度
    """

    def __init__(self):
        self.enabled = True
        self.threshold = DEFAULT_SIMILARITY_THRESHOLD
        self.ttl = DEFAULT_TTL
        self._redis: Optional[Redis] = None
        self._pool: Optional[ConnectionPool] = None

    async def _get_redis(self) -> Redis:
        """懒加载 Redis 连接池（用单例，避免重复建连）"""
        if self._redis is None:
            try:
                self._pool = ConnectionPool.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,  # 自动把 bytes 转成 str
                )
                self._redis = Redis(connection_pool=self._pool)
                # 测试连通性
                self._redis.ping()
                logger.info("✅ Redis 语义缓存连接成功")
            except Exception as e:
                logger.warning(f"⚠️  Redis 不可用，语义缓存停用: {e}")
                self.enabled = False
        return self._redis

    async def _embed(self, text: str) -> list:
        """文字 → 向量（调 Ollama Embeddings API）"""
        url = f"{settings.OLLAMA_BASE_URL}/api/embeddings"
        payload = {"model": settings.EMBEDDING_MODEL, "prompt": text}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=30) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["embedding"]

    def _cosine_similarity(self, v1: list, v2: list) -> float:
        """计算两个向量的余弦相似度"""
        import math
        dot = sum(a * b for a, b in zip(v1, v2))
        mag1 = math.sqrt(sum(a * a for a in v1))
        mag2 = math.sqrt(sum(b * b for b in v2))
        if mag1 == 0 or mag2 == 0:
            return 0.0
        return dot / (mag1 * mag2)

    def _cache_key(self, question: str) -> str:
        """生成缓存 key（用问题的 MD5，避免特殊字符问题）"""
        q_hash = hashlib.md5(question.encode("utf-8")).hexdigest()
        return f"semantic_cache:{q_hash}"

    async def get(self, question: str) -> Optional[Dict[str, Any]]:
        """
        查语义缓存

        流程：
        1. 把问题变成向量
        2. 遍历缓存里所有问题，算相似度
        3. 如果有相似度 > 阈值的 → 返回缓存答案
        4. 否则返回 None（未命中）

        参数：
          question: 用户问题

        返回：
          {"answer": "...", "sources": [...], "from_cache": True} 或 None
        """
        if not self.enabled:
            return None

        redis = await self._get_redis()
        if redis is None:
            return None

        try:
            # 算问题的向量
            q_vector = await self._embed(question)

            # 遍历所有缓存的 key，找最相似的
            # （生产环境应该用向量数据库，这里用暴力遍历做演示）
            keys = redis.keys("semantic_cache:*")
            best_score = -1.0
            best_data = None

            for key in keys:
                # 取缓存里存的问题向量
                vec_str = redis.hget(key, "question_vector")
                if not vec_str:
                    continue
                cached_vec = json.loads(vec_str)
                score = self._cosine_similarity(q_vector, cached_vec)

                if score > best_score:
                    best_score = score
                    data_str = redis.hget(key, "response_data")
                    if data_str:
                        best_data = json.loads(data_str)

            # 判断是否命中
            if best_score >= self.threshold and best_data:
                logger.info(f"🎯 语义缓存命中！相似度={best_score:.3f}")
                best_data["from_cache"] = True
                best_data["similarity"] = round(best_score, 4)
                return best_data

            logger.debug(f"语义缓存未命中，最高相似度={best_score:.3f}")
            return None

        except Exception as e:
            logger.error(f"语义缓存查询失败: {e}", exc_info=True)
            return None

    async def set(
        self,
        question: str,
        answer: str,
        sources: list,
        mode: str = "standard",
    ) -> None:
        """
        写缓存

        存储结构（Redis Hash）：
          key: semantic_cache:{md5(question)}
          fields:
            - question: 原始问题
            - question_vector: 问题的向量（JSON）
            - response_data: 完整响应（answer + sources + mode，JSON）
            - created_at: 时间戳

        参数：
          question: 用户问题
          answer: LLM 的回答
          sources: 引用来源
          mode: RAG 模式（standard / agentic）
        """
        if not self.enabled:
            return

        redis = await self._get_redis()
        if redis is None:
            return

        try:
            import time
            q_vector = await self._embed(question)
            cache_key = self._cache_key(question)

            response_data = {
                "answer": answer,
                "sources": sources,
                "mode": mode,
                "from_cache": False,
            }

            redis.hset(
                cache_key,
                mapping={
                    "question": question,
                    "question_vector": json.dumps(q_vector),
                    "response_data": json.dumps(response_data),
                    "created_at": str(int(time.time())),
                },
            )
            redis.expire(cache_key, self.ttl)
            logger.info(f"💾 语义缓存已写入：{cache_key}")

        except Exception as e:
            logger.error(f"语义缓存写入失败: {e}", exc_info=True)

    async def clear(self, question: Optional[str] = None) -> int:
        """
        清缓存

        参数：
          question: 如果指定问题 → 只删这一条
                    如果为 None → 清空所有语义缓存

        返回：
          删除的 key 数量
        """
        redis = await self._get_redis()
        if redis is None:
            return 0

        if question:
            key = self._cache_key(question)
            deleted = redis.delete(key)
            logger.info(f"🗑️  删除缓存：{key}")
            return deleted
        else:
            keys = redis.keys("semantic_cache:*")
            if keys:
                deleted = redis.delete(*keys)
                logger.info(f"🗑️  清空语义缓存：{deleted} 条")
                return deleted
            return 0

    async def stats(self) -> Dict[str, Any]:
        """缓存统计信息"""
        redis = await self._get_redis()
        if redis is None:
            return {"enabled": False}

        keys = redis.keys("semantic_cache:*")
        return {
            "enabled": True,
            "cached_questions": len(keys),
            "threshold": self.threshold,
            "ttl_seconds": self.ttl,
        }

    async def close(self) -> None:
        """关闭 Redis 连接（应用 shutdown 时调用）"""
        if self._redis:
            self._redis.close()
            self._redis = None
        if self._pool:
            self._pool.disconnect()
            self._pool = None


# ----------------------------------------------------------------
# 全局服务单例
# ----------------------------------------------------------------
semantic_cache = SemanticCacheService()
