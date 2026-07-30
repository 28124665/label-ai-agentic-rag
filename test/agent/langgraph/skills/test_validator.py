"""Skill configuration validator tests."""
from __future__ import annotations

import pytest

from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill
from agent.langgraph.skills.registry import SkillRegistry
from agent.langgraph.skills.validator import SkillValidationError, validate_skills


def test_rejects_report_core_section_missing_from_templates():
    report = ReportSkill(
        skill_id="invalid_report",
        skill_type="report",
        version="1.0",
        name="Invalid report",
        description="Invalid report for testing",
        enabled=True,
        report_type="invalid_analysis",
        section_templates=[{"section_id": "overview"}],
        core_sections=["missing_section"],
    )

    with pytest.raises(SkillValidationError, match="core_sections.*missing_section"):
        validate_skills([report])


def test_rejects_data_skill_without_linked_report_skill_id():
    data = DataSkill(
        skill_id="orphan_data",
        skill_type="data",
        version="1.0",
        name="Orphan data",
        description="Missing report link",
        enabled=True,
        linked_report_skill_id=None,
        db_targets=[{"target_id": "t1", "tables": [{"table_name": "tbl"}]}],
    )

    with pytest.raises(SkillValidationError, match="missing required linked_report_skill_id"):
        validate_skills([data])


def test_rejects_invalid_linked_report_skill_id():
    data = DataSkill(
        skill_id="bad_link_data",
        skill_type="data",
        version="1.0",
        name="Bad link data",
        description="Links to missing report",
        enabled=True,
        linked_report_skill_id="does_not_exist",
        db_targets=[{"target_id": "t1", "tables": [{"table_name": "tbl"}]}],
    )

    with pytest.raises(SkillValidationError, match="unknown report skill"):
        validate_skills([data])


def test_rejects_empty_rag_targets():
    report = ReportSkill(
        skill_id="r1",
        skill_type="report",
        version="1.0",
        name="R1",
        description="R1",
        enabled=True,
        report_type="r1_analysis",
        section_templates=[{"section_id": "overview"}],
        core_sections=["overview"],
    )
    retrieval = RetrievalSkill(
        skill_id="empty_retrieval",
        skill_type="retrieval",
        version="1.0",
        name="Empty retrieval",
        description="No rag targets",
        enabled=True,
        linked_report_skill_id="r1",
        rag_targets=[],
    )
    data = DataSkill(
        skill_id="d1",
        skill_type="data",
        version="1.0",
        name="D1",
        description="D1",
        enabled=True,
        linked_report_skill_id="r1",
        db_targets=[{"target_id": "t1", "tables": [{"table_name": "tbl"}]}],
    )

    with pytest.raises(SkillValidationError, match="rag_targets must not be empty"):
        validate_skills([report, data, retrieval])


def test_rejects_metric_binding_unknown_table():
    report = ReportSkill(
        skill_id="r2",
        skill_type="report",
        version="1.0",
        name="R2",
        description="R2",
        enabled=True,
        report_type="r2_analysis",
        section_templates=[{"section_id": "overview"}],
        core_sections=["overview"],
    )
    data = DataSkill(
        skill_id="d2",
        skill_type="data",
        version="1.0",
        name="D2",
        description="D2",
        enabled=True,
        linked_report_skill_id="r2",
        db_targets=[{"target_id": "t1", "tables": [{"table_name": "tbl"}]}],
        metric_bindings={
            "m1": {
                "source_target_id": "t1",
                "table_name": "missing_table",
            }
        },
    )
    retrieval = RetrievalSkill(
        skill_id="ret2",
        skill_type="retrieval",
        version="1.0",
        name="Ret2",
        description="Ret2",
        enabled=True,
        linked_report_skill_id="r2",
        rag_targets=[{"target_id": "kb1", "kb_id": "kb1"}],
    )

    with pytest.raises(SkillValidationError, match="metric_bindings.*missing_table"):
        validate_skills([report, data, retrieval])


def test_rejects_report_without_reverse_linked_data_and_retrieval():
    report = ReportSkill(
        skill_id="lonely_report",
        skill_type="report",
        version="1.0",
        name="Lonely",
        description="No reverse links",
        enabled=True,
        report_type="lonely_analysis",
        section_templates=[{"section_id": "overview"}],
        core_sections=["overview"],
    )

    with pytest.raises(SkillValidationError, match="no reverse-linked data skill"):
        validate_skills([report])


def test_registry_production_skills_still_validate():
    registry = SkillRegistry()
    skills = registry.list_all()
    assert len(skills) >= 19
    validate_skills(skills)
