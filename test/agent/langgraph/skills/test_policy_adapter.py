"""Tests for converting resolved skills into policy decisions."""
from __future__ import annotations

from agent.langgraph.skills.models import SkillResolveContext
from agent.langgraph.skills.policy_adapter import SkillPolicyAdapter, SkillPolicyContext
from agent.langgraph.skills.registry import SkillRegistry
from agent.langgraph.skills.resolver import SkillResolver


def _quality_skill_set():
    result = SkillResolver(SkillRegistry()).resolve(
        SkillResolveContext(
            user_question="生成质量报告",
            tenant_id="tenant_001",
            skill_id="quality_report",
        )
    )
    assert result.skill_set is not None
    return result.skill_set


def test_denies_tenant_outside_non_empty_allowed_tenants():
    skill_set = _quality_skill_set()
    skill_set.report_skill.allowed_tenants = ["tenant_allowed"]
    adapter = SkillPolicyAdapter()

    decision = adapter.evaluate(
        adapter.build_rules(skill_set),
        SkillPolicyContext(
            tenant_id="tenant_denied",
            permissions={"report:generate", "quality:read"},
        ),
    )

    assert decision.allowed is False
    assert decision.reason_code == "TENANT_NOT_ALLOWED"


def test_denies_report_format_not_declared_by_skill():
    adapter = SkillPolicyAdapter()

    decision = adapter.evaluate(
        adapter.build_rules(_quality_skill_set()),
        SkillPolicyContext(
            tenant_id="tenant_001",
            permissions={"report:generate", "quality:read"},
            report_format="pdf",
        ),
    )

    assert decision.allowed is False
    assert decision.reason_code == "FORMAT_NOT_ALLOWED"
