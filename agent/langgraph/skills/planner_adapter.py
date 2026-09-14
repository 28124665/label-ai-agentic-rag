"""Build deterministic execution plans from resolved runtime skills."""
from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from agent.langgraph.routers.models import ExecutionPlan, PlanStep, StepArgs
from agent.langgraph.skills.models import DataSkill, ResolvedSkillSet

logger = logging.getLogger(__name__)

_TEMPLATE_VARIABLE_PATTERN = re.compile(r"{([^{}]+)}")


class SkillPlanContext(BaseModel):
    """Inputs needed to generate an execution plan from a resolved skill set."""

    user_question: str
    tenant_id: str
    time_range: dict[str, Any] | None = None
    report_date: str | None = None
    query_lang: str = "zh-CN"
    skill_set: ResolvedSkillSet
    agent_config: dict[str, Any] = Field(default_factory=dict)


class SkillPlanResult(BaseModel):
    """Result of deterministic skill-aware plan generation."""

    success: bool
    plan: ExecutionPlan | None = None
    reason: str = ""
    missing_context: list[str] = Field(default_factory=list)


class SkillPlannerAdapter:
    """Translate declared skill targets and templates into an execution DAG."""

    def build_plan(self, context: SkillPlanContext) -> SkillPlanResult:
        """Build a constrained execution plan without introducing undeclared sources."""
        missing_context = self._missing_required_context(context)
        if missing_context:
            logger.info(
                "[SkillPlannerAdapter] Missing required context for %s: %s",
                context.skill_set.report_skill.skill_id,
                missing_context,
            )
            return SkillPlanResult(
                success=False,
                reason="MISSING_REQUIRED_CONTEXT",
                missing_context=missing_context,
            )

        template_context = self._template_context(context)
        steps = self._build_database_steps(context, template_context)
        steps.extend(self._build_rag_steps(context, template_context))
        steps.extend(self._build_graph_steps(context, template_context))
        steps.append(self._build_report_step(context, steps))
        plan = ExecutionPlan(
            plan_id=f"{context.skill_set.report_skill.skill_id}_plan",
            steps=steps,
            fallback_strategy="skill_guided",
        )
        logger.info(
            "[SkillPlannerAdapter] Built plan %s with %d steps",
            plan.plan_id,
            len(plan.steps),
        )
        return SkillPlanResult(success=True, plan=plan)

    def _build_database_steps(
        self, context: SkillPlanContext, template_context: dict[str, Any]
    ) -> list[PlanStep]:
        data_skill = context.skill_set.data_skill
        if data_skill is None:
            return []

        db_targets = {
            target["target_id"]: target
            for target in data_skill.db_targets
            if isinstance(target.get("target_id"), str)
        }
        steps: list[PlanStep] = []
        for template in data_skill.query_templates:
            target_id = template.get("target_id")
            target = db_targets.get(target_id)
            if target is None:
                logger.warning(
                    "[SkillPlannerAdapter] Skip query template %s with unknown target %s",
                    template.get("template_id"),
                    target_id,
                )
                continue
            filters = template.get("filters", {})
            rendered_filters = self._render_value(filters, template_context)
            template_id = str(template["template_id"])
            steps.append(
                PlanStep(
                    step_id=f"query_{template_id}",
                    tool="database",
                    args=StepArgs(
                        query=str(template.get("purpose", context.user_question)),
                        db_id=str(target["db_id"]),
                        extra={
                            **self._skill_context(context.skill_set),
                            "query_template_id": template_id,
                            "table_name": str(template["table_name"]),
                            "target_id": target_id,
                            "filters": rendered_filters,
                        },
                    ),
                    depends_on=[],
                    can_parallel=True,
                    description=str(template.get("purpose", "")),
                )
            )
        return steps

    def _build_rag_steps(
        self, context: SkillPlanContext, template_context: dict[str, Any]
    ) -> list[PlanStep]:
        retrieval_skill = context.skill_set.retrieval_skill
        if retrieval_skill is None:
            return []

        optional_context = set(
            retrieval_skill.query_context_requirements.get("optional_context", [])
        )
        steps: list[PlanStep] = []
        for target in retrieval_skill.rag_targets:
            target_id = str(target["target_id"])
            rendered_queries = self._render_rag_queries(
                target.get("query_templates", []),
                template_context,
                optional_context,
                bool(target.get("required", False)),
            )
            if not rendered_queries:
                continue
            steps.append(
                PlanStep(
                    step_id=f"retrieve_{target_id}",
                    tool="rag",
                    args=StepArgs(
                        query="\n".join(rendered_queries),
                        kb_ids=[str(target["kb_id"])],
                        extra={
                            **self._skill_context(context.skill_set),
                            "rag_target_id": target_id,
                            "query_template_ids": [
                                str(template["template_id"])
                                for template in target.get("query_templates", [])
                            ],
                            "document_filters": target.get("document_filters", {}),
                            "metadata_filters": self._render_value(
                                target.get("metadata_filters", {}), template_context
                            ),
                        },
                    ),
                    depends_on=[],
                    can_parallel=True,
                    description=str(target.get("purpose", "")),
                )
            )
        return steps

    def _build_graph_steps(
        self, context: SkillPlanContext, template_context: dict[str, Any]
    ) -> list[PlanStep]:
        """当 Skill 声明 graph 证据需求时，生成图问答 PlanStep（设计文档 §3.4.4）。

        graph 能力以「证据来源扩展」方式接入：与 _build_database_steps /
        _build_rag_steps 并列，读取 agent_config.graph_config 填充 StepArgs。
        """
        if not self._skill_requires_graph(context.skill_set):
            return []

        report_skill = context.skill_set.report_skill
        graph_config = context.agent_config.get("graph_config", {}) or {}
        source_type = str(graph_config.get("source_type", "Meeting"))
        max_rows = int(graph_config.get("max_rows", 20))
        max_result_chars = int(graph_config.get("max_result_chars", 12000))
        enable_hybrid_retrieval = bool(
            graph_config.get("enable_hybrid_retrieval", False)
        )
        enable_pg = bool(graph_config.get("enable_pg", False))
        timeout_ms = int(graph_config.get("timeout_ms", 30000))

        return [
            PlanStep(
                step_id=f"graph_{report_skill.skill_id}",
                tool="graph",
                args=StepArgs(
                    query=context.user_question,
                    source_type=source_type,
                    enable_pg=enable_pg,
                    max_rows=max_rows,
                    max_result_chars=max_result_chars,
                    enable_hybrid_retrieval=enable_hybrid_retrieval,
                    timeout_ms=timeout_ms,
                    extra={
                        **self._skill_context(context.skill_set),
                        "graph_source": "skill_evidence_requirement",
                    },
                ),
                depends_on=[],
                can_parallel=True,
                description=f"图谱问答：{report_skill.name}",
            )
        ]

    def _skill_requires_graph(self, skill_set: ResolvedSkillSet) -> bool:
        """判断 ResolvedSkillSet 是否声明 graph 证据需求（设计文档 §3.4.2）。"""
        required_types = {
            t.casefold() for t in (skill_set.report_skill.required_evidence_types or [])
        }
        if required_types & {"graph", "graph_rows", "graph_result"}:
            return True
        for linked in (skill_set.data_skill, skill_set.retrieval_skill):
            if linked is None:
                continue
            for req in linked.evidence_requirements or []:
                evidence_type = (req.get("evidence_type") or "").casefold()
                if evidence_type in {"graph", "graph_rows", "graph_result"}:
                    return True
        return False

    def _build_report_step(
        self, context: SkillPlanContext, evidence_steps: list[PlanStep]
    ) -> PlanStep:
        report_skill = context.skill_set.report_skill
        return PlanStep(
            step_id=f"generate_{report_skill.skill_id}",
            tool="report",
            args=StepArgs(
                query=f"生成{report_skill.name}",
                extra={
                    **self._skill_context(context.skill_set),
                    "format": report_skill.default_format or "markdown",
                },
            ),
            depends_on=[step.step_id for step in evidence_steps],
            can_parallel=False,
            description=f"生成{report_skill.name}",
        )

    @staticmethod
    def _skill_context(skill_set: ResolvedSkillSet) -> dict[str, str | None]:
        return {
            "skill_id": skill_set.report_skill.skill_id,
            "data_skill_id": skill_set.data_skill.skill_id if skill_set.data_skill else None,
            "retrieval_skill_id": (
                skill_set.retrieval_skill.skill_id if skill_set.retrieval_skill else None
            ),
        }

    def _missing_required_context(self, context: SkillPlanContext) -> list[str]:
        required = set(context.skill_set.report_skill.required_context)
        if context.skill_set.data_skill:
            required.update(self._data_required_context(context.skill_set.data_skill))
        values = self._template_context(context)
        return sorted(name for name in required if not values.get(name))

    @staticmethod
    def _data_required_context(data_skill: DataSkill) -> set[str]:
        return {
            item
            for rule in data_skill.validation_rules
            for item in rule.get("required_context", [])
            if isinstance(item, str)
        }

    @staticmethod
    def _template_context(context: SkillPlanContext) -> dict[str, Any]:
        extras = context.agent_config.get("context", {})
        return {
            "tenant_id": context.tenant_id,
            "time_range": context.time_range,
            "report_date": context.report_date,
            "query_lang": context.query_lang,
            **(extras if isinstance(extras, dict) else {}),
        }

    @staticmethod
    def _render_value(value: Any, template_context: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return _TEMPLATE_VARIABLE_PATTERN.sub(
                lambda match: str(template_context.get(match.group(1), match.group(0))),
                value,
            )
        if isinstance(value, list):
            return [
                SkillPlannerAdapter._render_value(item, template_context) for item in value
            ]
        if isinstance(value, dict):
            return {
                key: SkillPlannerAdapter._render_value(item, template_context)
                for key, item in value.items()
            }
        return value

    def _render_rag_queries(
        self,
        query_templates: list[dict[str, Any]],
        template_context: dict[str, Any],
        optional_context: set[str],
        target_required: bool,
    ) -> list[str]:
        queries: list[str] = []
        for template in query_templates:
            query = str(template.get("query", ""))
            variables = set(_TEMPLATE_VARIABLE_PATTERN.findall(query))
            missing = {name for name in variables if not template_context.get(name)}
            if missing and not missing.issubset(optional_context):
                continue
            if missing and not target_required:
                continue
            queries.append(self._render_value(query, template_context))
        return queries
