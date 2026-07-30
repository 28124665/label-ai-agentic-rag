"""ReportTool 单元测试 — Verifier 模块。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.verifier import ReportVerifier


def _make_evidence(evidence_id: str = "ev_001", source_type: str = "db") -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "title": f"Evidence {evidence_id}",
        "content": "test content",
    }


def _make_section(
    section_id: str = "overview",
    refs: list[str] | None = None,
    content: str = "测试内容",
) -> dict:
    """构造测试章节。

    注意：`refs=None` 时使用默认 ["ev_001"]；
    `refs=[]` 时返回空列表（用于测试无引用场景）。
    """
    if refs is None:
        section_refs = ["ev_001"]
    else:
        section_refs = refs
    return {
        "section_id": section_id,
        "title": f"Section {section_id}",
        "content": content,
        "evidence_refs": section_refs,
    }


def _make_artifact(
    sections: list[dict] | None = None,
    charts: list[dict] | None = None,
    tables: list[dict] | None = None,
) -> dict:
    return {
        "report_id": "rpt_001",
        "title": "测试报告",
        "sections": sections or [_make_section()],
        "charts": charts or [],
        "tables": tables or [],
        "summary": "测试摘要",
    }


class TestReportVerifier:
    def test_pass_valid_artifact(self):
        """合法 artifact 通过验证。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact(
            sections=[_make_section(refs=["ev_001"])],
            charts=[
                {
                    "chart_id": "c1",
                    "title": "Chart 1",
                    "chart_type": "bar",
                    "evidence_refs": ["ev_001"],
                }
            ],
            tables=[
                {
                    "table_id": "t1",
                    "title": "Table 1",
                    "columns": ["a"],
                    "rows": [[1]],
                    "evidence_refs": ["ev_001"],
                }
            ],
        )
        result = verifier.verify(artifact, evidence)
        assert result["passed"] is True
        assert result["score"] >= 0.8

    def test_section_without_refs_fails(self):
        """章节无 refs 验证失败。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact(
            sections=[_make_section(refs=[])],
        )
        result = verifier.verify(artifact, evidence)
        assert result["passed"] is False
        assert any(i["code"] == "VER_SECTION_NO_REFS" for i in result["issues"])

    def test_chart_without_refs_fails(self):
        """图表无 refs 验证失败。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact(
            charts=[
                {
                    "chart_id": "c1",
                    "title": "Chart 1",
                    "chart_type": "bar",
                    "evidence_refs": [],
                }
            ],
        )
        result = verifier.verify(artifact, evidence)
        assert result["passed"] is False
        assert any(i["code"] == "VER_CHART_NO_REFS" for i in result["issues"])

    def test_table_without_refs_fails(self):
        """表格无 refs 验证失败。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact(
            tables=[
                {
                    "table_id": "t1",
                    "title": "Table 1",
                    "columns": ["a"],
                    "rows": [[1]],
                    "evidence_refs": [],
                }
            ],
        )
        result = verifier.verify(artifact, evidence)
        assert result["passed"] is False
        assert any(i["code"] == "VER_TABLE_NO_REFS" for i in result["issues"])

    def test_refs_not_in_evidence_fails(self):
        """引用不存在的 evidence_id 验证失败。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence("ev_real")]
        artifact = _make_artifact(
            sections=[_make_section(refs=["ev_fake"])],
        )
        result = verifier.verify(artifact, evidence)
        assert result["passed"] is False
        assert any(i["code"] == "VER_REF_NOT_EXIST" for i in result["issues"])

    def test_numbers_without_refs_warning(self):
        """章节有数字但无 refs 产生 high issue。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact(
            sections=[_make_section(refs=[], content="异常率上升 30%，环比增长 5.2%")],
        )
        result = verifier.verify(artifact, evidence)
        assert result["passed"] is False
        assert any(i["code"] == "VER_NUMBERS_NO_REFS" for i in result["issues"])

    def test_sensitive_data_critical_fails(self):
        """critical 敏感信息导致失败。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact(
            sections=[
                _make_section(
                    content="API Key: sk-abcdefghijklmnopqrstuvwxyz123456",
                )
            ],
        )
        result = verifier.verify(artifact, evidence)
        assert result["passed"] is False
        assert any(
            "SENSITIVE" in i.get("code", "") for i in result["issues"]
        )

    def test_quantity_limits(self):
        """数量限制检查。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        # 创建超限 sections
        sections = [
            _make_section(section_id=f"s{i}", refs=["ev_001"])
            for i in range(15)
        ]
        artifact = _make_artifact(sections=sections)
        result = verifier.verify(artifact, evidence, max_sections=10)
        assert any(
            i["code"] == "VER_TOO_MANY_SECTIONS" for i in result["issues"]
        )

    def test_duplicate_sections(self):
        """重复章节检测。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact(
            sections=[
                _make_section(section_id="dup", refs=["ev_001"]),
                _make_section(section_id="dup", refs=["ev_001"]),
            ],
        )
        result = verifier.verify(artifact, evidence)
        assert any(
            i["code"] == "VER_DUPLICATE_SECTIONS" for i in result["issues"]
        )

    def test_empty_content_warning(self):
        """空内容产生 medium issue。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact(
            sections=[_make_section(content="")],
        )
        result = verifier.verify(artifact, evidence)
        assert any(i["code"] == "VER_EMPTY_CONTENT" for i in result["issues"])

    def test_checks_performed_recorded(self):
        """已执行的检查被记录。"""
        verifier = ReportVerifier()
        evidence = [_make_evidence()]
        artifact = _make_artifact()
        result = verifier.verify(artifact, evidence)
        assert len(result["checks_performed"]) == 10
        assert "section_has_evidence_refs" in result["checks_performed"]
        assert "sensitive_data_scan" in result["checks_performed"]
