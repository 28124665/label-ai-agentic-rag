"""分层召回单元测试（设计文档 v1.1 §10.5-10.6）。

覆盖 ``BM25Recaller`` / ``EmbeddingRecaller`` / ``HybridRecaller``。
"""
from __future__ import annotations

import pytest

from agent.langgraph.skills.catalog_models import SkillCard
from agent.langgraph.skills.recall import (
    BM25Recaller,
    EmbeddingRecaller,
    HybridRecaller,
)


# ========== 辅助构造 ==========


def _make_card(skill_id: str, name: str, description: str, **overrides) -> SkillCard:
    """构造测试用 SkillCard。"""
    defaults = dict(
        skill_id=skill_id,
        version="1.0.0",
        skill_type="report",
        namespace="test.report",
        name=name,
        description=description,
    )
    defaults.update(overrides)
    return SkillCard(**defaults)


def _build_cards() -> list[SkillCard]:
    """构造覆盖不同关键词的 Card 集合。"""
    return [
        _make_card("db_skill", "数据库查询", "database query 数据库访问"),
        _make_card("rag_skill", "知识检索", "rag retrieval 知识库检索"),
        _make_card("report_skill", "数据汇总报告", "数据 统计 汇总 报告"),
    ]


# ========== BM25Recaller ==========


def test_bm25_search_returns_relevant():
    """BM25 按关键词返回相关 Card。"""
    recaller = BM25Recaller(_build_cards())
    results = recaller.search("数据库")
    assert results
    assert results[0].skill_id == "db_skill"
    assert results[0].score > 0


def test_bm25_search_empty_query():
    """空查询返回空结果。"""
    recaller = BM25Recaller(_build_cards())
    assert recaller.search("") == []


def test_bm25_search_no_match():
    """无匹配 token 返回空结果。"""
    recaller = BM25Recaller(_build_cards())
    assert recaller.search("zzznotexist") == []


# ========== EmbeddingRecaller ==========


def test_embedding_search_returns_relevant():
    """TF-IDF 余弦相似度返回相关 Card（用 db_skill 独有的英文 token）。"""
    recaller = EmbeddingRecaller(_build_cards())
    results = recaller.search("database query")
    assert results
    assert results[0].skill_id == "db_skill"
    assert results[0].score > 0


def test_embedding_search_empty_query():
    """空查询返回空结果。"""
    recaller = EmbeddingRecaller(_build_cards())
    assert recaller.search("") == []


# ========== HybridRecaller ==========


def test_hybrid_recall_fuses_scores():
    """fused_score = bm25_score * 0.4 + embedding_score * 0.6。"""
    recaller = HybridRecaller(_build_cards())
    results = recaller.search("数据库")
    assert results
    for r in results:
        expected = r.bm25_score * 0.4 + r.embedding_score * 0.6
        assert r.fused_score == pytest.approx(expected, rel=1e-6)


def test_hybrid_recall_normalizes():
    """归一化后各路分数落在 [0, 1]。"""
    recaller = HybridRecaller(_build_cards())
    results = recaller.search("数据")
    assert results
    for r in results:
        assert 0.0 <= r.bm25_score <= 1.0
        assert 0.0 <= r.embedding_score <= 1.0
        assert 0.0 <= r.fused_score <= 1.0


def test_hybrid_recall_filters_zero_score():
    """fused_score=0 的结果被过滤。"""
    recaller = HybridRecaller(_build_cards())
    results = recaller.search("数据库")
    assert results
    assert all(r.fused_score > 0 for r in results)


def test_hybrid_recall_top_k():
    """返回结果数不超过 top_k。"""
    recaller = HybridRecaller(_build_cards())
    results = recaller.search("数据", top_k=2)
    assert len(results) <= 2


def test_hybrid_get_card():
    """get_card 按 skill_id 返回 SkillCard。"""
    cards = _build_cards()
    recaller = HybridRecaller(cards)
    assert recaller.get_card("db_skill").skill_id == "db_skill"
    assert recaller.get_card("not_exist") is None
