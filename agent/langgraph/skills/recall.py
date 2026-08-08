#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""分层召回（设计文档 v1.1 §10.5-10.6）。

两路召回 + 融合：
    ``BM25Recaller`` — 基于关键词的 BM25 召回（纯 Python，无外部依赖）
    ``EmbeddingRecaller`` — 语义召回（默认 TF-IDF 余弦相似度 fallback，
        后续可替换为真实 Embedding 模型）
    ``HybridRecaller`` — 融合 BM25 + Embedding 分数（加权 0.4/0.6）

类比 Java 中的策略模式：
    ``Recaller`` 接口 ≈ ``Strategy`` 接口
    ``BM25Recaller`` / ``EmbeddingRecaller`` ≈ ``ConcreteStrategy``
    ``HybridRecaller`` ≈ 组合多个 Strategy 的 ``CompositeStrategy``
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from agent.langgraph.skills.catalog_models import SkillCard

logger = logging.getLogger(__name__)

# BM25 参数
_BM25_K1 = 1.5
_BM25_B = 0.75

# 混合召回权重
_BM25_WEIGHT = 0.4
_EMBEDDING_WEIGHT = 0.6


@dataclass
class RecallResult:
    """单路召回结果。"""

    skill_id: str
    score: float


@dataclass
class HybridRecallResult:
    """混合召回结果（含各路分数，供 Reranker 和 trace 使用）。"""

    skill_id: str
    fused_score: float
    bm25_score: float = 0.0
    embedding_score: float = 0.0


def _tokenize(text: str) -> list[str]:
    """简单分词：中文按字 + 英文按词 + 数字。

    生产环境建议替换为 jieba 等分词器，但为保证零依赖先用简单实现。
    """
    if not text:
        return []
    tokens: list[str] = []
    # 英文单词 + 数字
    for match in re.findall(r"[a-zA-Z]+|\d+", text):
        tokens.append(match.casefold())
    # 中文字符（逐字）
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            tokens.append(char)
    return tokens


def _build_search_text(card: SkillCard) -> str:
    """构建 SkillCard 的检索文本（设计文档 §10.5）。

    拼接 name + description + aliases + positive_examples + intents，
    作为 BM25 和 Embedding 的索引源。
    """
    parts = [
        card.name,
        card.description,
        " ".join(card.aliases),
        " ".join(card.intents),
        " ".join(card.positive_examples),
    ]
    return " ".join(p for p in parts if p)


