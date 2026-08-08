"""候选 Skill 依赖选择器单元测试（设计文档 v1.1 §9.7）。"""
from __future__ import annotations

from agent.langgraph.skills.candidate_selector import (
    CandidateDependencySelector,
    CandidateSelectionContext,
)
from agent.langgraph.skills.catalog_models import SkillCard, SkillManifest


# ========== 辅助构造 ==========


def _make_manifest(
    skill_id: str,
    version: str = "1.0.0",
    cost_class: str = "low",
    latency_class: str = "interactive",
    skill_type: str = "data",
) -> SkillManifest:
    """构造测试用 SkillManifest。"""
    card = SkillCard(
        skill_id=skill_id,
        version=version,
        skill_type=skill_type,
        namespace="test.data",
        name=skill_id,
        description=skill_id,
        cost_class=cost_class,
        latency_class=latency_class,
    )
    return SkillManifest(card=card)


# ========== 选择策略测试 ==========


def test_select_empty_returns_none():
    """空候选集返回 None。"""
    selector = CandidateDependencySelector()
    ctx = CandidateSelectionContext(tenant_id="t1")
    assert selector.select([], ctx) is None


def test_select_tenant_preference_hits():
    """preferred_skill_id 命中即选定。"""
    candidates = [
        _make_manifest("a", version="1.0.0"),
        _make_manifest("b", version="2.0.0"),
    ]
    selector = CandidateDependencySelector()
    ctx = CandidateSelectionContext(tenant_id="t1", preferred_skill_id="b")
    chosen = selector.select(candidates, ctx)
    assert chosen is not None
    assert chosen.card.skill_id == "b"


def test_select_cost_filter():
    """preferred_cost_class 过滤候选集。"""
    candidates = [
        _make_manifest("a", cost_class="high"),
        _make_manifest("b", cost_class="low"),
    ]
    selector = CandidateDependencySelector()
    ctx = CandidateSelectionContext(tenant_id="t1", preferred_cost_class="low")
    chosen = selector.select(candidates, ctx)
    assert chosen is not None
    assert chosen.card.cost_class == "low"


def test_select_latency_filter():
    """preferred_latency_class 过滤候选集。"""
    candidates = [
        _make_manifest("a", latency_class="batch"),
        _make_manifest("b", latency_class="interactive"),
    ]
    selector = CandidateDependencySelector()
    ctx = CandidateSelectionContext(
        tenant_id="t1", preferred_latency_class="interactive"
    )
    chosen = selector.select(candidates, ctx)
    assert chosen is not None
    assert chosen.card.latency_class == "interactive"


def test_select_version_constraint():
    """版本约束过滤不满足的候选。"""
    candidates = [
        _make_manifest("a", version="1.0.0"),
        _make_manifest("b", version="2.0.0"),
    ]
    selector = CandidateDependencySelector()
    ctx = CandidateSelectionContext(tenant_id="t1")
    chosen = selector.select(candidates, ctx, constraint=">=1.0,<2.0")
    assert chosen is not None
    assert chosen.card.version == "1.0.0"


def test_select_version_constraint_no_match_keeps_all():
    """版本约束无匹配时保留全部候选。"""
    candidates = [
        _make_manifest("a", version="1.0.0"),
        _make_manifest("b", version="2.0.0"),
    ]
    selector = CandidateDependencySelector()
    ctx = CandidateSelectionContext(tenant_id="t1")
    chosen = selector.select(candidates, ctx, constraint=">=3.0")
    assert chosen is not None
    assert chosen.card.skill_id in {"a", "b"}


def test_select_version_priority():
    """无偏好时取最高版本。"""
    candidates = [
        _make_manifest("a", version="1.0.0"),
        _make_manifest("b", version="2.0.0"),
        _make_manifest("c", version="1.5.0"),
    ]
    selector = CandidateDependencySelector()
    ctx = CandidateSelectionContext(tenant_id="t1")
    chosen = selector.select(candidates, ctx)
    assert chosen is not None
    assert chosen.card.version == "2.0.0"


def test_select_default_first():
    """无偏好且版本/成本/延迟全相同时取首个候选。"""
    candidates = [
        _make_manifest("a", version="1.0.0"),
        _make_manifest("b", version="1.0.0"),
    ]
    selector = CandidateDependencySelector()
    ctx = CandidateSelectionContext(tenant_id="t1")
    chosen = selector.select(candidates, ctx)
    assert chosen is not None
    assert chosen.card.skill_id == "a"
