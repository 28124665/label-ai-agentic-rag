"""Validation rules for loaded skill configurations."""
from __future__ import annotations

from collections.abc import Iterable

from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill, SkillBase

# Fallback report skill is allowed without linked data/retrieval skills.
_FALLBACK_REPORT_SKILL_IDS = frozenset({"generic_analysis"})


class SkillValidationError(ValueError):
    """Raised when one or more configured skills are inconsistent."""


def validate_skills(skills: Iterable[SkillBase]) -> None:
    """Validate cross-skill references and internal configuration references."""
    skill_list = list(skills)
    issues: list[str] = []
    ids = [skill.skill_id for skill in skill_list]
    duplicate_ids = sorted({skill_id for skill_id in ids if ids.count(skill_id) > 1})
    if duplicate_ids:
        issues.append(f"duplicate skill_id values: {', '.join(duplicate_ids)}")

    reports = [skill for skill in skill_list if isinstance(skill, ReportSkill)]
    data_skills = [skill for skill in skill_list if isinstance(skill, DataSkill)]
    retrieval_skills = [skill for skill in skill_list if isinstance(skill, RetrievalSkill)]
    report_ids = {skill.skill_id for skill in reports}
    report_types = [skill.report_type for skill in reports]
    duplicate_report_types = sorted(
        {report_type for report_type in report_types if report_types.count(report_type) > 1}
    )
    if duplicate_report_types:
        issues.append(f"duplicate report_type values: {', '.join(duplicate_report_types)}")

    linked_data_report_ids = {
        skill.linked_report_skill_id
        for skill in data_skills
        if skill.linked_report_skill_id
    }
    linked_retrieval_report_ids = {
        skill.linked_report_skill_id
        for skill in retrieval_skills
        if skill.linked_report_skill_id
    }

    for report in reports:
        _validate_report(report, issues)
        if report.skill_id in _FALLBACK_REPORT_SKILL_IDS:
            continue
        if report.skill_id not in linked_data_report_ids:
            issues.append(
                f"report skill '{report.skill_id}' has no reverse-linked data skill"
            )
        if report.skill_id not in linked_retrieval_report_ids:
            issues.append(
                f"report skill '{report.skill_id}' has no reverse-linked retrieval skill"
            )

    for skill in data_skills:
        _validate_data_skill(skill, report_ids, issues)
    for skill in retrieval_skills:
        _validate_retrieval_skill(skill, report_ids, issues)

    if issues:
        raise SkillValidationError("Invalid skill configuration:\n- " + "\n- ".join(issues))


def _validate_report(skill: ReportSkill, issues: list[str]) -> None:
    section_ids = {
        section["section_id"]
        for section in skill.section_templates
        if isinstance(section, dict) and isinstance(section.get("section_id"), str)
    }
    missing_sections = sorted(set(skill.core_sections) - section_ids)
    if missing_sections:
        issues.append(
            f"report skill '{skill.skill_id}' core_sections reference missing section_id values: "
            f"{', '.join(missing_sections)}"
        )

    metric_ids = {
        metric["metric_id"]
        for metric in skill.metric_definitions
        if isinstance(metric, dict) and isinstance(metric.get("metric_id"), str)
    }
    required_metric_ids = {
        metric_id
        for section in skill.section_templates
        if isinstance(section, dict)
        for metric_id in section.get("required_metric_ids", [])
        if isinstance(metric_id, str)
    }
    missing_metrics = sorted(required_metric_ids - metric_ids)
    if missing_metrics:
        issues.append(
            f"report skill '{skill.skill_id}' required_metric_ids reference missing metric_id values: "
            f"{', '.join(missing_metrics)}"
        )


def _validate_data_skill(
    skill: DataSkill, report_ids: set[str], issues: list[str]
) -> None:
    _validate_report_link(skill, report_ids, issues, require_link=True)
    targets = {
        target["target_id"]: {
            table["table_name"]
            for table in target.get("tables", [])
            if isinstance(table, dict) and isinstance(table.get("table_name"), str)
        }
        for target in skill.db_targets
        if isinstance(target, dict) and isinstance(target.get("target_id"), str)
    }
    for query in skill.query_templates:
        if not isinstance(query, dict):
            continue
        target_id = query.get("target_id")
        table_name = query.get("table_name")
        if target_id not in targets:
            issues.append(
                f"data skill '{skill.skill_id}' query template '{query.get('template_id', '<unknown>')}' "
                f"references unknown target_id '{target_id}'"
            )
        elif table_name not in targets[target_id]:
            issues.append(
                f"data skill '{skill.skill_id}' query template '{query.get('template_id', '<unknown>')}' "
                f"references unknown table_name '{table_name}'"
            )
    _validate_metric_bindings(skill, targets, issues)
    _validate_evidence_sources(skill.skill_id, skill.evidence_requirements, set(targets), issues)


def _validate_metric_bindings(
    skill: DataSkill,
    targets: dict[str, set[str]],
    issues: list[str],
) -> None:
    for metric_id, binding in skill.metric_bindings.items():
        if not isinstance(binding, dict):
            continue
        target_id = binding.get("source_target_id")
        table_name = binding.get("table_name")
        if not target_id and not table_name:
            continue
        if target_id not in targets:
            issues.append(
                f"data skill '{skill.skill_id}' metric_bindings '{metric_id}' "
                f"references unknown source_target_id '{target_id}'"
            )
            continue
        if table_name and table_name not in targets[target_id]:
            issues.append(
                f"data skill '{skill.skill_id}' metric_bindings '{metric_id}' "
                f"references unknown table_name '{table_name}'"
            )


def _validate_retrieval_skill(
    skill: RetrievalSkill, report_ids: set[str], issues: list[str]
) -> None:
    _validate_report_link(skill, report_ids, issues, require_link=True)
    if not skill.rag_targets:
        issues.append(f"retrieval skill '{skill.skill_id}' rag_targets must not be empty")
    target_ids: set[str] = set()
    for target in skill.rag_targets:
        if not isinstance(target, dict):
            continue
        target_id = target.get("target_id")
        if isinstance(target_id, str):
            target_ids.add(target_id)
        if not target.get("kb_id"):
            issues.append(
                f"retrieval skill '{skill.skill_id}' rag target '{target_id}' is missing kb_id"
            )
    _validate_evidence_sources(skill.skill_id, skill.evidence_requirements, target_ids, issues)


def _validate_report_link(
    skill: DataSkill | RetrievalSkill,
    report_ids: set[str],
    issues: list[str],
    *,
    require_link: bool,
) -> None:
    linked_report = skill.linked_report_skill_id
    if require_link and not linked_report:
        issues.append(
            f"skill '{skill.skill_id}' is missing required linked_report_skill_id"
        )
        return
    if linked_report and linked_report not in report_ids:
        issues.append(
            f"skill '{skill.skill_id}' links to unknown report skill '{linked_report}'"
        )


def _validate_evidence_sources(
    skill_id: str,
    requirements: list[dict],
    target_ids: set[str],
    issues: list[str],
) -> None:
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        source_target_id = requirement.get("source_target_id")
        if source_target_id not in target_ids:
            issues.append(
                f"skill '{skill_id}' evidence requirement "
                f"'{requirement.get('evidence_id', '<unknown>')}' references unknown "
                f"source_target_id '{source_target_id}'"
            )