class BM25Recaller:
    """BM25 召回器（设计文档 §10.5）。

    纯 Python 实现 BM25 算法，无外部依赖。
    索引在 Catalog 构建时预计算，查询时只做打分。

    类比 Java 中的 ``@Component`` 检索引擎：
        ``BM25Recaller`` ≈ Lucene 的 ``BM25Similarity``，但简化为内存版。
    """

    def __init__(self, cards: list[SkillCard]) -> None:
        self._docs: dict[str, list[str]] = {}
        self._doc_len: dict[str, int] = {}
        self._avg_doc_len: float = 0.0
        self._df: dict[str, int] = {}  # document frequency
        self._tf: dict[str, dict[str, int]] = {}  # term frequency per doc
        self._n_docs: int = 0
        self._build_index(cards)

    def _build_index(self, cards: list[SkillCard]) -> None:
        """构建 BM25 索引。"""
        total_len = 0
        for card in cards:
            text = _build_search_text(card)
            tokens = _tokenize(text)
            self._docs[card.skill_id] = tokens
            self._doc_len[card.skill_id] = len(tokens)
            total_len += len(tokens)

            tf = Counter(tokens)
            self._tf[card.skill_id] = dict(tf)
            for term in tf:
                self._df[term] = self._df.get(term, 0) + 1

        self._n_docs = len(cards)
        self._avg_doc_len = total_len / max(self._n_docs, 1)

    def search(self, query: str, top_k: int = 20) -> list[RecallResult]:
        """BM25 搜索，返回 top_k 结果。"""
        query_tokens = _tokenize(query)
        if not query_tokens or self._n_docs == 0:
            return []

        results: list[RecallResult] = []
        for skill_id, doc_tokens in self._docs.items():
            score = self._score(query_tokens, skill_id)
            if score > 0:
                results.append(RecallResult(skill_id=skill_id, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _score(self, query_tokens: list[str], skill_id: str) -> float:
        """计算单文档 BM25 分数。"""
        doc_len = self._doc_len.get(skill_id, 0)
        tf_doc = self._tf.get(skill_id, {})
        score = 0.0

        for term in query_tokens:
            tf = tf_doc.get(term, 0)
            if tf == 0:
                continue
            df = self._df.get(term, 0)
            # IDF
            idf = math.log(1 + (self._n_docs - df + 0.5) / (df + 0.5))
            # TF 归一化
            tf_norm = (tf * (_BM25_K1 + 1)) / (
                tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / max(self._avg_doc_len, 1))
            )
            score += idf * tf_norm
        return score


class EmbeddingRecaller:
    """语义召回器（设计文档 §10.6）。

    默认使用 TF-IDF 余弦相似度作为 Embedding fallback，无需外部 embedding 服务。
    后续可替换为真实 Embedding 模型（如 Sentence-BERT），只需实现 ``_embed`` 方法。

    类比 Java 中的策略模式 + 模板方法：
        ``EmbeddingRecaller`` 定义召回骨架，``_embed`` 是可变步骤（模板方法），
        当前实现用 TF-IDF，后续可替换为 ``SentenceBERTEmbedder``。
    """

    def __init__(self, cards: list[SkillCard]) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._idf: dict[str, float] = {}
        self._vocab: dict[str, int] = {}
        self._build_index(cards)

    def _build_index(self, cards: list[SkillCard]) -> None:
        """构建 TF-IDF 向量索引。"""
        # 构建 vocab + DF
        doc_tokens: dict[str, list[str]] = {}
        df: dict[str, int] = {}
        for card in cards:
            text = _build_search_text(card)
            tokens = _tokenize(text)
            doc_tokens[card.skill_id] = tokens
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1

        n_docs = max(len(cards), 1)
        self._vocab = {term: idx for idx, term in enumerate(sorted(df.keys()))}
        self._idf = {
            term: math.log(1 + n_docs / max(df_term, 1))
            for term, df_term in df.items()
        }

        # 构建 TF-IDF 向量
        vocab_size = len(self._vocab)
        for skill_id, tokens in doc_tokens.items():
            vec = [0.0] * vocab_size
            tf = Counter(tokens)
            for term, count in tf.items():
                idx = self._vocab.get(term)
                if idx is not None:
                    vec[idx] = count * self._idf.get(term, 0.0)
            # L2 归一化
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            self._vectors[skill_id] = vec

    def search(self, query: str, top_k: int = 20) -> list[RecallResult]:
        """语义搜索，返回 top_k 结果。"""
        query_vec = self._embed(query)
        if not query_vec or not self._vectors:
            return []

        results: list[RecallResult] = []
        for skill_id, doc_vec in self._vectors.items():
            score = self._cosine(query_vec, doc_vec)
            if score > 0:
                results.append(RecallResult(skill_id=skill_id, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def _embed(self, text: str) -> list[float]:
        """文本向量化（TF-IDF fallback）。

        后续可替换为真实 Embedding 模型：
            ``return sentence_bert.encode(text)``
        """
        tokens = _tokenize(text)
        if not tokens:
            return []
        vec = [0.0] * len(self._vocab)
        tf = Counter(tokens)
        for term, count in tf.items():
            idx = self._vocab.get(term)
            if idx is not None:
                vec[idx] = count * self._idf.get(term, 0.0)
        # L2 归一化
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    @staticmethod
    def _cosine(vec_a: list[float], vec_b: list[float]) -> float:
        """余弦相似度（向量已 L2 归一化，点积即余弦）。"""
        if len(vec_a) != len(vec_b):
            return 0.0
        return sum(a * b for a, b in zip(vec_a, vec_b))


class HybridRecaller:
    """混合召回器（设计文档 §10.5-10.6 融合）。

    融合 BM25 + Embedding 两路召回结果，加权得分：
        fused_score = bm25_score * 0.4 + embedding_score * 0.6

    分数归一化：各路分数先 min-max 归一化到 [0, 1]，再加权融合。

    类比 Java 中的组合模式 + 结果融合：
        ``HybridRecaller`` ≈ ``CompositeRecaller``，
        融合策略类似 Elasticsearch 的 ``function_score`` 多函数组合。
    """

    def __init__(self, cards: list[SkillCard]) -> None:
        self._bm25 = BM25Recaller(cards)
        self._embedding = EmbeddingRecaller(cards)
        self._cards_by_id: dict[str, SkillCard] = {c.skill_id: c for c in cards}

    def search(
        self,
        query: str,
        top_k: int = 20,
        bm25_weight: float = _BM25_WEIGHT,
        embedding_weight: float = _EMBEDDING_WEIGHT,
    ) -> list[HybridRecallResult]:
        """混合召回，返回 top_k 融合结果。"""
        bm25_results = self._bm25.search(query, top_k=top_k * 2)
        embedding_results = self._embedding.search(query, top_k=top_k * 2)

        # 归一化各路分数
        bm25_scores = self._normalize({r.skill_id: r.score for r in bm25_results})
        embedding_scores = self._normalize({r.skill_id: r.score for r in embedding_results})

        # 融合
        all_ids = set(bm25_scores.keys()) | set(embedding_scores.keys())
        fused: list[HybridRecallResult] = []
        for skill_id in all_ids:
            bm25_s = bm25_scores.get(skill_id, 0.0)
            emb_s = embedding_scores.get(skill_id, 0.0)
            fused_score = bm25_s * bm25_weight + emb_s * embedding_weight
            fused.append(HybridRecallResult(
                skill_id=skill_id,
                fused_score=fused_score,
                bm25_score=bm25_s,
                embedding_score=emb_s,
            ))

        fused.sort(key=lambda r: r.fused_score, reverse=True)

        # 过滤 fused_score=0 的结果（两路都未命中）
        fused = [r for r in fused if r.fused_score > 0]

        return fused[:top_k]

    @staticmethod
    def _normalize(scores: dict[str, float]) -> dict[str, float]:
        """min-max 归一化到 [0, 1]。"""
        if not scores:
            return {}
        max_score = max(scores.values())
        min_score = min(scores.values())
        if max_score == min_score:
            return {k: 1.0 for k in scores}
        return {k: (v - min_score) / (max_score - min_score) for k, v in scores.items()}

    def get_card(self, skill_id: str) -> SkillCard | None:
        """获取 SkillCard（Reranker 需要）。"""
        return self._cards_by_id.get(skill_id)
