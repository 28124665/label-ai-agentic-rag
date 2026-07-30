"""ReportTool 单元测试 — Chart / Table Builder 模块。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.chart_builder import ChartBuilder
from agent.langgraph.tools.report.table_builder import TableBuilder


def _make_db_evidence(
    evidence_id: str = "ev_db_001",
    rows: list | None = None,
    columns: list | None = None,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "db",
        "title": f"DB 数据 {evidence_id}",
        "content": "DB 查询结果",
        "structured_data": {
            "columns": columns or ["category", "value"],
            "rows": rows or [["A", 10], ["B", 20], ["C", 15]],
        },
        "source_uri": f"db://test/{evidence_id}",
    }


def _make_rag_evidence(evidence_id: str = "ev_rag_001") -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "rag",
        "title": "知识库文档",
        "content": "知识库内容",
        "structured_data": {},
    }


class TestChartBuilder:
    def test_empty_evidence(self):
        """空 evidence 不生成图表。"""
        builder = ChartBuilder()
        charts, failed = builder.build([])
        assert charts == []
        assert failed == []

    def test_rag_only_no_charts(self):
        """仅有 RAG evidence（无结构化数据）不生成图表。"""
        builder = ChartBuilder()
        charts, failed = builder.build([_make_rag_evidence()])
        assert charts == []
        assert failed == []

    def test_db_kpi_chart(self):
        """DB 单行生成 KPI 图表。"""
        builder = ChartBuilder()
        evidence = _make_db_evidence(
            rows=[{"metric_a": 100, "metric_b": 200}],
            columns=["metric_a", "metric_b"],
        )
        charts, failed = builder.build([evidence])
        assert len(charts) == 1
        assert charts[0]["chart_type"] == "kpi"
        assert len(charts[0]["series"]) == 2
        assert evidence["evidence_id"] in charts[0]["evidence_refs"]

    def test_db_bar_chart(self):
        """DB 双列生成柱状图。"""
        builder = ChartBuilder()
        evidence = _make_db_evidence(
            rows=[
                {"category": "A", "value": 10},
                {"category": "B", "value": 20},
                {"category": "C", "value": 15},
            ],
            columns=["category", "value"],
        )
        charts, failed = builder.build([evidence])
        assert len(charts) == 1
        assert charts[0]["chart_type"] == "bar"
        assert charts[0]["x_axis"] == "category"
        assert charts[0]["y_axis"] == "value"

    def test_db_table_chart(self):
        """DB 多列生成表格图表。"""
        builder = ChartBuilder()
        # 3 行 3 列才会走到 table 分支
        evidence = _make_db_evidence(
            rows=[
                {"a": 1, "b": 2, "c": 3},
                {"a": 4, "b": 5, "c": 6},
                {"a": 7, "b": 8, "c": 9},
            ],
            columns=["a", "b", "c"],
        )
        charts, failed = builder.build([evidence])
        assert len(charts) == 1
        assert charts[0]["chart_type"] == "table"

    def test_max_charts_limit(self):
        """max_charts 限制。"""
        builder = ChartBuilder()
        evidences = [_make_db_evidence(evidence_id=f"ev_db_{i}") for i in range(5)]
        charts, failed = builder.build(evidences, max_charts=2)
        assert len(charts) <= 2


class TestTableBuilder:
    def test_empty_evidence(self):
        """空 evidence 不生成表格。"""
        builder = TableBuilder()
        tables, failed = builder.build([])
        assert tables == []

    def test_rag_only_no_tables(self):
        """仅有 RAG evidence 不生成表格。"""
        builder = TableBuilder()
        tables, failed = builder.build([_make_rag_evidence()])
        assert tables == []

    def test_db_table(self):
        """DB evidence 生成表格。"""
        builder = TableBuilder()
        evidence = _make_db_evidence(
            rows=[
                {"a": 1, "b": "x"},
                {"a": 2, "b": "y"},
            ],
            columns=["a", "b"],
        )
        tables, failed = builder.build([evidence])
        assert len(tables) == 1
        assert tables[0]["columns"] == ["a", "b"]
        assert tables[0]["rows"] == [[1, "x"], [2, "y"]]
        assert evidence["evidence_id"] in tables[0]["evidence_refs"]

    def test_max_rows_truncation(self):
        """max_rows 截断。"""
        builder = TableBuilder(max_rows=3)
        evidence = _make_db_evidence(
            rows=[{"a": i, "b": i * 2} for i in range(10)],
            columns=["a", "b"],
        )
        tables, failed = builder.build([evidence])
        assert len(tables[0]["rows"]) == 3

    def test_max_tables_limit(self):
        """max_tables 限制。"""
        builder = TableBuilder()
        evidences = [_make_db_evidence(evidence_id=f"ev_db_{i}") for i in range(5)]
        tables, failed = builder.build(evidences, max_tables=2)
        assert len(tables) <= 2
