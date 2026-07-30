"""Registry for loading and querying configured runtime skills."""
from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml

from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill, SkillBase
from agent.langgraph.skills.validator import validate_skills

SkillT = TypeVar("SkillT", bound=SkillBase)


class SkillRegistry:
    """Load and provide indexed access to report, data, and retrieval skills."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[3] / "config" / "report_skills"
        self._skills: dict[str, SkillBase] = {}
        self.reload()

    def reload(self) -> None:
        """Reload YAML configurations and validate them as one consistent set."""
        skills: list[SkillBase] = []
        skills.extend(self._load_directory(self.root, "report", ReportSkill))
        skills.extend(self._load_directory(self.root / "data", "data", DataSkill))
        skills.extend(
            self._load_directory(self.root / "retrieval", "retrieval", RetrievalSkill)
        )
        validate_skills(skills)
        self._skills = {skill.skill_id: skill for skill in skills}

    def get_by_id(self, skill_id: str) -> SkillBase | None:
        """Return a skill by identifier when it exists."""
        return self._skills.get(skill_id)

    def get_report_by_type(self, report_type: str) -> ReportSkill | None:
        """Return the configured report skill for a report type."""
        return next(
            (
                skill
                for skill in self._skills.values()
                if isinstance(skill, ReportSkill) and skill.report_type == report_type
            ),
            None,
        )

    def get_data_for_report(self, report_skill_id: str) -> DataSkill | None:
        """Return the data skill reverse-linked to a report skill."""
        return self._get_linked_skill(report_skill_id, DataSkill)

    def get_retrieval_for_report(self, report_skill_id: str) -> RetrievalSkill | None:
        """Return the retrieval skill reverse-linked to a report skill."""
        return self._get_linked_skill(report_skill_id, RetrievalSkill)

    def list_all(self) -> list[SkillBase]:
        """Return all loaded skills in configuration load order."""
        return list(self._skills.values())

    def _get_linked_skill(
        self, report_skill_id: str, skill_type: type[SkillT]
    ) -> SkillT | None:
        return next(
            (
                skill
                for skill in self._skills.values()
                if isinstance(skill, skill_type)
                and skill.linked_report_skill_id == report_skill_id
            ),
            None,
        )

    def _load_directory(
        self, directory: Path, skill_type: str, model_type: type[SkillT]
    ) -> list[SkillT]:
        if not directory.exists():
            return []
        skills: list[SkillT] = []
        for path in sorted(directory.glob("*.yaml")):
            with path.open(encoding="utf-8") as config_file:
                payload = yaml.safe_load(config_file) or {}
            if not isinstance(payload, dict):
                raise ValueError(f"Skill configuration '{path}' must be a YAML mapping")
            payload.setdefault("skill_type", skill_type)
            skills.append(model_type.model_validate(payload))
        return skills
