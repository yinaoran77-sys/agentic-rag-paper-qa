"""
文档处理服务（Phase 6 升级版 - 支持 Docling）

相比 Phase 3 的 pypdf 版本，这个版本：
- 优先用 Docling【IBM 出品的文档解析库，能识别表格、标题、段落】
-  fallback 到 pypdf（防止 Docling 解析失败）
- 更好的中文 PDF 支持
- 保留文档结构信息（标题、段落、表格）

为什么用 Docling 而不是直接 pypdf？
- pypdf 只能提取纯文本，表格会变成乱码
- Docling 能识别文档结构（标题、段落、表格、图片）
- 对学术论文特别有用（表格数据不会丢）
- 支持 OCR（扫描件也能处理）

依赖（已在 requirements.txt）：
- docling >= 1.0.0
- pymupdf >= 1.24.0（Docling 的内部依赖）
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.document import Chunk, Document, DocumentStatus
from app.services.opensearch import opensearch_service

logger = logging.getLogger(__name__)

# ================================================================
# 文本分块（支持按句子边界分割）
# ================================================================


def split_text_sentence_aware(text: str, chunk_size: int = 512, overlap: int = 128) -> List[str]:
    """
    按句子边界分块（比简单字符分块更智能）

    思路（大白话）：
    1. 先把文本按句子分开（用。！？等标点）
    2. 然后逐个句子往块里塞，直到快满了
    3. 新块从上一个块的结尾处开始（overlap）
    4. 这样不会把一句话拦腰切断

    参数：
      text: 原始文本
      chunk_size: 每块最大字符数（默认 512）
      overlap: 相邻块重叠字符数（默认 128）

    返回：
      分好的块列表
    """
    if not text or not text.strip():
        return []

    # 简单句子分割（中英文通用）
    import re
    sentence_endings = re.compile(r'([。！？\n\.!?\n])')
    parts = sentence_endings.split(text)

    # 把标点和前面的句子拼回去
    sentences = []
    current = ""
    for part in parts:
        current += part
        if sentence_endings.match(part):
            sentences.append(current.strip())
            current = ""
    if current.strip():
        sentences.append(current.strip())

    # 按 chunk_size 分组
    chunks = []
    current_chunk = ""
    for sent in sentences:
        if len(current_chunk) + len(sent) <= chunk_size:
            current_chunk += sent
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = sent

    if current_chunk:
        chunks.append(current_chunk)

    # 添加 overlap
    if len(chunks) <= 1 or overlap <= 0:
        return chunks

    result = [chunks[0]]
    for chunk in chunks[1:]:
        prev = result[-1]
        if len(prev) > overlap:
            merged = prev[-overlap:] + chunk
            result[-1] = prev  # 保持不变
            result.append(merged)
        else:
            result.append(chunk)

    return result


# ================================================================
# Docling PDF 解析
# ================================================================


def extract_text_with_docling(pdf_path: str) -> List[Dict[str, Any]]:
    """
    用 Docling 解析 PDF，返回结构化内容

    返回格式：
    [
      {"type": "title", "text": "摘要"},
      {"type": "paragraph", "text": "本文研究了..."},
      {"type": "table", "text": "表1：实验结果..."},
      ...
    ]

    为什么返回结构化内容而不是纯文本？
    - 检索时可以根据类型加权（标题的权重更高）
    - 回答时可以引用"见表1"而不是一大段文字
    - 前端展示时可以做格式化
    """
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(pdf_path)

        items = []
        # 遍历 Docling 识别出的所有内容块
        for item in result.document.iterate_items():
            # item 是 (item, level) 元组
            doc_item = item[0] if isinstance(item, tuple) else item

            # 判断内容类型
            item_type = "paragraph"  # 默认段落
            if hasattr(doc_item, "label"):
                label = str(doc_item.label).lower()
                if "title" in label or "heading" in label:
                    item_type = "title"
                elif "table" in label:
                    item_type = "table"
                elif "figure" in label or "image" in label:
                    item_type = "image"

            text = doc_item.text if hasattr(doc_item, "text") else str(doc_item)
            if text.strip():
                items.append({"type": item_type, "text": text.strip()})

        logger.info(f"Docling 解析完成：{len(items)} 个内容块")
        return items

    except ImportError:
        logger.warning("Docling 未安装，fallback 到 pypdf")
        return []
    except Exception as e:
        logger.error(f"Docling 解析失败: {e}，fallback 到 pypdf")
        return []


def extract_text_from_pdf(pdf_path: str) -> str:
    """
    PDF → 纯文本（自动选择最佳解析器）

    优先级：
    1. Docling（如果能用）
    2. pypdf（fallback）

    返回拼接好的纯文本字符串
    """
    # 先试 Docling
    structured = extract_text_with_docling(pdf_path)
    if structured:
        return "\n\n".join([item["text"] for item in structured])

    # Fallback：pypdf
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    parts = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            parts.append(text)
        else:
            logger.warning(f"第 {i+1} 页无法提取文本")
    return "\n\n".join(parts)


def extract_structured_from_pdf(pdf_path: str) -> List[Dict[str, Any]]:
    """
    PDF → 结构化内容（保留类型信息）

    如果 Docling 可用，返回结构化内容；
    否则用 pypdf 提取纯文本，再用 sentence-aware 分块器拆分。
    """
    structured = extract_text_with_docling(pdf_path)
    if structured:
        return structured

    # Fallback：pypdf + sentence-aware 分块（避免整文档变成 1 个 chunk）
    text = extract_text_from_pdf(pdf_path)
    if not text:
        return []

    # 用分块器把长文本拆成多个 paragraph 块
    chunks = split_text_sentence_aware(text, chunk_size=settings.CHUNK_SIZE, overlap=settings.CHUNK_OVERLAP)
    return [{"type": "paragraph", "text": chunk} for chunk in chunks]


# ================================================================
# Embedding 调用
# ================================================================

EMBEDDING_MODEL = settings.EMBEDDING_MODEL  # 来自 .env，默认 nomic-embed-text
EMBEDDING_DIM = 768  # nomic-embed-text 输出 768 维


async def get_embedding(text: str) -> List[float]:
    """调用 Ollama Embeddings API 获取向量"""
    url = f"{settings.OLLAMA_BASE_URL}/api/embeddings"
    payload = {"model": EMBEDDING_MODEL, "prompt": text}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=30) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["embedding"]


async def get_embeddings_batch(texts: List[str], batch_size: int = 8) -> List[List[float]]:
    """批量获取嵌入向量（带重试，失败返回 None）"""
    results = []
    for text in texts:
        vec = None
        for attempt in range(3):  # 重试 3 次
            try:
                vec = await get_embedding(text)
                break
            except Exception as e:
                logger.warning(f"嵌入失败（第 {attempt+1}/3 次）: {e}")
                await asyncio.sleep(1.0 * (attempt + 1))  # 递增等待
        if vec is None:
            logger.error(f"嵌入彻底失败，跳过该块（前 100 字: {text[:100]}...）")
        results.append(vec)
    return results


# ================================================================
# 文档处理主流程（Phase 6 升级版）
# ================================================================


async def process_document(document_id: str, file_path: str, filename: str) -> Dict[str, Any]:
    """
    处理一篇文档的完整流程（支持结构化解析）

    相比 Phase 3 版本的新增功能：
    - 用 Docling 做结构化解析（如果可用）
    - 分块时保留内容类型信息
    - 标题类型的内容在索引时打上标记

    步骤：
      1. 更新状态 → processing
      2. 提取文本（Docling 优先，pypdf fallback）
      3. 按句子边界分块
      4. 生成嵌入向量
      5. 存 PostgreSQL
      6. 索引到 OpenSearch（带类型信息）
      7. 更新状态 → completed
    """
    from sqlalchemy import select, update

    async with AsyncSessionLocal() as session:
        # 1. 标记为处理中
        await session.execute(
            update(Document).where(Document.id == document_id).values(status=DocumentStatus.PROCESSING)
        )
        await session.commit()

        try:
            # 2. 提取文本
            suffix = Path(filename).suffix.lower()
            if suffix == ".pdf":
                # 优先用结构化解析
                structured_items = extract_structured_from_pdf(file_path)
                if not structured_items:
                    raise ValueError("PDF 解析结果为空")
                # 把结构化内容转成分块
                chunks_text, chunk_types = _structure_to_chunks(structured_items, chunk_size=settings.CHUNK_SIZE)
            elif suffix in (".txt", ".md"):
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                chunks_text = split_text_sentence_aware(text, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
                chunk_types = ["paragraph"] * len(chunks_text)
            else:
                raise ValueError(f"不支持的文件类型: {suffix}")

            if not chunks_text:
                raise ValueError("分块结果为空")

            logger.info(f"文档 {filename}：分成 {len(chunks_text)} 块")

            # 3. 生成嵌入向量
            embeddings = await get_embeddings_batch(chunks_text)

            # 4. 构建 Chunk 对象（跳过嵌入失败的块）
            chunks_objs = []
            os_docs = []

            for i, (chunk_text, chunk_type, embedding) in enumerate(zip(chunks_text, chunk_types, embeddings)):
                chunk_id = str(uuid.uuid4())
                chunk_obj = Chunk(
                    id=chunk_id,
                    document_id=document_id,
                    content=chunk_text,
                    chunk_index=i,
                    meta_data={"char_count": len(chunk_text), "content_type": chunk_type},
                )
                chunks_objs.append(chunk_obj)

                # 只有嵌入成功的块才索引到 OpenSearch
                if embedding is not None:
                    os_docs.append({
                        "_id": chunk_id,
                        "_source": {
                            "content": chunk_text,
                            "content_vector": embedding,
                            "document_id": document_id,
                            "chunk_index": i,
                            "meta_data": {"char_count": len(chunk_text), "content_type": chunk_type},
                        }
                    })

            # 5. 写入 PostgreSQL
            session.add_all(chunks_objs)
            await session.commit()
            logger.info(f"PostgreSQL 写入 {len(chunks_objs)} 个 chunks")

            # 6. 索引到 OpenSearch
            os_result = await opensearch_service.index_documents(os_docs)
            logger.info(f"OpenSearch 索引 {os_result.get('count', 0)} 个 chunks")

            # 7. 更新状态 → completed
            await session.execute(
                update(Document).where(Document.id == document_id).values(
                    status=DocumentStatus.COMPLETED,
                    file_size=sum(len(c) for c in chunks_text),
                )
            )
            await session.commit()

            return {"ok": True, "chunks": len(chunks_objs)}

        except Exception as e:
            logger.error(f"处理文档失败: {e}", exc_info=True)
            await session.execute(
                update(Document).where(Document.id == document_id).values(
                    status=DocumentStatus.FAILED,
                    error_message=str(e)[:500],
                )
            )
            await session.commit()
            return {"ok": False, "error": str(e)}


def _structure_to_chunks(
    structured_items: List[Dict[str, Any]],
    chunk_size: int = 512,
) -> tuple:
    """
    把 Docling 的结构化内容转成分块

    策略（大白话）：
    - 标题（title）单独成块（检索时权重更高）
    - 段落（paragraph）按 chunk_size 合并
    - 表格（table）单独成块（不被切断）

    返回：
      (chunks_text: List[str], chunk_types: List[str])
    """
    chunks_text = []
    chunk_types = []

    current_paragraph = ""
    for item in structured_items:
        item_type = item["type"]
        text = item["text"]

        if item_type == "title":
            # 标题单独一块
            chunks_text.append(text)
            chunk_types.append("title")
        elif item_type == "table":
            # 表格单独一块
            chunks_text.append(text)
            chunk_types.append("table")
        elif item_type == "image":
            # 图片暂时跳过（后续可以存图片描述）
            continue
        else:
            # paragraph：合并直到达到 chunk_size
            if len(current_paragraph) + len(text) <= chunk_size:
                current_paragraph += "\n" + text if current_paragraph else text
            else:
                if current_paragraph:
                    chunks_text.append(current_paragraph)
                    chunk_types.append("paragraph")
                current_paragraph = text

    # 收尾
    if current_paragraph:
        chunks_text.append(current_paragraph)
        chunk_types.append("paragraph")

    return chunks_text, chunk_types
