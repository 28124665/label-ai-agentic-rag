"""Tests for deterministic skill-aware execution plan generation."""
from __future__ import annotations

import pytest

from agent.langgraph.routers.models import PlanStep
from agent.langgraph.routers.models import RouteDecision
from agent.langgraph.routers.planner import Planner
from agent.langgraph.skills.models import SkillResolveContext
from agent.langgraph.skills.planner_adapter import SkillPlanContext, SkillPlannerAdapter
from agent.langgraph.skills.registry import SkillRegistry
from agent.langgraph.skills.resolver import SkillResolver


def _quality_skill_set():
    result = SkillResolver(SkillRegistry()).resolve(
        SkillResolveContext(
            user_question="生成本月质量报告",
            tenant_id="tenant_001",
            skill_id="quality_report",
        )
    )
    assert result.resolved is True
    assert result.skill_set is not None
    return result.skill_set


def _plan_context(**overrides):
    context = {
        "user_question": "生成本月质量报告",
        "tenant_id": "tenant_001",
        "time_range": {"start": "2026-07-01", "end": "2026-07-31"},
        "report_date": "2026-07-31",
        "query_lang": "zh-CN",
        "skill_set": _quality_skill_set(),
        "agent_config": {},
    }
    context.update(overrides)
    return SkillPlanContext(**context)


def test_quality_report_plan_contains_declared_database_rag_and_report_steps():
    result = SkillPlannerAdapter().build_plan(_plan_context())

    assert result.success is True
    assert result.plan is not None
    assert result.plan.plan_id == "quality_report_plan"
    steps_by_id = {step.step_id: step for step in result.plan.steps}

    exception_step = steps_by_id["query_quality_exception_detail"]
    production_step = steps_by_id["query_production_output_for_quality"]
    inspection_step = steps_by_id["query_quality_inspection_for_fpy"]
    sop_step = steps_by_id["retrieve_quality_sop"]
    report_step = steps_by_id["generate_quality_report"]

    assert exception_step.tool == "database"
    assert exception_step.args.db_id == "qms_prod"
    assert exception_step.args.extra["table_name"] == "quality_exception"
    assert exception_step.args.extra["query_template_id"] == "quality_exception_detail"
    assert exception_step.can_parallel is True
    assert exception_step.depends_on == []

    assert production_step.tool == "database"
    assert production_step.args.db_id == "mes_prod"
    assert production_step.args.extra["table_name"] == "production_output"
    assert production_step.can_parallel is True
    assert production_step.depends_on == []

    assert inspection_step.tool == "database"
    assert inspection_step.args.db_id == "qms_prod"
    assert inspection_step.args.extra["table_name"] == "quality_inspection_result"
    assert inspection_step.can_parallel is True
    assert inspection_step.depends_on == []

    assert sop_step.tool == "rag"
    assert sop_step.args.kb_ids == ["quality_sop_kb"]
    assert sop_step.args.extra["rag_target_id"] == "quality_sop"
    assert sop_step.can_parallel is True
    assert sop_step.depends_on == []

    assert report_step.tool == "report"
    assert report_step.can_parallel is False
    assert set(report_step.depends_on) >= {
        "query_quality_exception_detail",
        "query_production_output_for_quality",
        "query_quality_inspection_for_fpy",
        "retrieve_quality_sop",
    }
    assert report_step.args.extra == {
        "skill_id": "quality_report",
        "data_skill_id": "quality_data_access",
        "retrieval_skill_id": "quality_knowledge_retrieval",
        "format": "markdown",
    }

    declared_tables = {
        "quality_exception",
        "production_output",
        "quality_inspection_result",
    }
    planned_tables = {
        step.args.extra["table_name"]
        for step in result.plan.steps
        if step.tool == "database"
    }
    assert planned_tables <= declared_tables


def test_plan_fails_with_required_missing_context():
    result = SkillPlannerAdapter().build_plan(
        _plan_context(tenant_id="", time_range=None)
    )

    assert result.success is False
    assert result.plan is None
    assert result.reason == "MISSING_REQUIRED_CONTEXT"
    assert set(result.missing_context) == {"tenant_id", "time_range"}


def test_report_plan_step_accepts_report_tool():
    step = PlanStep(step_id="generate_report", tool="report")

    assert step.tool == "report"


@pytest.mark.asyncio
async def test_planner_exposes_skill_aware_plan_as_hybrid_route():
    decision = await Planner().plan_with_skills(
        query="生成本月质量报告",
        llm_decision=RouteDecision(
            target="hybrid",
            confidence=0.9,
            source="llm",
            complexity="complex",
        ),
        skill_set=_quality_skill_set(),
        tenant_id="tenant_001",
        time_range={"start": "2026-07-01", "end": "2026-07-31"},
        report_date="2026-07-31",
    )

    assert decision.source == "planner"
    assert decision.target == "hybrid"
    assert decision.metadata["plan"]["plan_id"] == "quality_report_plan"
