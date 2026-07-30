"""Skill registry tests."""
from __future__ import annotations

from agent.langgraph.skills.registry import SkillRegistry


def test_loads_all_configured_skills():
    registry = SkillRegistry()

    skills = registry.list_all()

    assert len(skills) == 19
    assert registry.get_by_id("quality_report").skill_type == "report"
    assert registry.get_by_id("quality_data_access").skill_type == "data"
    assert registry.get_by_id("quality_knowledge_retrieval").skill_type == "retrieval"


def test_reverse_links_data_and_retrieval_skills_to_report():
    registry = SkillRegistry()

    assert registry.get_data_for_report("quality_report").skill_id == "quality_data_access"
    assert (
        registry.get_retrieval_for_report("quality_report").skill_id
        == "quality_knowledge_retrieval"
    )


def test_finds_report_skill_by_report_type():
    registry = SkillRegistry()

    assert registry.get_report_by_type("quality_analysis").skill_id == "quality_report"
