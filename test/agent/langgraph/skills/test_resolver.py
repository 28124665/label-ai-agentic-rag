"""Skill resolver tests."""
from __future__ import annotations

from agent.langgraph.skills.models import SkillResolveContext
from agent.langgraph.skills.registry import SkillRegistry
from agent.langgraph.skills.resolver import SkillResolver


def test_resolves_quality_report_by_explicit_skill_id():
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(
        SkillResolveContext(user_question="生成质量报告", tenant_id="tenant_001", skill_id="quality_report")
    )

    assert result.resolved is True
    assert result.skill_set.report_skill.skill_id == "quality_report"
    assert result.skill_set.data_skill.skill_id == "quality_data_access"
    assert result.skill_set.retrieval_skill.skill_id == "quality_knowledge_retrieval"
    assert result.skill_set.resolution_source == "skill_id"


def test_resolves_quality_report_by_report_type():
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(
        SkillResolveContext(
            user_question="生成报告", tenant_id="tenant_001", report_type="quality_analysis"
        )
    )

    assert result.resolved is True
    assert result.skill_set.report_skill.skill_id == "quality_report"
    assert result.skill_set.resolution_source == "report_type"


def test_resolves_quality_report_by_keyword():
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(SkillResolveContext(user_question="请分析质量异常", tenant_id="tenant_001"))

    assert result.resolved is True
    assert result.skill_set.report_skill.skill_id == "quality_report"
    assert result.skill_set.resolution_source == "keyword"


def test_returns_not_found_for_unknown_explicit_skill_id():
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(
        SkillResolveContext(
            user_question="生成报告", tenant_id="tenant_001", skill_id="unknown_skill"
        )
    )

    assert result.resolved is False
    assert result.skill_set is None
    assert result.reason == "SKILL_NOT_FOUND"


def test_returns_not_found_for_blank_explicit_skill_id():
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(
        SkillResolveContext(user_question="生成报告", tenant_id="tenant_001", skill_id="   ")
    )

    assert result.resolved is False
    assert result.reason == "SKILL_NOT_FOUND"


def test_returns_permission_denied_when_required_permission_missing():
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(
        SkillResolveContext(
            user_question="生成质量报告",
            tenant_id="tenant_001",
            skill_id="quality_report",
            agent_config={"permissions": ["report:generate"]},
        )
    )

    assert result.resolved is False
    assert result.reason == "SKILL_PERMISSION_DENIED"


def test_uses_generic_analysis_as_fallback():
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(SkillResolveContext(user_question="帮我分析", tenant_id="tenant_001"))

    assert result.resolved is True
    assert result.fallback_used is True
    assert result.skill_set.report_skill.skill_id == "generic_analysis"


# ========== P0 新增测试（§18.1 交付：完整返回路径和短路路径测试）==========


def test_tenant_whitelist_denies_non_allowed_tenant():
    """§10.3 tenant 过滤层：allowed_tenants 非空时，非白名单租户被拒绝。"""
    resolver = SkillResolver(SkillRegistry())
    # 临时给 quality_report 设置租户白名单（仅 tenant_allowed 可用）
    quality_skill = resolver.registry.get_by_id("quality_report")
    assert quality_skill is not None
    original_tenants = quality_skill.allowed_tenants
    quality_skill.allowed_tenants = ["tenant_allowed"]

    try:
        result = resolver.resolve(
            SkillResolveContext(
                user_question="生成质量报告",
                tenant_id="tenant_blocked",
                skill_id="quality_report",
            )
        )
        assert result.resolved is False
        assert result.reason == "SKILL_PERMISSION_DENIED"
    finally:
        quality_skill.allowed_tenants = original_tenants


def test_tenant_whitelist_allows_listed_tenant():
    """§10.3 tenant 过滤层：allowed_tenants 非空时，白名单内租户正常通过。"""
    resolver = SkillResolver(SkillRegistry())
    quality_skill = resolver.registry.get_by_id("quality_report")
    assert quality_skill is not None
    original_tenants = quality_skill.allowed_tenants
    quality_skill.allowed_tenants = ["tenant_allowed"]

    try:
        result = resolver.resolve(
            SkillResolveContext(
                user_question="生成质量报告",
                tenant_id="tenant_allowed",
                skill_id="quality_report",
            )
        )
        assert result.resolved is True
        assert result.skill_set.report_skill.skill_id == "quality_report"
    finally:
        quality_skill.allowed_tenants = original_tenants


def test_runtime_capability_gate_db_tool_disabled_blocks_skill_with_data_dependency():
    """§10.3.1 runtime capability gate：db_tool 禁用时，有 DataSkill 依赖的 Skill 不可用。"""
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(
        SkillResolveContext(
            user_question="生成质量报告",
            tenant_id="tenant_001",
            skill_id="quality_report",
            agent_config={"db_tool": {"enabled": False}},
        )
    )

    assert result.resolved is False
    assert result.reason == "SKILL_PERMISSION_DENIED"


