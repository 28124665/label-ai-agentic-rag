"""Stage 6 决策策略单元测试（P2，§10.8）。

覆盖 DecisionPolicy 的 select / clarify / no_skill / composition 决策路径，
以及 DecisionThresholds 的配置读取与 critical 阈值硬编码约束。
"""
from __future__ import annotations

from agent.langgraph.skills.catalog_models import SkillCard
from agent.langgraph.skills.decision_policy import (
    DecisionPolicy,
    DecisionThresholds,
)
from agent.langgraph.skills.reranker import RankedCandidate, SkillRankDecision


def _make_card(**overrides) -> SkillCard:
    """构造测试用 SkillCard。"""
    defaults = dict(
        skill_id="test_skill",
        version="1.0",
        skill_type="report",
        namespace="test.report.test_skill",
        name="Test",
        description="test desc",
        risk_level="medium",
    )
    defaults.update(overrides)
    return SkillCard(**defaults)


def _make_ranked(skill_id: str, score: float) -> RankedCandidate:
    """构造测试用 RankedCandidate。"""
    return RankedCandidate(skill_id=skill_id, score=score)


# ========== no_skill 决策 ==========


def test_decide_no_candidates_returns_no_skill():
    """候选为空时返回 no_skill 决策。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(candidates=[])
    result = policy.decide(decision, {})
    assert result.decision == "no_skill"
    assert result.reason == "NO_CANDIDATES"
    assert result.selected_skill_id is None


def test_decide_low_score_returns_no_skill():
    """Top1 分数低于 no_skill_confidence（0.40）时返回 no_skill。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(candidates=[_make_ranked("s1", 0.30)])
    candidates_by_id = {"s1": _make_card(skill_id="s1")}
    result = policy.decide(decision, candidates_by_id)
    assert result.decision == "no_skill"
    assert "TOP1_SCORE_BELOW_THRESHOLD" in result.reason


# ========== clarify 决策 ==========


def test_decide_critical_skill_low_score_clarify():
    """critical 风险 Skill 且分数低于 0.85 时返回 clarify。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(candidates=[_make_ranked("s1", 0.80)])
    candidates_by_id = {"s1": _make_card(skill_id="s1", risk_level="critical")}
    result = policy.decide(decision, candidates_by_id)
    assert result.decision == "clarify"
    assert "CRITICAL_SKILL_LOW_CONFIDENCE" in result.reason


def test_decide_below_absolute_threshold_clarify():
    """Top1 分数低于 absolute_threshold（0.75）时返回 clarify。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(candidates=[_make_ranked("s1", 0.60)])
    candidates_by_id = {"s1": _make_card(skill_id="s1", risk_level="medium")}
    result = policy.decide(decision, candidates_by_id)
    assert result.decision == "clarify"
    assert "TOP1_SCORE_BELOW_ABSOLUTE" in result.reason


def test_decide_insufficient_margin_clarify():
    """Top1 与 Top2 分差小于 margin_threshold（0.15）时返回 clarify。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(
        candidates=[_make_ranked("s1", 0.90), _make_ranked("s2", 0.85)]
    )
    candidates_by_id = {
        "s1": _make_card(skill_id="s1"),
        "s2": _make_card(skill_id="s2"),
    }
    result = policy.decide(decision, candidates_by_id)
    assert result.decision == "clarify"
    assert "INSUFFICIENT_MARGIN" in result.reason


def test_decide_ambiguity_reason_clarify():
    """Reranker 标记 ambiguity_reason 时返回 clarify。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(
        candidates=[_make_ranked("s1", 0.90), _make_ranked("s2", 0.60)],
        ambiguity_reason="候选语义重叠",
    )
    candidates_by_id = {
        "s1": _make_card(skill_id="s1"),
        "s2": _make_card(skill_id="s2"),
    }
    result = policy.decide(decision, candidates_by_id)
    assert result.decision == "clarify"
    assert "AMBIGUITY" in result.reason
    assert "候选语义重叠" in result.reason


def test_decide_clarify_includes_candidates():
    """clarify 结果包含 Top-2 候选 SkillCard。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(
        candidates=[_make_ranked("s1", 0.60), _make_ranked("s2", 0.50)]
    )
    card1 = _make_card(skill_id="s1")
    card2 = _make_card(skill_id="s2")
    candidates_by_id = {"s1": card1, "s2": card2}
    result = policy.decide(decision, candidates_by_id)
    assert result.decision == "clarify"
    assert result.clarify_candidates is not None
    assert len(result.clarify_candidates) == 2
    assert result.clarify_candidates[0].skill_id == "s1"
    assert result.clarify_candidates[1].skill_id == "s2"


# ========== composition / select 决策 ==========


def test_decide_multi_intent_composition():
    """multi_intent=True 且有 2 个候选时返回 composition。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(
        candidates=[_make_ranked("s1", 0.90), _make_ranked("s2", 0.70)],
        multi_intent=True,
    )
    candidates_by_id = {
        "s1": _make_card(skill_id="s1"),
        "s2": _make_card(skill_id="s2"),
    }
    result = policy.decide(decision, candidates_by_id)
    assert result.decision == "composition"
    assert result.selected_skill_id == "s1"
    assert result.selected_card is not None
    assert result.selected_card.skill_id == "s1"
    assert result.reason == "MULTI_INTENT_COMPOSITION"


def test_decide_select_top1():
    """分数充足、分差足够、无歧义时返回 select。"""
    policy = DecisionPolicy()
    decision = SkillRankDecision(
        candidates=[_make_ranked("s1", 0.90), _make_ranked("s2", 0.60)]
    )
    candidates_by_id = {
        "s1": _make_card(skill_id="s1"),
        "s2": _make_card(skill_id="s2"),
    }
    result = policy.decide(decision, candidates_by_id)
    assert result.decision == "select"
    assert result.selected_skill_id == "s1"
    assert result.selected_card.skill_id == "s1"
    assert "SELECTED" in result.reason


# ========== DecisionThresholds 配置 ==========


def test_thresholds_from_config():
    """DecisionThresholds.from_config 从 skill_router 配置读取阈值。"""
    config = {
        "skill_router": {
            "absolute_threshold": 0.80,
            "margin_threshold": 0.20,
            "composition_confidence": 0.65,
            "no_skill_confidence": 0.50,
        }
    }
    thresholds = DecisionThresholds.from_config(config)
    assert thresholds.absolute_threshold == 0.80
    assert thresholds.margin_threshold == 0.20
    assert thresholds.composition_confidence == 0.65
    assert thresholds.no_skill_confidence == 0.50


def test_thresholds_critical_not_overridable():
    """critical_absolute_threshold 硬编码，不可被配置覆盖。"""
    config = {"skill_router": {"critical_absolute_threshold": 0.50}}
    thresholds = DecisionThresholds.from_config(config)
    assert thresholds.critical_absolute_threshold == 0.85
