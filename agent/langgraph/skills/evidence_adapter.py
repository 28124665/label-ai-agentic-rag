"""Normalize and check evidence requirements declared by skills."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent.langgraph.skills.models import ResolvedSkillSet


class SkillEvidenceRequirement(BaseModel):
    """One normalized piece of evidence required by a resolved skill set."""

    evidence_id: str
    evidence_type: str
    source_target_id: str | None = None
    table_name: str | None = None
    required: bool = False
    min_docs: int = 0
    required_for_sections: list[str] = Field(default_factory=list)


class SkillEvidenceCheckResult(BaseModel):
    """Evidence coverage result used to constrain answer and report generation."""

    answerable: bool
    publish_mode: str
    missing_required_evidence: list[str] = Field(default_factory=list)
    section_coverage: dict[str, bool] = Field(default_factory=dict)
    reason: str


class SkillEvidenceAdapter:
    """Collect and validate evidence requirements across resolved skills."""

    def collect_requirements(
        self, skill_set: ResolvedSkillSet
    ) -> list[SkillEvidenceRequirement]:
        """Collect data, retrieval, and optional report evidence requirements."""
        raw_requirements: list[dict[str, Any]] = []
        if skill_set.data_skill:
            raw_requirements.extend(skill_set.data_skill.evidence_requirements)
        if skill_set.retrieval_skill:
            raw_requirements.extend(skill_set.retrieval_skill.evidence_requirements)

        for evidence_type in skill_set.report_skill.required_evidence_types:
            raw_requirements.append(
                {
                    "evidence_id": f"report_{evidence_type}_evidence",
                    "evidence_type": evidence_type,
                    "required": True,
                    "required_for_sections": [],
                }
            )

        requirements: dict[str, SkillEvidenceRequirement] = {}
        for raw_requirement in raw_requirements:
            requirement = SkillEvidenceRequirement.model_validate(raw_requirement)
            requirements.setdefault(requirement.evidence_id, requirement)
        return list(requirements.values())

    def check(
        self,
        requirements: list[SkillEvidenceRequirement],
        evidence: list[dict],
    ) -> SkillEvidenceCheckResult:
        """Check required evidence presence and core section coverage."""
        present_ids = {
            evidence_id
            for item in evidence
            for evidence_id in self._evidence_ids(item)
        }
        matching_requirements = {
            requirement.evidence_id: any(
                self._matches_requirement(requirement, item) for item in evidence
            )
            for requirement in requirements
        }
        missing = [
            requirement.evidence_id
            for requirement in requirements
            if requirement.required and not matching_requirements[requirement.evidence_id]
        ]
        sections = {
            section
            for requirement in requirements
            for section in requirement.required_for_sections
        }
        coverage = {
            section: all(
                matching_requirements[requirement.evidence_id]
                for requirement in requirements
                if requirement.required and section in requirement.required_for_sections
            )
            for section in sections
        }
        if missing:
            return SkillEvidenceCheckResult(
                answerable=False,
                publish_mode="summary_only",
                missing_required_evidence=missing,
                section_coverage=coverage,
                reason=f"缺少必需 Evidence: {', '.join(missing)}",
            )
        return SkillEvidenceCheckResult(
            answerable=True,
            publish_mode="full",
            section_coverage=coverage,
            reason="Skill 声明的必需 Evidence 已满足",
        )

    @staticmethod
    def _evidence_ids(item: dict) -> list[str]:
        identifiers = item.get("evidence_ids")
        if isinstance(identifiers, list):
            return [str(identifier) for identifier in identifiers]
        identifier = item.get("evidence_id")
        return [str(identifier)] if identifier else []

    def _matches_requirement(
        self, requirement: SkillEvidenceRequirement, item: dict
    ) -> bool:
        if requirement.evidence_id in self._evidence_ids(item):
            return self._matches_metadata(requirement, item)
        if (
            requirement.evidence_id.startswith("report_")
            and self._source_type_matches(requirement.evidence_type, item.get("source_type"))
        ):
            return True
        return False

    @staticmethod
    def _matches_metadata(
        requirement: SkillEvidenceRequirement, item: dict
    ) -> bool:
        if (
            requirement.source_target_id
            and item.get("source_target_id")
            and item["source_target_id"] != requirement.source_target_id
        ):
            return False
        if (
            requirement.table_name
            and item.get("table_name")
            and item["table_name"] != requirement.table_name
        ):
            return False
        return True

    @staticmethod
    def _source_type_matches(evidence_type: str, source_type: Any) -> bool:
        normalized = str(source_type or "").casefold()
        expected = evidence_type.casefold()
        aliases = {
            "db": {"db", "db_rows"},
            "db_rows": {"db", "db_rows"},
            "rag": {"rag", "rag_docs"},
            "rag_docs": {"rag", "rag_docs"},
            "graph": {"graph", "graph_rows"},
            "graph_rows": {"graph", "graph_rows", "graph_result"},
        }
        return normalized in aliases.get(expected, {expected})
