"""Tests for skill evidence requirement checks."""
from __future__ import annotations

from agent.langgraph.skills.evidence_adapter import SkillEvidenceAdapter
from agent.langgraph.skills.models import SkillResolveContext
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


def test_missing_production_output_rows_requires_summary_only():
    adapter = SkillEvidenceAdapter()

    result = adapter.check(
        adapter.collect_requirements(_quality_skill_set()),
        [{"evidence_id": "quality_exception_rows", "source_type": "db"}],
    )

    assert result.answerable is False
    assert result.publish_mode == "summary_only"
    assert "production_output_rows" in result.missing_required_evidence


def test_required_quality_database_evidence_improves_section_coverage():
    adapter = SkillEvidenceAdapter()
    requirements = adapter.collect_requirements(_quality_skill_set())

    before = adapter.check(requirements, [])
    after = adapter.check(
        requirements,
        [
            {"evidence_id": "quality_exception_rows", "source_type": "db"},
            {"evidence_id": "production_output_rows", "source_type": "db"},
            {"evidence_id": "inspection_rows", "source_type": "db"},
            {"evidence_id": "corrective_action_rows", "source_type": "db"},
        ],
    )

    assert after.section_coverage["overview"] is True
    assert sum(after.section_coverage.values()) > sum(before.section_coverage.values())
