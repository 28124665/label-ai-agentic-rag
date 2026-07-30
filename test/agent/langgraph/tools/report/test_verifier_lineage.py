"""ReportVerifier 扩展：Claim Lineage Check 单元测试（按 docs §4）。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.verifier import ReportVerifier


def _make_evidence(evidence_id: str, confidence: float = 0.9) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "db",
        "title": f"ev {evidence_id}",
        "content": "test",
        "structured_data": {"columns": ["a"], "rows": [{"a": 1}]},
        "source_uri": f"db://{evidence_id}",
        "tenant_id": "t1",
        "confidence": confidence,
    }


def _make_section(
    section_id: str = "sec_1",
    title: str = "概述",
    content: str = "本月质量良好。",
    evidence_refs: list[str] | None = None,
) -> dict:
    return {
        "section_id": section_id,
        "title": title,
        "order": 0,
        "section_type": "overview",
        "content": content,
        "evidence_refs": list(evidence_refs) if evidence_refs is not None else ["ev_001"],
    }


def _make_claim(
    claim_id: str = "claim_1",
    claim_type: str = "fact",
    evidence_refs: list[str] | None = None,
    support_status: str = "supported",
    needs_human_review: bool = False,
    review_reason: str = "",
    confidence: float = 0.9,
) -> dict:
    return {
        "claim_id": claim_id,
        "text": "test",
        "claim_type": claim_type,
        "evidence_refs": list(evidence_refs) if evidence_refs is not None else ["ev_001"],
        "support_status": support_status,
        "needs_human_review": needs_human_review,
        "review_reason": review_reason,
        "confidence": confidence,
    }


class TestClaimLineageCheck:
    """_check_claim_lineage 行为测试。"""

    def test_metric_claim_without_evidence_high_issue(self):
        """metric Claim 无 evidence → high issue。"""
        verifier = ReportVerifier()
        claims = [_make_claim(claim_type="metric", evidence_refs=[])]
        artifact = {"sections": [], "charts": [], "tables": [], "claims": claims}
        result = verifier.verify(
            artifact=artifact,
            evidence=[_make_evidence("ev_001")],
        )
        codes = [i["code"] for i in result["issues"]]
        assert "VER_CLAIM_MISSING_EVIDENCE" in codes

    def test_comparison_claim_without_evidence_high_issue(self):
        """comparison Claim 无 evidence → high issue。"""
        verifier = ReportVerifier()
        claims = [_make_claim(claim_type="comparison", evidence_refs=[])]
        artifact = {"sections": [], "charts": [], "tables": [], "claims": claims}
        result = verifier.verify(
            artifact=artifact,
            evidence=[_make_evidence("ev_001")],
        )
        codes = [i["code"] for i in result["issues"]]
        assert "VER_CLAIM_MISSING_EVIDENCE" in codes

    def test_fact_claim_without_evidence_default_removed(self):
        """fact Claim 无 evidence → 默认删除（high issue）。"""
        verifier = ReportVerifier()
        claims = [_make_claim(claim_type="fact", evidence_refs=[])]
        artifact = {"sections": [], "charts": [], "tables": [], "claims": claims}
        result = verifier.verify(
            artifact=artifact,
            evidence=[_make_evidence("ev_001")],
        )
        codes = [i["code"] for i in result["issues"]]
        assert "VER_CLAIM_MISSING_EVIDENCE" in codes

    def test_recommendation_claim_without_evidence_low_warning(self):
        """recommendation Claim 无 evidence → low warning（标记 unsupported）。"""
        verifier = ReportVerifier()
        claims = [_make_claim(
            claim_type="recommendation",
            evidence_refs=[],
            support_status="unsupported",
        )]
        artifact = {"sections": [], "charts": [], "tables": [], "claims": claims}
        result = verifier.verify(
            artifact=artifact,
            evidence=[_make_evidence("ev_001")],
        )
        codes = [i["code"] for i in result["issues"]]
        assert "VER_CLAIM_UNSUPPORTED" in codes

    def test_claim_references_nonexistent_evidence(self):
        """Claim 引用不存在的 evidence_id → high issue。"""
        verifier = ReportVerifier()
        claims = [_make_claim(claim_type="metric", evidence_refs=["ev_nonexistent"])]
        artifact = {"sections": [], "charts": [], "tables": [], "claims": claims}
        result = verifier.verify(
            artifact=artifact,
            evidence=[_make_evidence("ev_001")],
        )
        codes = [i["code"] for i in result["issues"]]
        assert "VER_CLAIM_EVIDENCE_NOT_EXIST" in codes

    def test_low_confidence_marks_human_review_issue(self):
        """低置信度 Claim → medium issue（needs_human_review）。"""
        verifier = ReportVerifier()
        claims = [_make_claim(
            claim_type="metric",
            evidence_refs=["ev_001"],
            needs_human_review=True,
            review_reason="置信度 0.50 低于阈值 0.70",
            confidence=0.5,
        )]
        artifact = {"sections": [], "charts": [], "tables": [], "claims": claims}
        result = verifier.verify(
            artifact=artifact,
            evidence=[_make_evidence("ev_001", confidence=0.5)],
        )
        codes = [i["code"] for i in result["issues"]]
        assert "VER_CLAIM_NEEDS_HUMAN_REVIEW" in codes

    def test_valid_claim_no_issues(self):
        """有效 Claim 无 issue。"""
        verifier = ReportVerifier()
        claims = [_make_claim(
            claim_type="metric",
            evidence_refs=["ev_001"],
            confidence=0.9,
        )]
        artifact = {
            "sections": [_make_section(evidence_refs=["ev_001"])],
            "charts": [],
            "tables": [],
            "claims": claims,
        }
        result = verifier.verify(
            artifact=artifact,
            evidence=[_make_evidence("ev_001", confidence=0.9)],
        )
        codes = [i["code"] for i in result["issues"]]
        # 不应有 Claim 相关 issue
        claim_codes = [c for c in codes if c.startswith("VER_CLAIM_")]
        assert claim_codes == []
