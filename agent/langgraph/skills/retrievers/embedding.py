"""Embedding 检索器（设计文档 §7.4.2）。

基于向量余弦相似度的语义召回，使用与 RAGFlow 相同的 Embedding 模型。
模型通过 ``embed_func`` 回调注入，本模块不直接依赖任何具体模型实现。

向量化字段（§7.4.2）：
    ``"{name}。{description} 涉及领域：{domains}。意图：{intents}"``

索引存储为 NumPy 矩阵（``np.ndarray``），余弦相似度计算。
构建是异步的，构建期间 ``embedding_index=None``，降级为仅 BM25 召回。

类比 Java：
    ``EmbeddingRetriever`` ≈ ``@Service`` 语义检索引擎，
    ``embed_func`` ≈ 依赖注入的 ``EmbeddingProvider`` 接口（策略模式），
    ``EmbeddingIndex`` ≈ 内存态的向量矩阵（``KnnVectorReader`` 快照），
    ``build`` ≈ 异步构建向量索引（``CompletableFuture``），
    ``search`` ≈ 余弦相似度打分 + top-k 选取。
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from agent.langgraph.skills.card import SkillCard
from agent.langgraph.skills.retrievers.bm25 import ScoredCard

logger = logging.getLogger(__name__)

# embed_func 类型：接收文本列表，返回嵌入向量列表（同步或异步）
EmbedFunc = Callable[[list[str]], Any]


@dataclass
class EmbeddingIndex:
    """Embedding 索引（内存态，构建后只读）。

    向量矩阵 ``embeddings`` 的行顺序与 ``card_ids`` 一致，
    即 ``embeddings[i]`` 对应 ``card_ids[i]`` 的 SkillCard。

    Attributes:
        card_ids: 行号 → skill_id 映射
        cards_by_id: skill_id → SkillCard 映射（回填 ScoredCard 用）
        embeddings: 向量矩阵，shape (n_cards, dim)，dtype float32
    """

    card_ids: list[str]
    cards_by_id: dict[str, SkillCard]
    embeddings: np.ndarray  # shape (n_cards, dim), float32


def _build_text(card: SkillCard) -> str:
    """构建 SkillCard 的 Embedding 文本（§7.4.2）。

    模板：``"{name}。{description} 涉及领域：{domains}。意图：{intents}"``
    domains / intents 为列表时以顿号连接。

    Args:
        card: SkillCard 实例

    Returns:
        拼接后的向量化文本
    """
    domains = "、".join(card.domains) if card.domains else ""
    intents = "、".join(card.intents) if card.intents else ""
    return f"{card.name}。{card.description} 涉及领域：{domains}。意图：{intents}"


class EmbeddingRetriever:
    """Embedding 检索器（设计文档 §7.4.2）。

    使用与 RAGFlow 相同的 Embedding 模型（通过 ``embed_func`` 回调注入，
    不直接依赖模型）。索引存储为 ``np.ndarray`` 矩阵，余弦相似度计算。

    构建是异步的：``build`` 期间 ``self._index`` 为 ``None``，
    此时 ``search`` 返回空列表，调用方应降级为仅 BM25 召回。

    使用方式：
        >>> retriever = EmbeddingRetriever()
        >>> index = await retriever.build(cards, embed_func)
        >>> results = retriever.search(query_embedding, top_k=5)

    类比 Java：
        ``EmbeddingRetriever`` ≈ ``@Service`` 语义检索引擎，
        ``embed_func`` ≈ ``@Autowired EmbeddingProvider``（策略模式注入），
        ``build`` ≈ ``@Async`` 异步构建向量索引。
    """

    def __init__(self) -> None:
        self._index: EmbeddingIndex | None = None

    async def build(self, cards: list[SkillCard], embed_func: EmbedFunc) -> EmbeddingIndex:
        """异步构建 Embedding 索引。

        构建期间 ``self._index`` 置为 ``None``，此时 ``search`` 返回空列表，
        调用方应降级为仅 BM25 召回（§7.4.2）。

        ``embed_func`` 支持同步和异步两种形式：
            - 同步：``embed_func(texts) -> list[list[float]]``
            - 异步：``async def embed_func(texts) -> list[list[float]]``

        Args:
            cards: SkillCard 列表
            embed_func: Embedding 回调函数，接收文本列表，返回向量列表

        Returns:
            EmbeddingIndex: 构建完成的索引（同时存储在实例内部供 ``search`` 使用）
        """
        # 构建期间清空旧索引，降级为仅 BM25
        self._index = None

        card_ids: list[str] = [c.skill_id for c in cards]
        cards_by_id: dict[str, SkillCard] = {c.skill_id: c for c in cards}

        if not cards:
            index = EmbeddingIndex(
                card_ids=card_ids,
                cards_by_id=cards_by_id,
                embeddings=np.empty((0, 0), dtype=np.float32),
            )
            self._index = index
            return index

        texts = [_build_text(card) for card in cards]
        # 调用 embed_func，兼容同步/异步回调
        result = embed_func(texts)
        if inspect.isawaitable(result):
            embeddings_list = await result
        else:
            embeddings_list = result

        embeddings = np.array(embeddings_list, dtype=np.float32)
        if embeddings.ndim == 1:
            # 单条文本返回一维向量的兜底处理
            embeddings = embeddings.reshape(1, -1)

        index = EmbeddingIndex(
            card_ids=card_ids,
            cards_by_id=cards_by_id,
            embeddings=embeddings,
        )
        self._index = index
        return index

    def search(self, query_embedding: list[float], top_k: int) -> list[ScoredCard]:
        """Embedding 查询，返回 top_k 结果。

        使用余弦相似度打分。索引未就绪（``build`` 未调用或正在构建）时
        返回空列表，调用方应降级为仅 BM25 召回。

        Args:
            query_embedding: 查询文本的嵌入向量
            top_k: 返回结果数上限

        Returns:
            按分数降序排列的 ScoredCard 列表，``source="embedding"``
        """
        if self._index is None or self._index.embeddings.shape[0] == 0:
            return []

        query_vec = np.array(query_embedding, dtype=np.float32)
        scores = self._cosine_similarity(query_vec, self._index.embeddings)

        # 按分数降序，取 top_k
        ranked_indices = np.argsort(scores)[::-1]
        results: list[ScoredCard] = []
        for row_idx in ranked_indices:
            score = float(scores[row_idx])
            if score <= 0:
                continue
            skill_id = self._index.card_ids[row_idx]
            card = self._index.cards_by_id.get(skill_id)
            if card is None:
                continue
            results.append(ScoredCard(card=card, score=score, source="embedding"))
            if len(results) >= top_k:
                break
        return results

    @staticmethod
    def _cosine_similarity(query_vec: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
        """计算查询向量与所有文档向量的余弦相似度。

        cos(a, b) = (a · b) / (||a|| * ||b||)

        Args:
            query_vec: 查询向量，shape (dim,)
            embeddings: 文档向量矩阵，shape (n_docs, dim)

        Returns:
            相似度数组，shape (n_docs,)
        """
        if embeddings.shape[0] == 0:
            return np.array([], dtype=np.float32)

        query_norm = float(np.linalg.norm(query_vec))
        emb_norms = np.linalg.norm(embeddings, axis=1)

        # 避免除零：零向量的相似度置为 0
        eps = 1e-8
        denom = emb_norms * query_norm
        safe_denom = np.where(denom < eps, eps, denom)
        scores = (embeddings @ query_vec) / safe_denom
        # 零向量（查询或文档）的相似度置为 0
        scores = np.where(denom < eps, 0.0, scores)
        return scores
