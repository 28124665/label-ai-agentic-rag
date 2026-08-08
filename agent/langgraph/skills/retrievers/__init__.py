"""Skill 检索器包（设计文档 §7.4）。

导出 BM25Retriever 和 EmbeddingRetriever 供 Stage 4 混合召回使用。

模块组成：
    - ``bm25``：BM25 关键词召回（rank_bm25，降级为 Counter 实现）
    - ``embedding``：Embedding 语义召回（embed_func 回调注入，NumPy 余弦相似度）

类比 Java：
    本包 ≈ 检索层的 ``@Component`` 集合，
    ``BM25Retriever`` / ``EmbeddingRetriever`` ≈ 两个独立的 ``Strategy`` 实现，
    ``ScoredCard`` ≈ 统一的返回 DTO。
"""

from __future__ import annotations

from agent.langgraph.skills.retrievers.bm25 import BM25Index, BM25Retriever, ScoredCard
from agent.langgraph.skills.retrievers.embedding import EmbeddingIndex, EmbeddingRetriever

__all__ = [
    "BM25Index",
    "BM25Retriever",
    "EmbeddingIndex",
    "EmbeddingRetriever",
    "ScoredCard",
]
