"""ReportTool 单元测试 — Exporter 模块。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.exporter import (
    ReportExporter,
    render_html,
    render_markdown,
)


def _make_artifact() -> dict:
    return {
        "report_id": "rpt_001",
        "title": "测试报告",
        "report_type": "business_analysis",
        "format": "markdown",
        "language": "zh_CN",
        "tenant_id": "tenant_001",
        "created_at": "2026-07-30T00:00:00Z",
        "summary": "本报告展示了业务分析的关键发现。",
        "sections": [
            {
                "section_id": "overview",
                "title": "一、业务概览",
                "order": 0,
                "section_type": "overview",
                "content": "近一个月业务增长稳定。",
                "evidence_refs": ["ev_001"],
                "chart_refs": [],
                "table_refs": [],
                "confidence": 0.85,
            }
        ],
        "charts": [
            {
                "chart_id": "c1",
                "chart_type": "bar",
                "title": "月度数据",
                "x_axis": "month",
                "y_axis": "value",
                "series": [{"name": "value", "data": [10, 20]}],
                "data": [
                    {"month": "Jan", "value": 10},
                    {"month": "Feb", "value": 20},
                ],
                "evidence_refs": ["ev_001"],
            }
        ],
        "tables": [
            {
                "table_id": "t1",
                "title": "数据表",
                "columns": ["category", "value"],
                "rows": [["A", 10], ["B", 20]],
                "evidence_refs": ["ev_001"],
            }
        ],
        "evidence_refs": ["ev_001"],
        "verification_result": {"passed": True, "score": 0.9, "issues": []},
    }


class TestRenderMarkdown:
    def test_basic_render(self):
        """基础 Markdown 渲染。"""
        md = render_markdown(_make_artifact())
        assert "# 测试报告" in md
        assert "## 一、业务概览" in md
        assert "近一个月业务增长稳定" in md
        assert "| category | value |" in md
        assert "ev_001" in md

    def test_includes_summary(self):
        """包含摘要。"""
        md = render_markdown(_make_artifact())
        assert "业务分析的关键发现" in md

    def test_includes_verification(self):
        """包含验证结果。"""
        md = render_markdown(_make_artifact())
        assert "验证结果" in md
        assert "是" in md  # passed=True

    def test_evidence_refs_section(self):
        """evidence 引用清单。"""
        md = render_markdown(_make_artifact())
        assert "Evidence 引用" in md


class TestRenderHtml:
    def test_basic_render(self):
        """基础 HTML 渲染。"""
        html = render_html(_make_artifact())
        assert "<!DOCTYPE html>" in html
        assert "测试报告" in html
        assert "近一个月业务增长稳定" in html

    def test_html_escapes_user_content(self):
        """HTML 转义用户内容（防 XSS）。"""
        artifact = _make_artifact()
        artifact["sections"][0]["content"] = "<script>alert('XSS')</script>"
        html = render_html(artifact)
        # <script> 标签应被转义
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_escapes_title(self):
        """HTML 转义标题。"""
        artifact = _make_artifact()
        artifact["title"] = "Test <Report> & Analysis"
        html = render_html(artifact)
        assert "<Report>" not in html
        assert "&lt;Report&gt;" in html
        assert "&amp;" in html

    def test_html_csp_blocks_scripts(self):
        """CSP 禁止脚本。"""
        html = render_html(_make_artifact())
        assert "Content-Security-Policy" in html
        assert "script-src 'none'" in html

    def test_html_escapes_table_cells(self):
        """表格单元格转义。"""
        artifact = _make_artifact()
        artifact["tables"][0]["rows"] = [["<b>XSS</b>", "value"]]
        html = render_html(artifact)
        assert "<b>XSS</b>" not in html
        assert "&lt;b&gt;XSS&lt;/b&gt;" in html


class TestReportExporter:
    def test_export_markdown(self):
        """导出 Markdown。"""
        exporter = ReportExporter()
        content, ext = exporter.export(_make_artifact(), format="markdown")
        assert ext == "md"
        assert "# 测试报告" in content

    def test_export_html(self):
        """导出 HTML。"""
        exporter = ReportExporter()
        content, ext = exporter.export(_make_artifact(), format="html")
        assert ext == "html"
        assert "<!DOCTYPE html>" in content

    def test_export_unsupported_format(self):
        """不支持的格式。"""
        exporter = ReportExporter()
        with pytest.raises(ValueError):
            exporter.export(_make_artifact(), format="pdf")
