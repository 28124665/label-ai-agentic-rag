"""BM25 检索器（设计文档 §7.4.1）。

基于 ``rank_bm25.BM25Okapi`` 的关键词召回，支持中英文混合分词。
``rank_bm25`` 未安装时降级为 ``Counter`` 实现的简单关键词匹配。

文档构建规则（§7.4.1）：
    每个 SkillCard 生成一篇 BM25 文档，字段拼接顺序：
    aliases → name → intents → positive_examples → description，空字段跳过。

分词规则（§7.4.1）：
    - 中文用 jieba 分词（未安装时降级为单字分词）
    - 英文按空格+小写化
    - 混合文本中英分别分词后合并

类比 Java：
    ``BM25Retriever`` ≈ Lucene 的 ``IndexSearcher`` + ``BM25Similarity``，
    ``BM25Index`` ≈ 内存态的倒排索引（``DirectoryReader`` 快照），
    ``build`` ≈ ``IndexWriter.commit`` 构建不可变快照，
    ``search`` ≈ ``IndexSearcher.search`` 在快照上打分。
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agent.langgraph.skills.card import SkillCard

logger = logging.getLogger(__name__)

# BM25 参数（§7.4.1）
_BM25_K1 = 1.5
_BM25_B = 0.75

# 尝试导入 rank_bm25，未安装时降级为 Counter 实现
try:
    from rank_bm25 import BM25Okapi

    _HAS_RANK_BM25 = True
except ImportError:  # pragma: no cover
    _HAS_RANK_BM25 = False
    logger.warning("rank_bm25 not installed, BM25Retriever falls back to Counter-based keyword matching")

# 尝试导入 jieba，未安装时中文降级为单字分词
try:
    import jieba

    _HAS_JIEBA = True
except ImportError:  # pragma: no cover
    _HAS_JIEBA = False
    logger.warning("jieba not installed, Chinese tokenization falls back to character-level splitting")


# ========== 数据结构 ==========


@dataclass
class ScoredCard:
    """带分数的 SkillCard 检索结果（§7.4 通用返回结构）。

    Attributes:
        card: 匹配到的 SkillCard
        score: 相似度/相关性分数（越大越相关）
        source: 分数来源标识，"bm25" / "embedding" / "rrf"
    """

    card: SkillCard
    score: float
    source: str  # "bm25" / "embedding" / "rrf"


@dataclass
class BM25Index:
    """BM25 索引（内存态，构建后只读）。

    行号 → skill_id 的映射保存在 ``card_ids`` 列表中，
    ``rank_bm25.BM25Okapi`` 的文档顺序与 ``card_ids`` 一致。

    Attributes:
        card_ids: 行号 → skill_id 映射（card_ids[i] 对应第 i 篇文档）
        cards_by_id: skill_id → SkillCard 映射（回填 ScoredCard 用）
        docs: 分词后的文档列表（与 card_ids 同序）
        backend: rank_bm25.BM25Okapi 实例；降级模式为 None
        tf_list: 降级模式 —— 每篇文档的词频字典
        df: 降级模式 —— 词 → 文档频率
        doc_len_list: 降级模式 —— 每篇文档长度
        avg_doc_len: 降级模式 —— 平均文档长度
        n_docs: 文档总数
    """

    card_ids: list[str]
    cards_by_id: dict[str, SkillCard]
    docs: list[list[str]]
    backend: Any  # rank_bm25.BM25Okapi | None
    tf_list: list[dict[str, int]] = field(default_factory=list)
    df: dict[str, int] = field(default_factory=dict)
    doc_len_list: list[int] = field(default_factory=list)
    avg_doc_len: float = 0.0
    n_docs: int = 0


# ========== 分词与文档构建 ==========


def _tokenize(text: str) -> list[str]:
    """中英文混合分词（§7.4.1）。

    中文用 jieba 分词，英文按空格+小写化，混合文本中英分别分词后合并。
    jieba 未安装时中文降级为单字分词。

    Args:
        text: 待分词文本

    Returns:
        分词后的 token 列表
    """
    if not text:
        return []
    tokens: list[str] = []
    # 正则分离中文段和英文/数字段
    for segment in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9]+", text):
        if "\u4e00" <= segment[0] <= "\u9fff":
            # 中文段：jieba 分词，降级为单字
            if _HAS_JIEBA:
                tokens.extend(t for t in jieba.lcut(segment) if t.strip())
            else:
                tokens.extend(list(segment))
        else:
            # 英文/数字段：整体小写化作为一个 token
            tokens.append(segment.casefold())
    return tokens


def _build_doc(card: SkillCard) -> str:
    """构建 SkillCard 的 BM25 文档文本（§7.4.1）。

    字段拼接顺序：aliases → name → intents → positive_examples → description，
    空字段跳过。

    Args:
        card: SkillCard 实例

    Returns:
        拼接后的文档文本
    """
    parts: list[str] = []
    if card.aliases:
        parts.extend(card.aliases)
    if card.name:
        parts.append(card.name)
    if card.intents:
        parts.extend(card.intents)
    if card.positive_examples:
        parts.extend(card.positive_examples)
    if card.description:
        parts.append(card.description)
    return " ".join(parts)


# ========== BM25Retriever ==========


class BM25Retriever:
    """BM25 检索器（设计文档 §7.4.1）。

    ``rank_bm25`` 可用时使用 ``BM25Okapi``，否则降级为 ``Counter`` 实现的
    简单关键词匹配。索引存储在内存，行号 → skill_id 映射保存在 ``card_ids`` 列表。

    使用方式：
        >>> retriever = BM25Retriever()
        >>> index = retriever.build(cards)
        >>> results = retriever.search("质量异常分析", top_k=5)

    类比 Java：
        ``BM25Retriever`` ≈ ``@Service`` 检索引擎，
        ``build`` ≈ ``@PostConstruct`` 构建索引快照，
        ``search`` ≈ 在快照上执行 ``IndexSearcher.search``。
    """

    def __init__(self) -> None:
        self._index: BM25Index | None = None

    def build(self, cards: list[SkillCard]) -> BM25Index:
        """构建 BM25 索引。

        每个 SkillCard 生成一篇文档，按 §7.4.1 规则拼接字段并分词。
        ``rank_bm25`` 可用时构建 ``BM25Okapi`` 实例，否则构建 Counter 索引。

        Args:
            cards: SkillCard 列表

        Returns:
            BM25Index: 构建完成的索引（同时存储在实例内部供 ``search`` 使用）
        """
        card_ids: list[str] = []
        cards_by_id: dict[str, SkillCard] = {}
        docs: list[list[str]] = []

        for card in cards:
            card_ids.append(card.skill_id)
            cards_by_id[card.skill_id] = card
            docs.append(_tokenize(_build_doc(card)))

        n_docs = len(cards)
        avg_doc_len = sum(len(d) for d in docs) / max(n_docs, 1)

        # 空语料直接返回空索引，避免 BM25Okapi 除零
        if n_docs == 0:
            index = BM25Index(
                card_ids=card_ids,
                cards_by_id=cards_by_id,
                docs=docs,
                backend=None,
                avg_doc_len=avg_doc_len,
                n_docs=n_docs,
            )
            self._index = index
            return index

        if _HAS_RANK_BM25:
            # rank_bm25 模式：BM25Okapi 内部维护倒排索引
            backend: Any = BM25Okapi(docs, k1=_BM25_K1, b=_BM25_B)
            index = BM25Index(
                card_ids=card_ids,
                cards_by_id=cards_by_id,
                docs=docs,
                backend=backend,
                avg_doc_len=avg_doc_len,
                n_docs=n_docs,
            )
        else:
            # 降级模式：用 Counter 构建 tf/df 索引
            tf_list: list[dict[str, int]] = []
            df: dict[str, int] = {}
            doc_len_list: list[int] = []
            for doc in docs:
                tf = Counter(doc)
                tf_list.append(dict(tf))
                doc_len_list.append(len(doc))
                for term in tf:
                    df[term] = df.get(term, 0) + 1
            index = BM25Index(
                card_ids=card_ids,
                cards_by_id=cards_by_id,
                docs=docs,
                backend=None,
                tf_list=tf_list,
                df=df,
                doc_len_list=doc_len_list,
                avg_doc_len=avg_doc_len,
                n_docs=n_docs,
            )

        self._index = index
        return index

    def search(self, query: str, top_k: int) -> list[ScoredCard]:
        """BM25 查询，返回 top_k 结果。

        Args:
            query: 查询文本
            top_k: 返回结果数上限

        Returns:
            按分数降序排列的 ScoredCard 列表，``source="bm25"``
        """
        if self._index is None:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens or self._index.n_docs == 0:
            return []

        scores = self._score_all(query_tokens)
        # 按分数降序，取 top_k，过滤零分结果
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results: list[ScoredCard] = []
        for row_idx, score in ranked:
            if score <= 0:
                continue
            skill_id = self._index.card_ids[row_idx]
            card = self._index.cards_by_id.get(skill_id)
            if card is None:
                continue
            results.append(ScoredCard(card=card, score=float(score), source="bm25"))
            if len(results) >= top_k:
                break
        return results

    def _score_all(self, query_tokens: list[str]) -> list[float]:
        """对所有文档打分。

        ``rank_bm25`` 模式调用 ``backend.get_scores``，
        降级模式手动计算 BM25 分数。
        """
        index = self._index
        assert index is not None

        if index.backend is not None:
            return list(index.backend.get_scores(query_tokens))

        # 降级模式：手动 BM25 打分
        return [self._fallback_bm25_score(query_tokens, row_idx) for row_idx in range(index.n_docs)]

    def _fallback_bm25_score(self, query_tokens: list[str], row_idx: int) -> float:
        """降级模式 BM25 打分（Counter 实现）。

        公式与 rank_bm25.BM25Okapi 一致：
            score = Σ IDF(q) * TF_norm(q, d)
            IDF(q) = log((N - df + 0.5) / (df + 0.5) + 1)
            TF_norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * |d| / avg_dl))
        """
        index = self._index
        assert index is not None

        tf_doc = index.tf_list[row_idx]
        doc_len = index.doc_len_list[row_idx]
        avg_dl = max(index.avg_doc_len, 1.0)
        n_docs = index.n_docs

        score = 0.0
        for term in query_tokens:
            tf = tf_doc.get(term, 0)
            if tf == 0:
                continue
            df = index.df.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            tf_norm = (tf * (_BM25_K1 + 1)) / (tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / avg_dl))
            score += idf * tf_norm
        return score
