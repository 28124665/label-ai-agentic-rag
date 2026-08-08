"""Stage 0-1 硬过滤单元测试（P2，§10.3 / §18.3）。

覆盖 HardFilter 的 lifecycle / tenant / permission / runtime capability /
latency_class / exclusion_rules 过滤路径，以及 FilterContext 的构建。
"""
from __future__ import annotations

from agent.langgraph.skills.catalog_models import SkillCard
from agent.langgraph.skills.filters import FilterContext, HardFilter
from agent.langgraph.skills.models import SkillResolveContext


def _make_card(**overrides) -> SkillCard:
    """构造测试用 SkillCard，默认通过全部硬过滤。"""
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


def _make_context(**overrides) -> FilterContext:
    """构造测试用 FilterContext。"""
    defaults = dict(
        tenant_id="t1",
        user_permissions=None,
        agent_config=None,
        route_target="",
        entities=None,
    )
    defaults.update(overrides)
    return FilterContext(**defaults)


# ========== lifecycle 过滤 ==========


def test_lifecycle_filter_active_passes():
    """active lifecycle_status 通过生命周期过滤。"""
    hf = HardFilter()
    card = _make_card(lifecycle_status="active")
    assert hf.check_single(card, _make_context()) is None


def test_lifecycle_filter_deprecated_blocked():
    """deprecated lifecycle_status 被生命周期过滤拦截。"""
    hf = HardFilter()
    card = _make_card(lifecycle_status="deprecated")
    assert hf.check_single(card, _make_context()) == "LIFECYCLE_NOT_SELECTABLE"


# ========== tenant 过滤 ==========


def test_tenant_filter_empty_allowed_passes():
    """空 allowed_tenants 表示允许所有租户。"""
    hf = HardFilter()
    card = _make_card(allowed_tenants=[])
    assert hf.check_single(card, _make_context(tenant_id="any_tenant")) is None


def test_tenant_filter_whitelist():
    """allowed_tenants 白名单仅允许列出的租户。"""
    hf = HardFilter()
    card = _make_card(allowed_tenants=["tenant_a"])
    # 白名单内租户通过
    assert hf.check_single(card, _make_context(tenant_id="tenant_a")) is None
    # 白名单外租户被拦截
    assert (
        hf.check_single(card, _make_context(tenant_id="tenant_b"))
        == "TENANT_NOT_ALLOWED"
    )


# ========== permission 过滤 ==========


def test_permission_filter_subset_passes():
    """用户权限覆盖 Skill 要求权限时通过。"""
    hf = HardFilter()
    card = _make_card(required_permissions=["report:generate"])
    ctx = _make_context(user_permissions=["report:generate", "quality:read"])
    assert hf.check_single(card, ctx) is None


def test_permission_filter_missing_blocked():
    """用户权限缺失时被权限过滤拦截。"""
    hf = HardFilter()
    card = _make_card(required_permissions=["report:generate", "quality:read"])
    ctx = _make_context(user_permissions=["report:generate"])
    assert hf.check_single(card, ctx) == "PERMISSION_DENIED"


# ========== runtime capability 过滤 ==========


def test_runtime_capability_db_disabled():
    """db_tool 禁用时，依赖 database 能力的 Skill 被拦截。"""
    hf = HardFilter()
    card = _make_card(capabilities=["database", "report"])
    ctx = _make_context(agent_config={"db_tool": {"enabled": False}})
    assert hf.check_single(card, ctx) == "CAPABILITY_DISABLED"


def test_runtime_capability_db_enabled():
    """db_tool 启用时，依赖 database 能力的 Skill 通过。"""
    hf = HardFilter()
    card = _make_card(capabilities=["database", "report"])
    ctx = _make_context(agent_config={"db_tool": {"enabled": True}})
    assert hf.check_single(card, ctx) is None


# ========== latency_class 过滤 ==========


def test_latency_interactive_passes():
    """interactive latency_class 在任意 route_target 下通过。"""
    hf = HardFilter()
    card = _make_card(latency_class="interactive")
    for target in ["", "rag", "report", "database"]:
        assert hf.check_single(card, _make_context(route_target=target)) is None


def test_latency_batch_blocked_for_non_report():
    """batch latency_class 在 route_target != 'report' 时被拦截。"""
    hf = HardFilter()
    card = _make_card(latency_class="batch")
    ctx = _make_context(route_target="rag")
    assert hf.check_single(card, ctx) == "LATENCY_MISMATCH"


def test_latency_batch_allowed_for_report():
    """batch latency_class 在 route_target == 'report' 时通过。"""
    hf = HardFilter()
    card = _make_card(latency_class="batch")
    ctx = _make_context(route_target="report")
    assert hf.check_single(card, ctx) is None


# ========== exclusion_rules 过滤 ==========


def test_exclusion_rules_no_rules():
    """空 exclusion_rules 不触发排除。"""
    hf = HardFilter()
    card = _make_card(exclusion_rules=[])
    ctx = _make_context(entities={"factory": "F1"})
    assert hf.check_single(card, ctx) is None


# ========== apply / check_single 批量与单测 ==========


def test_apply_batch_multiple_cards():
    """apply() 批量过滤多个 SkillCard 并记录排除原因。"""
    hf = HardFilter()
    active_card = _make_card(skill_id="active_one")
    deprecated_card = _make_card(
        skill_id="deprecated_one", lifecycle_status="deprecated"
    )
    tenant_blocked_card = _make_card(
        skill_id="tenant_blocked", allowed_tenants=["other_tenant"]
    )
    ctx = _make_context(tenant_id="t1")

    result = hf.apply([active_card, deprecated_card, tenant_blocked_card], ctx)

    assert result.has_candidates is True
    assert len(result.passed) == 1
    assert result.passed[0].skill_id == "active_one"
    assert len(result.excluded) == 2
    reasons = {e.card.skill_id: e.reason for e in result.excluded}
    assert reasons["deprecated_one"] == "LIFECYCLE_NOT_SELECTABLE"
    assert reasons["tenant_blocked"] == "TENANT_NOT_ALLOWED"
    for e in result.excluded:
        assert e.excluded is True


def test_check_single_returns_reason():
    """check_single 对被拦截的 SkillCard 返回拒绝原因字符串。"""
    hf = HardFilter()
    card = _make_card(lifecycle_status="deprecated")
    reason = hf.check_single(card, _make_context())
    assert isinstance(reason, str)
    assert reason == "LIFECYCLE_NOT_SELECTABLE"


def test_check_single_returns_none_for_pass():
    """check_single 对通过的 SkillCard 返回 None。"""
    hf = HardFilter()
    card = _make_card()
    assert hf.check_single(card, _make_context()) is None


# ========== FilterContext.from_resolve_context ==========


def test_filter_context_from_resolve_context():
    """FilterContext.from_resolve_context 从 SkillResolveContext 提取过滤所需字段。"""
    resolve_ctx = SkillResolveContext(
        user_question="质量分析",
        tenant_id="tenant_001",
        agent_config={
            "permissions": ["report:generate"],
            "db_tool": {"enabled": False},
        },
        route_decision={
            "target": "report",
            "metadata": {"entities": {"factory": "F1"}},
        },
    )
    ctx = FilterContext.from_resolve_context(resolve_ctx)
    assert ctx.tenant_id == "tenant_001"
    assert ctx.user_permissions == ["report:generate"]
    assert ctx.agent_config == {
        "permissions": ["report:generate"],
        "db_tool": {"enabled": False},
    }
    assert ctx.route_target == "report"
    assert ctx.entities == {"factory": "F1"}
