"""Claim Builder / Verifier 单元测试（按 docs §4）。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.claims import (
    ClaimBuilder,
    needs_human_review_for_claim,
)
from agent.langgraph.tools.report.models import Claim, ClaimType, SupportStatus


def _make_evidence(
    evidence_id: str,
    source_type: str = "db",
    confidence: float = 0.9,
    db_id: str = "db1",
    table_name: str = "tbl",
    query_id: str = "q1",
    row_keys: list[str] | None = None,
) -> dict:
    """构造测试 Evidence。"""
    metadata: dict = {
        "source_target_id": "t1",
        "confidence": confidence,
    }
    if source_type == "db":
        metadata["provenance"] = {
            "db_id": db_id,
            "table_name": table_name,
            "query_id": query_id,
            "row_keys": row_keys or ["row_1"],
        }
    elif source_type == "rag":
        metadata["provenance"] = {
            "kb_id": "kb1",
            "doc_id": "doc_1",
            "chunk_id": "chunk_1",
            "doc_title": "测试文档",
        }
    return {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "title": f"evidence {evidence_id}",
        "content": "测试内容",
        "structured_data": {"columns": ["a"], "rows": [{"a": 1}]},
        "source_uri": f"db://test/{evidence_id}",
        "tenant_id": "tenant_001",
        "confidence": confidence,
        "metadata": metadata,
    }


def _make_section(
    section_id: str = "sec_1",
    title: str = "概述",
    content: str = "本月质量良好。",
    evidence_refs: list[str] | None = None,
) -> dict:
    """构造测试 Section。"""
    return {
        "section_id": section_id,
        "title": title,
        "order": 0,
        "section_type": "overview",
        "content": content,
        "evidence_refs": list(evidence_refs) if evidence_refs is not None else ["ev_001"],
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
        "evidence_refs": list(evidence_refs) if evidence_refs is not None else ["ev_001"],
    }


def _make_table(
    table_id: str = "tbl_1",
    title: str = "数据表",
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "table_id": table_id,
        "title": title,
        "columns": ["col1"],
        "rows": [["v1"]],
        "evidence_refs": list(evidence_refs) if evidence_refs is not None else ["ev_001"],
    }


class TestClaimBuilder:
    """ClaimBuilder 行为测试。"""

    def test_section_with_evidence_generates_claim(self):
        """章节有 evidence → 生成 fact Claim。"""
        ev = _make_evidence("ev_001", confidence=0.9)
        section = _make_section(evidence_refs=["ev_001"])
        builder = ClaimBuilder()
        claims = builder.build([section], [], [], [ev])
        assert len(claims) == 1
        assert claims[0]["claim_type"] == "fact"
        assert claims[0]["evidence_refs"] == ["ev_001"]
        # section.claim_refs 双向绑定
        assert "claim_refs" in section
        assert claims[0]["claim_id"] in section["claim_refs"]

    def test_metric_chart_claim(self):
        """图表 Claim 默认为 metric 类型。"""
        ev = _make_evidence("ev_001", confidence=0.9)
        chart = _make_chart(evidence_refs=["ev_001"])
        builder = ClaimBuilder()
        claims = builder.build([], [chart], [], [ev])
        assert len(claims) == 1
        assert claims[0]["claim_type"] == "metric"

    def test_table_claim(self):
        """表格 Claim 默认为 fact 类型。"""
        ev = _make_evidence("ev_001", confidence=0.9)
        table = _make_table(evidence_refs=["ev_001"])
        builder = ClaimBuilder()
        claims = builder.build([], [], [table], [ev])
        assert len(claims) == 1
        assert claims[0]["claim_type"] == "fact"

    def test_fact_section_without_evidence_removed(self):
        """fact 章节无 evidence → 被删除。"""
        section = _make_section(evidence_refs=[])
        builder = ClaimBuilder()
        claims = builder.build([section], [], [], [])
        assert len(claims) == 0

    def test_metric_chart_without_evidence_removed(self):
        """metric 图表无 evidence → 被删除。"""
        chart = _make_chart(evidence_refs=[])
        builder = ClaimBuilder()
        claims = builder.build([], [chart], [], [])
        assert len(claims) == 0

    def test_low_confidence_marks_human_review(self):
        """低置信度 Claim 标记 needs_human_review。"""
        ev = _make_evidence("ev_001", confidence=0.5)
        section = _make_section(evidence_refs=["ev_001"])
        builder = ClaimBuilder(low_confidence_threshold=0.7)
        claims = builder.build([section], [], [], [ev])
        assert len(claims) == 1
        assert claims[0]["needs_human_review"] is True
        assert "0.50" in claims[0]["review_reason"]
        assert "0.70" in claims[0]["review_reason"]

    def test_high_confidence_no_human_review(self):
        """高置信度 Claim 不标记 needs_human_review。"""
        ev = _make_evidence("ev_001", confidence=0.95)
        section = _make_section(evidence_refs=["ev_001"])
        builder = ClaimBuilder(low_confidence_threshold=0.7)
        claims = builder.build([section], [], [], [ev])
        assert len(claims) == 1
        assert claims[0]["needs_human_review"] is False
        assert claims[0]["confidence"] == 0.95

    def test_evidence_sources_summary(self):
        """evidence_sources 包含 provenance 摘要。"""
        ev = _make_evidence(
            "ev_001",
            source_type="db",
            db_id="test_db",
            table_name="test_table",
            query_id="q_001",
        )
        section = _make_section(evidence_refs=["ev_001"])
        builder = ClaimBuilder()
        claims = builder.build([section], [], [], [ev])
        assert len(claims[0]["evidence_sources"]) == 1
        src = claims[0]["evidence_sources"][0]
        assert src["evidence_id"] == "ev_001"
        assert src["db_id"] == "test_db"
        assert src["table_name"] == "test_table"
        assert src["query_id"] == "q_001"

    def test_multiple_evidence_aggregation(self):
        """多 evidence 关联同一章节时合并。"""
        evs = [
            _make_evidence("ev_001", confidence=0.9),
            _make_evidence("ev_002", confidence=0.8),
        ]
        section = _make_section(evidence_refs=["ev_001", "ev_002"])
        builder = ClaimBuilder()
        claims = builder.build([section], [], [], evs)
        assert len(claims) == 1
        # confidence = (0.9 + 0.8) / 2 = 0.85
        assert claims[0]["confidence"] == 0.85

    def test_chart_and_table_claim_refs_bound(self):
        """Chart / Table 的 claim_refs 双向绑定。"""
        ev = _make_evidence("ev_001", confidence=0.9)
        chart = _make_chart(evidence_refs=["ev_001"])
        table = _make_table(evidence_refs=["ev_001"])
        builder = ClaimBuilder()
        claims = builder.build([], [chart], [table], [ev])
        assert len(claims) == 2
        # chart.claim_refs 已绑定
        assert "claim_refs" in chart
        # table.claim_refs 已绑定
        assert "claim_refs" in table


class TestNeedsHumanReviewForClaim:
    """needs_human_review_for_claim 辅助函数。"""

    def test_needs_human_review_flag(self):
        """needs_human_review=True 触发。"""
        claim: Claim = {
            "claim_id": "c1",
            "text": "test",
            "claim_type": "fact",
            "evidence_refs": ["e1"],
            "support_status": "supported",
            "needs_human_review": True,
        }
        assert needs_human_review_for_claim(claim) is True

    def test_unsupported_status_triggers(self):
        """unsupported 状态触发。"""
        claim: Claim = {
            "claim_id": "c1",
            "text": "test",
            "claim_type": "recommendation",
            "evidence_refs": [],
            "support_status": "unsupported",
            "needs_human_review": False,
        }
        assert needs_human_review_for_claim(claim) is True

    def test_normal_claim_no_review(self):
        """正常 supported Claim 不需要审核。"""
        claim: Claim = {
            "claim_id": "c1",
            "text": "test",
            "claim_type": "fact",
            "evidence_refs": ["e1"],
            "support_status": "supported",
            "needs_human_review": False,
        }
        assert needs_human_review_for_claim(claim) is False
