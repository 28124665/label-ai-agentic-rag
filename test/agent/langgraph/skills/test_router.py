"""SkillRouter 主编排器单元测试（P2，§10.2 / §18.3）。

覆盖 Stage 0-6 路由编排：领域规则分类、RRF 融合、强信号匹配、
硬过滤、keyword 降级、Reranker 降级与 select 决策。
"""
from __future__ import annotations

from agent.langgraph.skills.catalog_models import SkillCard
from agent.langgraph.skills.models import SkillResolveContext
from agent.langgraph.skills.reranker import SkillReranker
from agent.langgraph.skills.retrievers import BM25Retriever, ScoredCard
from agent.langgraph.skills.router import (
    SkillRouter,
    classify_domains_by_rule,
    reciprocal_rank_fusion,
)


def _make_card(**overrides) -> SkillCard:
    """构造测试用 SkillCard，默认通过硬过滤且可参与召回。"""
    defaults = dict(
        skill_id="test_skill",
        version="1.0",
        skill_type="report",
        namespace="test.report.test_skill",
        name="Test",
        description="test desc",
        domains=["quality"],
        capabilities=["report"],
        lifecycle_status="active",
        risk_level="medium",
        cost_class="low",
        latency_class="interactive",
    )
    defaults.update(overrides)
    return SkillCard(**defaults)


def _make_context(**overrides) -> SkillResolveContext:
    """构造测试用 SkillResolveContext。"""
    defaults = dict(user_question="质量分析", tenant_id="t1")
    defaults.update(overrides)
    return SkillResolveContext(**defaults)


# ========== classify_domains_by_rule ==========


def test_classify_domains_by_rule_quality():
    """包含质量关键词的 query 命中 quality 领域。"""
    domains = classify_domains_by_rule("请分析质量缺陷")
    assert "quality" in domains
    assert domains[0] == "quality"


def test_classify_domains_by_rule_no_match():
    """无任何领域关键词命中时返回 generic。"""
    domains = classify_domains_by_rule("你好，今天天气怎么样")
    assert domains == ["generic"]


def test_classify_domains_top_3():
    """命中多个领域时返回最多 Top-3。"""
    # 同时命中 quality / production / cost / delivery，每个 1 个关键词
    domains = classify_domains_by_rule("质量产线成本交付")
    assert len(domains) == 3
    assert set(domains) == {"quality", "production", "cost"}


# ========== reciprocal_rank_fusion ==========


def test_reciprocal_rank_fusion_basic():
    """RRF 融合 BM25 与 Embedding 召回结果。"""
    card_a = _make_card(skill_id="a")
    card_b = _make_card(skill_id="b")
    bm25_results = [
        ScoredCard(card=card_a, score=0.9, source="bm25"),
        ScoredCard(card=card_b, score=0.5, source="bm25"),
    ]
    embedding_results = [
        ScoredCard(card=card_b, score=0.8, source="embedding"),
    ]
    fused = reciprocal_rank_fusion(bm25_results, embedding_results, top_k=10)
    assert len(fused) == 2
    assert fused[0].source == "rrf"
    # card_b 同时出现在两路召回，RRF 分数应高于仅 BM25 的 card_a
    assert fused[0].card.skill_id == "b"


def test_reciprocal_rank_fusion_empty():
    """两路召回均为空时融合结果为空。"""
    fused = reciprocal_rank_fusion([], [])
    assert fused == []


def test_reciprocal_rank_fusion_dedup():
    """同一 Skill 在两路召回中均出现时 RRF 分数累加。"""
    card_x = _make_card(skill_id="x")
    bm25_only = [ScoredCard(card=card_x, score=1.0, source="bm25")]
    bm25_both = [ScoredCard(card=card_x, score=1.0, source="bm25")]
    emb_both = [ScoredCard(card=card_x, score=1.0, source="embedding")]

    single = reciprocal_rank_fusion(bm25_only, [], top_k=10)
    combined = reciprocal_rank_fusion(bm25_both, emb_both, top_k=10)

    assert len(single) == 1
    assert len(combined) == 1
    # 双路累加分数应高于单路
    assert combined[0].score > single[0].score


# ========== Stage 0 规范化 ==========


def test_router_empty_query_fails():
    """空 query 直接返回失败。"""
    router = SkillRouter()
    result = router.route(_make_context(user_question=""))
    assert result.resolved is False
    assert result.reason == "EMPTY_QUERY"


# ========== Stage 2 强信号匹配 ==========


def test_router_strong_signal_skill_id():
    """显式 skill_id 强信号匹配直接路由（通过硬过滤）。"""
    card = _make_card(
        skill_id="quality_report", skill_type="report", report_type="quality_analysis"
    )
    router = SkillRouter(cards_by_key={("quality_report", "1.0"): card})
    result = router.route(
        _make_context(user_question="生成报告", skill_id="quality_report")
    )
    assert result.resolved is True
    assert result.reason == "ROUTED:skill_id:quality_report"


