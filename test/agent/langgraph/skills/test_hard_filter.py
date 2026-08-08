"""硬过滤链单元测试（设计文档 v1.1 §10.3）。

覆盖 ``HardFilterChain`` 各层过滤与 ``RuntimeCapabilityGate`` 请求级工具开关。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agent.langgraph.skills.catalog_models import (
    SkillCard,
    SkillGovernance,
    SkillManifest,
)
from agent.langgraph.skills.hard_filter import (
    HardFilterChain,
    HardFilterContext,
    RuntimeCapabilityGate,
)


# ========== 辅助构造 ==========


def _make_card(**overrides) -> SkillCard:
    """构造测试用 SkillCard，默认通过全部硬过滤层。"""
    defaults = dict(
        skill_id="skill_001",
        version="1.0.0",
        skill_type="report",
        namespace="test.report",
        name="测试 Skill",
        description="测试用 Skill",
        capabilities=["report"],
        lifecycle_status="active",
        languages=["zh_CN"],
        dependencies_healthy=True,
    )
    defaults.update(overrides)
    return SkillCard(**defaults)


def _make_snapshot(cards, manifests=None):
    """构造 mock CatalogSnapshot，支持 list_cards / get_card / get_manifest。"""
    snapshot = MagicMock()
    snapshot.list_cards.return_value = list(cards)
    cards_by_id = {c.skill_id: c for c in cards}
    snapshot.get_card.side_effect = lambda sid: cards_by_id.get(sid)
    manifests = manifests or {}
    snapshot.get_manifest.side_effect = lambda sid: manifests.get(sid)
    return snapshot


# ========== 生命周期过滤 ==========


def test_filter_lifecycle_blocks_non_active():
    """deprecated/archived 的 Card 被过滤。"""
    cards = [
        _make_card(skill_id="a", lifecycle_status="active"),
        _make_card(skill_id="b", lifecycle_status="deprecated"),
        _make_card(skill_id="c", lifecycle_status="archived"),
    ]
    chain = HardFilterChain(_make_snapshot(cards))
    kept, reasons = chain._filter_lifecycle(cards)
    assert [c.skill_id for c in kept] == ["a"]
    assert reasons == {"lifecycle": 2}


def test_filter_lifecycle_keeps_active():
    """全部 active 的 Card 通过。"""
    cards = [
        _make_card(skill_id="a"),
        _make_card(skill_id="b"),
    ]
    chain = HardFilterChain(_make_snapshot(cards))
    kept, reasons = chain._filter_lifecycle(cards)
    assert len(kept) == 2
    assert reasons == {}


# ========== 租户过滤 ==========


def test_filter_tenant_overrides_false_blocks():
    """tenant_overrides[tenant]=False 显式禁用。"""
    cards = [_make_card(skill_id="a", tenant_overrides={"t1": False})]
    chain = HardFilterChain(_make_snapshot(cards))
    ctx = HardFilterContext(tenant_id="t1")
    kept, reasons = chain._filter_tenant(cards, ctx)
    assert kept == []
    assert reasons == {"tenant": 1}


def test_filter_tenant_whitelist():
    """allowed_tenants 白名单校验：非白名单租户被过滤，白名单内通过。"""
    cards = [_make_card(skill_id="a", allowed_tenants=["t_allowed"])]
    chain = HardFilterChain(_make_snapshot(cards))

    blocked_ctx = HardFilterContext(tenant_id="t_blocked")
    kept, reasons = chain._filter_tenant(cards, blocked_ctx)
    assert kept == []
    assert reasons == {"tenant": 1}

    allowed_ctx = HardFilterContext(tenant_id="t_allowed")
    kept, reasons = chain._filter_tenant(cards, allowed_ctx)
    assert len(kept) == 1
    assert reasons == {}


def test_filter_dependency_tenant_blocked():
    """required_dependency 的 allowed_tenants 不含当前租户时阻塞。"""
    dep_card = _make_card(skill_id="dep_1", allowed_tenants=["t_allowed"])
    card = _make_card(skill_id="a", required_dependency_ids=["dep_1"])
    snapshot = _make_snapshot([dep_card, card])
    chain = HardFilterChain(snapshot)
    ctx = HardFilterContext(tenant_id="t_blocked")
    kept, reasons = chain._filter_tenant([card], ctx)
    assert kept == []
    assert reasons == {"dependency_tenant": 1}


# ========== 权限过滤 ==========


def test_filter_permission_subset():
    """required_permissions 必须是用户权限的子集。"""
    cards = [_make_card(skill_id="a", required_permissions=["a:read", "b:read"])]
    chain = HardFilterChain(_make_snapshot(cards))

    insufficient = HardFilterContext(tenant_id="t1", permissions=["a:read"])
    kept, reasons = chain._filter_permission(cards, insufficient)
    assert kept == []
    assert reasons == {"permission": 1}

    sufficient = HardFilterContext(tenant_id="t1", permissions=["a:read", "b:read"])
    kept, reasons = chain._filter_permission(cards, sufficient)
    assert len(kept) == 1
    assert reasons == {}


# ========== route capability 过滤 ==========


def test_filter_route_capability_chitchat_blocks_all():
    """chitchat route_target 不需要 Skill，全部过滤。"""
    cards = [_make_card(skill_id="a"), _make_card(skill_id="b")]
    chain = HardFilterChain(_make_snapshot(cards))
    ctx = HardFilterContext(tenant_id="t1", route_target="chitchat")
    kept, reasons = chain._filter_route_capability(cards, ctx)
    assert kept == []
    assert reasons == {"route_capability": 2}


def test_filter_route_capability_match():
    """capabilities 与 route_target 所需能力有交集才保留。"""
    cards = [
        _make_card(skill_id="a", capabilities=["database"]),
        _make_card(skill_id="b", capabilities=["rag"]),
    ]
    chain = HardFilterChain(_make_snapshot(cards))
    ctx = HardFilterContext(tenant_id="t1", route_target="database")
    kept, reasons = chain._filter_route_capability(cards, ctx)
    assert [c.skill_id for c in kept] == ["a"]
    assert reasons == {"route_capability": 1}


# ========== 语言过滤 ==========


def test_filter_language():
    """locale 必须在 languages 中（languages 为空时不限制）。"""
    cards = [
        _make_card(skill_id="a", languages=["en_US"]),
        _make_card(skill_id="b", languages=[]),
        _make_card(skill_id="c", languages=["zh_CN"]),
    ]
    chain = HardFilterChain(_make_snapshot(cards))
    ctx = HardFilterContext(tenant_id="t1", locale="zh_CN")
    kept, reasons = chain._filter_language(cards, ctx)
    assert {c.skill_id for c in kept} == {"b", "c"}
    assert reasons == {"language": 1}


# ========== 依赖健康过滤 ==========


def test_filter_dependency_health():
    """dependencies_healthy=False 被过滤。"""
    cards = [
        _make_card(skill_id="a", dependencies_healthy=True),
        _make_card(skill_id="b", dependencies_healthy=False),
    ]
    chain = HardFilterChain(_make_snapshot(cards))
    kept, reasons = chain._filter_dependency_health(cards)
    assert [c.skill_id for c in kept] == ["a"]
    assert reasons == {"dependency_health": 1}


# ========== RuntimeCapabilityGate ==========


def test_runtime_gate_db_disabled_blocks_database():
    """db_tool 禁用时，依赖 database 能力的 Card 被阻塞。"""
    gate = RuntimeCapabilityGate()
    cards = [_make_card(skill_id="a", capabilities=["database"])]
    kept, reasons = gate.filter(cards, {"db_tool": {"enabled": False}})
    assert kept == []
    assert reasons == {"runtime_db_disabled": 1}


def test_runtime_gate_db_disabled_blocks_data_skill():
    """db_tool 禁用时，data 类型 Skill 整体失效。"""
    gate = RuntimeCapabilityGate()
    cards = [_make_card(skill_id="a", skill_type="data", capabilities=[])]
    kept, reasons = gate.filter(cards, {"db_tool": {"enabled": False}})
    assert kept == []
    assert reasons == {"runtime_db_disabled": 1}


def test_runtime_gate_react_disabled_blocks_react_only():
    """react 禁用时（默认关闭），react_only Card 被阻塞。"""
    gate = RuntimeCapabilityGate()
    cards = [
        _make_card(
            skill_id="a",
            skill_type="retrieval",
            capabilities=["rag"],
            execution_mode="react_only",
        )
    ]
    kept, reasons = gate.filter(cards, {})
    assert kept == []
    assert reasons == {"runtime_react_disabled": 1}


# ========== feature flag 过滤 ==========


def test_filter_feature_flag_no_manifest_passes():
    """get_manifest 返回 None 时降级放行。"""
    cards = [_make_card(skill_id="a")]
    chain = HardFilterChain(_make_snapshot(cards))  # manifests=None
    ctx = HardFilterContext(tenant_id="t1")
    kept, reasons = chain._filter_feature_flag(cards, ctx)
    assert len(kept) == 1
    assert reasons == {}


def test_filter_feature_flag_canary_blocks():
    """canary_tenants 排除非灰度租户。"""
    card = _make_card(skill_id="a")
    manifest = SkillManifest(
        card=card,
        governance=SkillGovernance(canary_tenants=["canary_t"]),
    )
    snapshot = _make_snapshot([card], manifests={"a": manifest})
    chain = HardFilterChain(snapshot)
    ctx = HardFilterContext(tenant_id="normal_t")
    kept, reasons = chain._filter_feature_flag([card], ctx)
    assert kept == []
    assert reasons == {"canary_excluded": 1}


def test_filter_feature_flag_rollout_percentage():
    """rollout_percentage=0 时按概率过滤（hash_val>=0 恒成立）。"""
    card = _make_card(skill_id="a")
    manifest = SkillManifest(
        card=card,
        governance=SkillGovernance(rollout_percentage=0.0),
    )
    snapshot = _make_snapshot([card], manifests={"a": manifest})
    chain = HardFilterChain(snapshot)
    ctx = HardFilterContext(tenant_id="t1")
    kept, reasons = chain._filter_feature_flag([card], ctx)
    assert kept == []
    assert reasons == {"rollout_excluded": 1}
