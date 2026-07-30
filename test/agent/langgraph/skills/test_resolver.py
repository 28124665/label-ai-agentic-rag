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
