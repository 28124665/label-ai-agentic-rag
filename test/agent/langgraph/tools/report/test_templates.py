"""ReportTool 单元测试 — Templates / Planner 模块。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.planner import ReportPlanner
from agent.langgraph.tools.report.templates import (
    REPORT_TYPE_TITLES,
    SECTION_TEMPLATES,
    build_report_plan,
    get_default_title,
    get_template,
    list_report_types,
)


class TestTemplates:
    def test_list_report_types(self):
        """列出所有报告类型。"""
        types = list_report_types()
        assert "business_analysis" in types
        assert "quality_analysis" in types
        assert "trend_analysis" in types
        assert "knowledge_summary" in types
        assert "management_briefing" in types

    def test_get_template_quality_analysis(self):
        """获取质量分析模板。"""
        template = get_template("quality_analysis")
        assert len(template) > 0
        section_ids = [s["section_id"] for s in template]
        assert "overview" in section_ids
        assert "root_cause" in section_ids
        assert "recommendation" in section_ids

    def test_get_default_title(self):
        """获取默认标题。"""
        assert get_default_title("quality_analysis") == "质量分析报告"
        assert get_default_title("business_analysis") == "业务分析报告"

    def test_build_report_plan_with_evidence(self):
        """带 evidence 的报告计划。"""
        evidence = [
            {
                "evidence_id": "ev_db_001",
                "source_type": "db",
                "title": "质量异常数据",
            },
            {
                "evidence_id": "ev_rag_001",
                "source_type": "rag",
                "title": "质量管理规范",
            },
        ]
        plan = build_report_plan(
            report_type="quality_analysis",
            title="质量异常分析",
            evidence=evidence,
        )
        assert plan["title"] == "质量异常分析"
        assert len(plan["sections"]) > 0
        assert plan["report_type"] == "quality_analysis"

    def test_build_report_plan_empty_evidence_has_warnings(self):
        """空 evidence 时产生 warnings。"""
        plan = build_report_plan(
            report_type="business_analysis",
            title="",
            evidence=[],
        )
        assert len(plan["warnings"]) > 0
        # 默认标题兜底
        assert plan["title"] == "业务分析报告"

    def test_build_report_plan_respects_max_sections(self):
        """max_sections 限制。"""
        plan = build_report_plan(
            report_type="business_analysis",
            title="",
            evidence=[],
            max_sections=2,
        )
        assert len(plan["sections"]) <= 2


class TestReportPlanner:
    def test_planner_basic(self):
        """Planner 基础功能。"""
        planner = ReportPlanner()
        plan = planner.plan(
            report_type="trend_analysis",
            title="趋势报告",
            evidence=[{"evidence_id": "ev_1", "source_type": "db"}],
        )
        assert plan["title"] == "趋势报告"
        assert plan["report_type"] == "trend_analysis"
        assert len(plan["sections"]) > 0

    def test_planner_with_objective(self):
        """Planner 接受 objective。"""
        planner = ReportPlanner()
        plan = planner.plan(
            report_type="business_analysis",
            title="业务分析",
            evidence=[{"evidence_id": "ev_1", "source_type": "db"}],
            objective="分析近一季度业务增长",
        )
        # objective 应附加到第一个章节
        first_section = plan["sections"][0]
        assert first_section.get("objective") == "分析近一季度业务增长"

    def test_planner_with_max_sections_override(self):
        """max_sections 覆盖默认。"""
        planner = ReportPlanner({"max_sections": 5})
        plan = planner.plan(
            report_type="business_analysis",
            title="测试",
            evidence=[],
            max_sections=3,
        )
        assert len(plan["sections"]) <= 3
