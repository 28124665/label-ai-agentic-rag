"""ReportTool 单元测试 — Models 模块。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.models import (
    ChartSpec,
    ReportArtifact,
    ReportSection,
    ReportToolInput,
    ReportToolOutput,
    TableSpec,
    VerificationIssue,
    VerificationResult,
)


class TestReportToolInput:
    def test_minimal_input(self):
        """最小输入：仅 report_type。"""
        input_data: ReportToolInput = {
            "report_type": "business_analysis",
        }
        assert input_data["report_type"] == "business_analysis"

    def test_full_input(self):
        """完整输入。"""
        input_data: ReportToolInput = {
            "title": "近三个月质量异常分析报告",
            "report_type": "quality_analysis",
            "objective": "分析质量异常趋势",
            "audience": "管理层",
            "format": "markdown",
            "language": "zh_CN",
            "tenant_id": "tenant_001",
            "user_id": "user_001",
            "query_lang": "zh_CN",
            "evidence": [],
            "max_sections": 8,
            "max_charts": 4,
            "max_tables": 5,
        }
        assert input_data["title"] == "近三个月质量异常分析报告"
        assert input_data["format"] == "markdown"


class TestReportToolOutput:
    def test_success_output(self):
        """成功输出。"""
        output: ReportToolOutput = {
            "success": True,
            "error_message": "",
            "artifact": {"report_id": "rpt_001"},
            "summary": "报告摘要",
            "quality_score": 0.9,
            "section_count": 5,
            "chart_count": 2,
            "table_count": 1,
            "evidence_count": 3,
            "file_uri": "file:///tmp/report.md",
            "download_url": "/api/v1/reports/rpt_001/download",
        }
        assert output["success"] is True
        assert output["quality_score"] == 0.9

    def test_error_output(self):
        """错误输出。"""
        output: ReportToolOutput = {
            "success": False,
            "error_message": "证据不足",
            "error_code": "REPORT_EVIDENCE_INSUFFICIENT",
            "artifact": {},
            "summary": "",
            "quality_score": 0.0,
        }
        assert output["success"] is False
        assert output["error_code"] == "REPORT_EVIDENCE_INSUFFICIENT"


class TestReportArtifact:
    def test_artifact_minimal(self):
        """Artifact 最小化。"""
        artifact: ReportArtifact = {
            "report_id": "rpt_001",
            "title": "测试报告",
            "report_type": "business_analysis",
            "format": "markdown",
            "language": "zh_CN",
            "summary": "",
            "sections": [],
            "charts": [],
            "tables": [],
            "tenant_id": "tenant_001",
        }
        assert artifact["report_id"] == "rpt_001"
        assert artifact["title"] == "测试报告"


class TestReportSection:
    def test_section_with_evidence_refs(self):
        """带 evidence_refs 的章节。"""
        section: ReportSection = {
            "section_id": "overview",
            "title": "一、总体概况",
            "order": 0,
            "section_type": "overview",
            "content": "近三个月质量异常集中在设备参数波动和来料不稳定两个方向。",
            "evidence_refs": ["ev_db_001", "ev_rag_003"],
            "chart_refs": [],
            "table_refs": [],
            "confidence": 0.86,
        }
        assert len(section["evidence_refs"]) == 2
        assert section["confidence"] == 0.86


class TestChartSpec:
    def test_bar_chart(self):
        """柱状图。"""
        chart: ChartSpec = {
            "chart_id": "chart_001",
            "chart_type": "bar",
            "title": "月度异常趋势",
            "x_axis": "month",
            "y_axis": "count",
            "series": [{"name": "异常数", "data": [10, 20, 15]}],
            "data": [],
            "evidence_refs": ["ev_db_001"],
        }
        assert chart["chart_type"] == "bar"
        assert len(chart["evidence_refs"]) == 1


class TestTableSpec:
    def test_table_spec(self):
        """表格规格。"""
        table: TableSpec = {
            "table_id": "table_001",
            "title": "异常分布表",
            "columns": ["type", "count", "percentage"],
            "rows": [
                ["设备异常", 30, "30%"],
                ["来料异常", 50, "50%"],
            ],
            "evidence_refs": ["ev_db_002"],
        }
        assert len(table["columns"]) == 3
        assert len(table["rows"]) == 2


class TestVerificationResult:
    def test_passed_result(self):
        """通过验证。"""
        result: VerificationResult = {
            "passed": True,
            "score": 0.92,
            "issues": [],
            "warnings": [],
            "checks_performed": ["section_has_evidence_refs"],
        }
        assert result["passed"] is True

    def test_failed_result(self):
        """未通过验证。"""
        issue: VerificationIssue = {
            "code": "VER_SECTION_NO_REFS",
            "severity": "high",
            "message": "章节无 evidence_refs",
            "section_id": "overview",
        }
        result: VerificationResult = {
            "passed": False,
            "score": 0.5,
            "issues": [issue],
            "warnings": [],
            "checks_performed": ["section_has_evidence_refs"],
        }
        assert result["passed"] is False
        assert len(result["issues"]) == 1