def test_router_strong_signal_report_type():
    """report_type 强信号匹配直接路由。"""
    card = _make_card(
        skill_id="quality_report", skill_type="report", report_type="quality_analysis"
    )
    router = SkillRouter(cards_by_key={("quality_report", "1.0"): card})
    result = router.route(
        _make_context(user_question="生成报告", report_type="quality_analysis")
    )
    assert result.resolved is True
    assert result.reason == "ROUTED:skill_id:quality_report"


# ========== Stage 1 硬过滤 ==========


def test_router_no_candidates_after_filter():
    """全部 SkillCard 被硬过滤拦截时返回失败。"""
    deprecated_card = _make_card(skill_id="old_report", lifecycle_status="deprecated")
    router = SkillRouter(cards_by_key={("old_report", "1.0"): deprecated_card})
    result = router.route(_make_context(user_question="生成报告"))
    assert result.resolved is False
    assert result.reason == "NO_CANDIDATES_AFTER_FILTER"


# ========== Stage 4 混合召回 / keyword 降级 ==========


def test_router_keyword_fallback():
    """无 BM25 索引时降级为 keyword 匹配并成功路由。"""
    card = _make_card(
        skill_id="quality_report",
        skill_type="report",
        intents=["质量异常分析", "缺陷报告"],
        domains=["quality"],
    )
    router = SkillRouter(cards_by_key={("quality_report", "1.0"): card})
    result = router.route(_make_context(user_question="质量异常分析"))
    assert result.resolved is True
    assert result.reason.startswith("ROUTED:semantic:")


# ========== Stage 5 Reranker 降级 ==========


def test_router_reranker_degraded():
    """无 llm_call_func 时 Reranker 降级，不调用 LLM 仍完成路由。"""
    llm_called = {"value": False}

    async def mock_llm(prompt: str) -> str:
        llm_called["value"] = True
        return '{"candidates": [], "multi_intent": false}'

    card = _make_card(
        skill_id="quality_report",
        skill_type="report",
        name="质量报告",
        description="质量异常分析报告",
        intents=["质量异常分析"],
        domains=["quality"],
    )
    # 真实 BM25 索引（单文档语料 BM25 可能返回空，router 自动降级到 keyword 匹配）
    bm25 = BM25Retriever()
    bm25.build([card])
    reranker = SkillReranker(llm_call_func=mock_llm)
    router = SkillRouter(
        cards_by_key={("quality_report", "1.0"): card},
        bm25_retriever=bm25,
        reranker=reranker,
    )
    # 不传 llm_call_func → 走降级路径，不调用 LLM
    result = router.route(_make_context(user_question="质量异常分析"))
    assert llm_called["value"] is False
    # 降级路径仍能完成路由（BM25/keyword 召回 + 降级 rerank → select）
    assert result.resolved is True
    assert result.reason.startswith("ROUTED:semantic:")


# ========== Stage 6 决策 ==========


def test_router_select_decision():
    """高分候选触发 select 决策并成功路由。"""
    matching = _make_card(
        skill_id="quality_report",
        skill_type="report",
        intents=["质量异常分析"],
        domains=["quality"],
    )
    other = _make_card(
        skill_id="cost_report",
        skill_type="report",
        intents=["成本费用分析"],
        domains=["cost"],
    )
    router = SkillRouter(
        cards_by_key={
            ("quality_report", "1.0"): matching,
            ("cost_report", "1.0"): other,
        }
    )
    result = router.route(_make_context(user_question="质量异常分析"))
    assert result.resolved is True
    assert "ROUTED:semantic:quality_report" in result.reason


# ========== Stage 3 领域过滤（_filter_by_domain） ==========


def test_router_filter_by_domain_generic_always_kept():
    """generic domain 的 SkillCard 在任意领域过滤后始终保留。"""
    quality_card = _make_card(skill_id="q", domains=["quality"])
    generic_card = _make_card(skill_id="g", domains=["generic"])
    production_card = _make_card(skill_id="p", domains=["production"])
    cards = [quality_card, generic_card, production_card]
    kept = SkillRouter._filter_by_domain(cards, ["quality"])
    kept_ids = {c.skill_id for c in kept}
    assert "q" in kept_ids  # quality 命中
    assert "g" in kept_ids  # generic 兜底始终保留
    assert "p" not in kept_ids  # production 不在 quality 域


def test_router_filter_by_domain_intersection():
    """领域过滤保留 domain 有交集的 SkillCard。"""
    quality_card = _make_card(skill_id="q", domains=["quality"])
    production_card = _make_card(skill_id="p", domains=["production"])
    multi_card = _make_card(skill_id="m", domains=["quality", "production"])
    cards = [quality_card, production_card, multi_card]
    kept = SkillRouter._filter_by_domain(cards, ["quality"])
    kept_ids = {c.skill_id for c in kept}
    assert "q" in kept_ids
    assert "m" in kept_ids  # 多领域交集命中
    assert "p" not in kept_ids
