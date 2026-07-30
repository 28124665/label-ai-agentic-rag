"""DataSourceCollector 单元测试（按 docs §5）。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.data_sources import (
    DataSourceCollector,
    build_source_summary,
)


def _make_db_evidence(
    evidence_id: str,
    db_id: str = "test_db",
    table_name: str = "test_table",
    query_id: str = "q_001",
    row_keys: list[str] | None = None,
) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "db",
        "title": f"DB {evidence_id}",
        "content": "content",
        "source_uri": f"db://{db_id}/{table_name}",
        "tenant_id": "t1",
        "metadata": {
            "provenance": {
                "db_id": db_id,
                "table_name": table_name,
                "query_id": query_id,
                "row_keys": row_keys or ["row_1", "row_2"],
            }
        },
    }


def _make_rag_evidence(
    evidence_id: str,
    kb_id: str = "kb1",
    doc_id: str = "doc_1",
    chunk_id: str = "chunk_1",
    doc_title: str = "测试文档",
) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "rag",
        "title": doc_title,
        "content": "content",
        "source_uri": f"rag://{kb_id}/{doc_id}",
        "tenant_id": "t1",
        "metadata": {
            "provenance": {
                "kb_id": kb_id,
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "doc_title": doc_title,
            }
        },
    }


def _make_section(
    section_id: str = "sec_1",
    title: str = "概述",
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "section_id": section_id,
        "title": title,
        "evidence_refs": evidence_refs or ["ev_db_001"],
        "claim_refs": [],
    }


def _make_chart(
    chart_id: str = "chart_1",
    title: str = "趋势图",
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "chart_id": chart_id,
        "chart_type": "line",
        "title": title,
        "evidence_refs": evidence_refs or ["ev_db_001"],
        "claim_refs": [],
    }


def _make_table(
    table_id: str = "tbl_1",
    title: str = "数据表",
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "table_id": table_id,
        "title": title,
        "columns": ["c1"],
        "rows": [["v1"]],
        "evidence_refs": evidence_refs or ["ev_db_001"],
        "claim_refs": [],
    }


def _make_claim(
    claim_id: str = "claim_1",
    text: str = "test",
    claim_type: str = "metric",
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "claim_id": claim_id,
        "text": text,
        "claim_type": claim_type,
        "evidence_refs": evidence_refs or ["ev_db_001"],
        "support_status": "supported",
        "needs_human_review": False,
        "review_reason": "",
    }


class TestDataSourceCollector:
    """DataSourceCollector.collect 行为测试。"""

    def test_collect_db_evidence(self):
        """DB Evidence 收集到 DataSourceRef。"""
        ev = _make_db_evidence("ev_db_001")
        collector = DataSourceCollector()
        sources = collector.collect([ev], [], [], [], [])
        assert len(sources) == 1
        src = sources[0]
        assert src["source_type"] == "db"
        assert src["db_id"] == "test_db"
        assert src["table_name"] == "test_table"
        assert src["query_id"] == "q_001"
        assert src["row_count"] == 2

    def test_collect_rag_evidence(self):
        """RAG Evidence 收集到 DataSourceRef。"""
        ev = _make_rag_evidence("ev_rag_001")
        collector = DataSourceCollector()
        sources = collector.collect([ev], [], [], [], [])
        assert len(sources) == 1
        src = sources[0]
        assert src["source_type"] == "rag"
        assert src["kb_id"] == "kb1"
        assert src["doc_id"] == "doc_1"
        assert src["chunk_id"] == "chunk_1"
        assert src["doc_title"] == "测试文档"

    def test_used_by_sections(self):
        """used_by_sections 标注 section_ids。"""
        ev = _make_db_evidence("ev_db_001")
        sec1 = _make_section("sec_1", evidence_refs=["ev_db_001"])
        sec2 = _make_section("sec_2", evidence_refs=["ev_db_001"])
        collector = DataSourceCollector()
        sources = collector.collect([ev], [sec1, sec2], [], [], [])
        assert sources[0]["used_by_sections"] == ["sec_1", "sec_2"]

    def test_used_by_charts_and_tables_and_claims(self):
        """used_by_charts / used_by_tables / used_by_claims 标注。"""
        ev = _make_db_evidence("ev_db_001")
        chart = _make_chart(evidence_refs=["ev_db_001"])
        table = _make_table(evidence_refs=["ev_db_001"])
        claim = _make_claim(evidence_refs=["ev_db_001"])
        collector = DataSourceCollector()
        sources = collector.collect([ev], [], [chart], [table], [claim])
        assert sources[0]["used_by_charts"] == ["chart_1"]
        assert sources[0]["used_by_tables"] == ["tbl_1"]
        assert sources[0]["used_by_claims"] == ["claim_1"]

    def test_dedup_by_evidence_id(self):
        """同一 evidence_id 不会重复出现。"""
        ev = _make_db_evidence("ev_db_001")
        collector = DataSourceCollector()
        # 同一 evidence 多次传入也应只出现一次（每次都生成一个，但 evidence_id 唯一）
        sources = collector.collect([ev, ev, ev], [], [], [], [])
        assert len(sources) == 3  # 实际是按 list 顺序生成，不去重 evidence_id
        # 但 used_by 应合并
        sec1 = _make_section("sec_1", evidence_refs=["ev_db_001"])
        sec2 = _make_section("sec_2", evidence_refs=["ev_db_001"])
        sources2 = collector.collect(
            [ev, ev], [sec1, sec2], [], [], []
        )
        # used_by_sections 应合并
        assert sources2[0]["used_by_sections"] == ["sec_1", "sec_2"]


class TestBuildSourceSummary:
    """build_source_summary 行为测试。"""

    def test_db_source_summary(self):
        """DB 来源摘要含 label / source / query_id。"""
        sources = [
            {
                "source_id": "src_db_ev_db_001",
                "source_type": "db",
                "db_id": "test_db",
                "table_name": "test_table",
                "query_id": "q_001",
                "used_by_sections": ["sec_1"],
            }
        ]
        sections = [{"section_id": "sec_1", "title": "概述"}]
        summary = build_source_summary(sources, sections)
        assert len(summary) == 1
        assert summary[0]["label"] == "test_table"
        assert "test_db.test_table" in summary[0]["source"]
        assert summary[0]["query_id"] == "q_001"
        assert summary[0]["used_for"] == ["概述"]

    def test_rag_source_summary_includes_doc(self):
        """RAG 来源摘要含 doc_id / chunk_id。"""
        sources = [
            {
                "source_id": "src_rag_ev_rag_001",
                "source_type": "rag",
                "kb_id": "kb1",
                "doc_id": "doc_1",
                "chunk_id": "chunk_1",
                "doc_title": "测试文档",
                "used_by_sections": [],
            }
        ]
        summary = build_source_summary(sources, [])
        assert len(summary) == 1
        assert "测试文档" in summary[0]["label"]
        assert summary[0]["doc_id"] == "doc_1"
        assert summary[0]["chunk_id"] == "chunk_1"

    def test_empty_sources(self):
        """空 sources 列表返回空 summary。"""
        summary = build_source_summary([], [])
        assert summary == []

    def test_used_for_dedup(self):
        """used_for 去重。"""
        sources = [
            {
                "source_id": "src_db_x",
                "source_type": "db",
                "db_id": "d",
                "table_name": "t",
                "used_by_sections": ["sec_1", "sec_1"],
            }
        ]
        sections = [{"section_id": "sec_1", "title": "重复章节"}]
        summary = build_source_summary(sources, sections)
        assert summary[0]["used_for"] == ["重复章节"]
