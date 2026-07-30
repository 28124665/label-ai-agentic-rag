"""Translate resolved skill policies into executable guard rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.langgraph.skills.models import ResolvedSkillSet


@dataclass(frozen=True)
class SkillPolicyRules:
    """Normalized policy constraints declared by a resolved skill set."""

    required_permissions: frozenset[str] = frozenset()
    allowed_tenants: frozenset[str] = frozenset()
    require_tenant_filter: bool = False
    forbidden_operations: frozenset[str] = frozenset()
    sensitive_field_policy: dict[str, Any] = field(default_factory=dict)
    default_limit: int | None = None
    max_rows: int | None = None
    allowed_formats: frozenset[str] = frozenset()
    publish_policy: dict[str, Any] = field(default_factory=dict)
    allow_web_fallback: bool = True


@dataclass(frozen=True)
class SkillPolicyContext:
    """Runtime attributes required to evaluate normalized skill policies."""

    tenant_id: str = ""
    permissions: frozenset[str] | set[str] | list[str] = field(default_factory=frozenset)
    report_format: str = ""
    tenant_filter_present: bool | None = None
    operation: str = ""
    requested_fields: list[str] = field(default_factory=list)
    web_fallback_requested: bool = False


@dataclass(frozen=True)
class SkillPolicyDecision:
    """Allow/deny result returned by the skill policy adapter."""

    allowed: bool
    reason_code: str = "ALLOWED"
    reason: str = ""


class SkillPolicyAdapter:
    """Build and evaluate policy rules declared by report, data, and RAG skills."""

    def build_rules(self, skill_set: ResolvedSkillSet) -> SkillPolicyRules:
        """Normalize policy declarations from all skills in a resolved set."""
        report_skill = skill_set.report_skill
        data_skill = skill_set.data_skill
        retrieval_skill = skill_set.retrieval_skill
        data_policy = data_skill.policy_constraints if data_skill else {}
        retrieval_policy = retrieval_skill.retrieval_policy if retrieval_skill else {}

        permissions = set(report_skill.required_permissions)
        if data_skill:
            permissions.update(data_skill.required_permissions)
        if retrieval_skill:
            permissions.update(retrieval_skill.required_permissions)

        return SkillPolicyRules(
            required_permissions=frozenset(permissions),
            allowed_tenants=frozenset(report_skill.allowed_tenants),
            require_tenant_filter=bool(
                data_policy.get("require_tenant_filter", False)
                or retrieval_policy.get("require_tenant_filter", False)
            ),
            forbidden_operations=frozenset(
                str(operation).casefold()
                for operation in data_policy.get("forbidden_operations", [])
            ),
            sensitive_field_policy=dict(data_policy.get("sensitive_field_policy", {})),
            default_limit=self._optional_int(data_policy.get("default_limit")),
            max_rows=self._optional_int(data_policy.get("max_rows")),
            allowed_formats=frozenset(
                str(report_format).casefold()
                for report_format in report_skill.allowed_formats
            ),
            publish_policy=dict(report_skill.publish_policy),
            allow_web_fallback=bool(retrieval_policy.get("allow_web_fallback", True)),
        )

    def evaluate(
        self, rules: SkillPolicyRules, context: SkillPolicyContext
    ) -> SkillPolicyDecision:
        """Evaluate a runtime action against normalized skill policy rules."""
        permissions = set(context.permissions)
        if not rules.required_permissions.issubset(permissions):
            return self._deny("PERMISSION_DENIED", "用户缺少 Skill 所需权限")
        if rules.allowed_tenants and context.tenant_id not in rules.allowed_tenants:
            return self._deny("TENANT_NOT_ALLOWED", "当前租户未启用该 Skill")
        if context.report_format and (
            rules.allowed_formats
            and context.report_format.casefold() not in rules.allowed_formats
        ):
            return self._deny("FORMAT_NOT_ALLOWED", "请求的报告格式未被 Skill 允许")
        if rules.require_tenant_filter and context.tenant_filter_present is False:
            return self._deny("TENANT_FILTER_REQUIRED", "Skill 要求查询包含租户过滤条件")
        if (
            context.operation
            and context.operation.casefold() in rules.forbidden_operations
        ):
            return self._deny("OPERATION_FORBIDDEN", "操作被 DataSkill 策略禁止")
        if context.web_fallback_requested and not rules.allow_web_fallback:
            return self._deny("WEB_FALLBACK_DENIED", "RetrievalSkill 禁止 Web fallback")
        return SkillPolicyDecision(allowed=True)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        return value if isinstance(value, int) else None

    @staticmethod
    def _deny(reason_code: str, reason: str) -> SkillPolicyDecision:
        return SkillPolicyDecision(allowed=False, reason_code=reason_code, reason=reason)
