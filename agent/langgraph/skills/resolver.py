"""Resolve a report skill set from an incoming request context."""
from __future__ import annotations

from agent.langgraph.skills.models import (
    ReportSkill,
    ResolvedSkillSet,
    SkillResolveContext,
    SkillResolveResult,
)
from agent.langgraph.skills.registry import SkillRegistry


class SkillResolver:
    """Resolve report skills in explicit-to-fallback priority order."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def resolve(self, context: SkillResolveContext) -> SkillResolveResult:
        """Resolve the best report skill set for the supplied context."""
        if context.skill_id is not None:
            skill_id = context.skill_id.strip()
            if not skill_id:
                return self._failure("SKILL_NOT_FOUND")
            skill = self.registry.get_by_id(skill_id)
            if not isinstance(skill, ReportSkill):
                return self._failure("SKILL_NOT_FOUND")
            return self._resolve_report_skill(skill, "skill_id", context)

        report_type = context.report_type or self._route_report_type(context)
        if report_type:
            skill = self.registry.get_report_by_type(report_type)
            if skill:
                return self._resolve_report_skill(skill, "report_type", context)

        skill = self._find_keyword_skill(context.user_question)
        if skill:
            return self._resolve_report_skill(skill, "keyword", context)

        tenant_default = self._tenant_default_skill(context)
        if tenant_default:
            skill = self.registry.get_by_id(tenant_default)
            if isinstance(skill, ReportSkill):
                return self._resolve_report_skill(skill, "tenant_default", context)

        fallback = self.registry.get_by_id("generic_analysis")
        if isinstance(fallback, ReportSkill):
            return self._resolve_report_skill(fallback, "fallback", context, fallback_used=True)
        return self._failure("NO_SKILL_AVAILABLE")

    def _resolve_report_skill(
        self,
        report_skill: ReportSkill,
        source: str,
        context: SkillResolveContext,
        fallback_used: bool = False,
    ) -> SkillResolveResult:
        if not report_skill.enabled or not self._has_permission(report_skill, context):
            return self._failure("SKILL_PERMISSION_DENIED")

        data_skill = self.registry.get_data_for_report(report_skill.skill_id)
        retrieval_skill = self.registry.get_retrieval_for_report(report_skill.skill_id)
        warnings: list[str] = []
        if data_skill is None:
            warnings.append(f"No data skill linked to '{report_skill.skill_id}'")
        elif not data_skill.enabled:
            warnings.append(f"Data skill '{data_skill.skill_id}' is disabled")
            data_skill = None
        if retrieval_skill is None:
            warnings.append(f"No retrieval skill linked to '{report_skill.skill_id}'")
        elif not retrieval_skill.enabled:
            warnings.append(f"Retrieval skill '{retrieval_skill.skill_id}' is disabled")
            retrieval_skill = None

        skill_set = ResolvedSkillSet(
            report_skill=report_skill,
            data_skill=data_skill,
            retrieval_skill=retrieval_skill,
            resolution_source=source,
            fallback_used=fallback_used,
            warnings=warnings,
        )
        return SkillResolveResult(
            skill_set=skill_set,
            resolved=True,
            fallback_used=fallback_used,
            reason="RESOLVED",
            warnings=warnings,
        )

    def _find_keyword_skill(self, user_question: str) -> ReportSkill | None:
        normalized_question = user_question.casefold()
        candidates = [
            skill
            for skill in self.registry.list_all()
            if isinstance(skill, ReportSkill) and skill.enabled
        ]
        matches = [
            (
                sum(
                    len(keyword)
                    for keyword in skill.intent_keywords
                    if keyword.casefold() in normalized_question
                ),
                skill,
            )
            for skill in candidates
        ]
        matched_skills = [match for match in matches if match[0] > 0]
        return max(matched_skills, key=lambda match: match[0])[1] if matched_skills else None

    @staticmethod
    def _route_report_type(context: SkillResolveContext) -> str | None:
        metadata = (context.route_decision or {}).get("metadata", {})
        value = metadata.get("report_type") if isinstance(metadata, dict) else None
        return value if isinstance(value, str) else None

    @staticmethod
    def _tenant_default_skill(context: SkillResolveContext) -> str | None:
        config = context.agent_config or {}
        defaults = config.get("tenant_defaults", {})
        if isinstance(defaults, dict):
            value = defaults.get(context.tenant_id)
            if isinstance(value, str):
                return value
        value = config.get("default_skill_id")
        return value if isinstance(value, str) else None

    @staticmethod
    def _has_permission(report_skill: ReportSkill, context: SkillResolveContext) -> bool:
        config = context.agent_config or {}
        if "permissions" not in config:
            return True
        permissions = config["permissions"]
        if not isinstance(permissions, list):
            return False
        return set(report_skill.required_permissions).issubset(set(permissions))

    @staticmethod
    def _failure(reason: str) -> SkillResolveResult:
        return SkillResolveResult(
            skill_set=None,
            resolved=False,
            fallback_used=False,
            reason=reason,
        )