def test_runtime_capability_gate_db_tool_disabled_allows_skill_without_data_dependency():
    """§10.3.1 runtime capability gate：db_tool 禁用时，无 DataSkill 依赖的 Skill 仍可用。"""
    resolver = SkillResolver(SkillRegistry())

    # generic_analysis 无 DataSkill 依赖，db_tool 禁用时仍可用
    result = resolver.resolve(
        SkillResolveContext(
            user_question="帮我分析",
            tenant_id="tenant_001",
            skill_id="generic_analysis",
            agent_config={"db_tool": {"enabled": False}},
        )
    )

    assert result.resolved is True
    assert result.skill_set.report_skill.skill_id == "generic_analysis"


def test_keyword_skips_unauthorized_top_candidate_and_falls_back():
    """§18.1 核心交付：无权限最高分候选不阻塞后续合法候选。

    场景：用户问题含"质量缺陷"（仅匹配 quality_report），但用户缺少 quality:read 权限。
    keyword 路径应跳过 quality_report，无其他候选命中，最终 fallback 到 generic_analysis。
    注意：不用"质量异常"，因为"异常"同时匹配 factory_performance。
    """
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(
        SkillResolveContext(
            user_question="请分析质量缺陷",
            tenant_id="tenant_001",
            agent_config={"permissions": ["report:generate"]},  # 缺少 quality:read
        )
    )

    # quality_report 需 [report:generate, quality:read]，用户只有 report:generate
    # keyword 路径应跳过 quality_report，无其他 keyword 命中，最终 fallback
    assert result.resolved is True
    assert result.fallback_used is True
    assert result.skill_set.report_skill.skill_id == "generic_analysis"


def test_dependency_permission_denied_clears_data_skill_with_warning():
    """§12.2 约束1：依赖级权限校验——DataSkill 权限不足时 data_skill 置 None + warnings。"""
    resolver = SkillResolver(SkillRegistry())

    quality_skill = resolver.registry.get_by_id("quality_report")
    data_skill = resolver.registry.get_data_for_report("quality_report")
    assert quality_skill is not None
    assert data_skill is not None

    # 临时调整权限要求：
    # - ReportSkill 只需 report:generate（用户有）
    # - DataSkill 需要 quality:read（用户没有）
    original_report_perms = quality_skill.required_permissions
    original_data_perms = data_skill.required_permissions
    quality_skill.required_permissions = ["report:generate"]
    data_skill.required_permissions = ["quality:read"]
    try:
        result = resolver.resolve(
            SkillResolveContext(
                user_question="生成质量报告",
                tenant_id="tenant_001",
                skill_id="quality_report",
                agent_config={"permissions": ["report:generate"]},  # 缺少 quality:read
            )
        )
        # ReportSkill 通过（只需 report:generate），但 DataSkill 权限不足
        assert result.resolved is True
        assert result.skill_set.data_skill is None
        assert any("permission denied" in w for w in result.warnings)
    finally:
        quality_skill.required_permissions = original_report_perms
        data_skill.required_permissions = original_data_perms


def test_disabled_skill_excluded_from_keyword_candidates():
    """§12.1 lifecycle 过滤：enabled=False 的 Skill 不进入 keyword 候选。"""
    resolver = SkillResolver(SkillRegistry())
    quality_skill = resolver.registry.get_by_id("quality_report")
    assert quality_skill is not None
    original_enabled = quality_skill.enabled
    quality_skill.enabled = False

    try:
        result = resolver.resolve(
            SkillResolveContext(
                user_question="请分析质量缺陷",
                tenant_id="tenant_001",
            )
        )
        # quality_report 被 disabled，keyword 不命中，fallback 到 generic_analysis
        # 注意：不用"质量异常"，因为"异常"同时匹配 factory_performance
        assert result.resolved is True
        assert result.fallback_used is True
        assert result.skill_set.report_skill.skill_id == "generic_analysis"
    finally:
        quality_skill.enabled = original_enabled


def test_explicit_skill_id_with_full_permissions_resolves():
    """§10.3 显式 skill_id 路径：权限完整时正常解析。"""
    resolver = SkillResolver(SkillRegistry())

    result = resolver.resolve(
        SkillResolveContext(
            user_question="生成质量报告",
            tenant_id="tenant_001",
            skill_id="quality_report",
            agent_config={"permissions": ["report:generate", "quality:read"]},
        )
    )

    assert result.resolved is True
    assert result.skill_set.report_skill.skill_id == "quality_report"
    assert result.skill_set.resolution_source == "skill_id"
