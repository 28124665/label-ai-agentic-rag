"""EvidenceProvenance 单元测试（按 docs §4.1）。"""
from __future__ import annotations

import pytest

from agent.langgraph.evidence.provenance import (
    attach_provenance_to_evidence,
    build_db_provenance,
    build_rag_provenance,
    extract_provenance_summary,
)


def _make_evidence(source_type: str = "db", evidence_id: str = "ev_001") -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": source_type,
        "title": "test",
        "content": "content",
        "tenant_id": "t1",
        "metadata": {"source_target_id": "t1"},
    }


class TestBuildDBProvenance:
    """build_db_provenance 行为测试。"""

    def test_basic_db_provenance(self):
        """基本 DB 血缘构造。"""
        prov = build_db_provenance(
            db_id="test_db",
            table_name="test_table",
            query_id="q_001",
            row_keys=["row_1", "row_2"],
        )
        assert prov["db_id"] == "test_db"
        assert prov["table_name"] == "test_table"
        assert prov["query_id"] == "q_001"
        assert prov["row_keys"] == ["row_1", "row_2"]

    def test_empty_optional_fields_excluded(self):
        """空字段不写入。"""
        prov = build_db_provenance(db_id="db1", table_name="t1")
        assert "query_id" not in prov
        assert "row_keys" not in prov
        assert prov["db_id"] == "db1"

    def test_with_skill_and_step(self):
        """skill_id / step_id 通用字段写入。"""
        prov = build_db_provenance(
            db_id="db1",
            skill_id="skill_001",
            step_id="step_1",
        )
        assert prov["skill_id"] == "skill_001"
        assert prov["step_id"] == "step_1"


class TestBuildRAGProvenance:
    """build_rag_provenance 行为测试。"""

    def test_basic_rag_provenance(self):
        """基本 RAG 血缘构造。"""
        prov = build_rag_provenance(
            kb_id="kb1",
            doc_id="doc_001",
            chunk_id="chunk_001",
            doc_title="测试文档",
            doc_uri="rag://kb1/doc_001",
        )
        assert prov["kb_id"] == "kb1"
        assert prov["doc_id"] == "doc_001"
        assert prov["chunk_id"] == "chunk_001"
        assert prov["doc_title"] == "测试文档"
        assert prov["doc_uri"] == "rag://kb1/doc_001"

    def test_with_retrieval_query(self):
        """retrieval_query 写入。"""
        prov = build_rag_provenance(
            kb_id="kb1",
            retrieval_query="如何处理异常",
        )
        assert prov["retrieval_query"] == "如何处理异常"


class TestAttachProvenanceToEvidence:
    """attach_provenance_to_evidence 行为测试。"""

    def test_attach_to_evidence(self):
        """provenance 写入 metadata。"""
        evidence = _make_evidence()
        prov = build_db_provenance(db_id="db1", table_name="t1", query_id="q1")
        result = attach_provenance_to_evidence(evidence, prov)
        assert result["metadata"]["provenance"]["db_id"] == "db1"
        assert result["metadata"]["provenance"]["query_id"] == "q1"

    def test_preserves_existing_metadata(self):
        """保留原 metadata 中其它字段。"""
        evidence = _make_evidence()
        evidence["metadata"]["source_target_id"] = "t1"
        prov = build_db_provenance(db_id="db1")
        attach_provenance_to_evidence(evidence, prov)
        assert evidence["metadata"]["source_target_id"] == "t1"
        assert "provenance" in evidence["metadata"]

    def test_empty_evidence_returns_as_is(self):
        """空 evidence 直接返回。"""
        result = attach_provenance_to_evidence({}, {"db_id": "db1"})
        assert result == {}

    def test_no_metadata_field_creates_one(self):
        """evidence 无 metadata 时创建。"""
        evidence = {"evidence_id": "e1"}
        prov = build_db_provenance(db_id="db1")
        result = attach_provenance_to_evidence(evidence, prov)
        assert "metadata" in result
        assert "provenance" in result["metadata"]


class TestExtractProvenanceSummary:
    """extract_provenance_summary 行为测试。"""

    def test_db_summary(self):
        """DB 血缘摘要。"""
        evidence = _make_evidence(source_type="db")
        prov = build_db_provenance(
            db_id="db1",
            table_name="t1",
            query_id="q1",
            row_keys=["r1", "r2", "r3"],
        )
        attach_provenance_to_evidence(evidence, prov)
        summary = extract_provenance_summary(evidence)
        assert summary["db_id"] == "db1"
        assert summary["table_name"] == "t1"
        assert summary["query_id"] == "q1"
        assert summary["rows_used"] == 3

    def test_rag_summary(self):
        """RAG 血缘摘要。"""
        evidence = _make_evidence(source_type="rag")
        prov = build_rag_provenance(
            kb_id="kb1",
            doc_id="doc_1",
            chunk_id="chunk_1",
            doc_title="标题",
        )
        attach_provenance_to_evidence(evidence, prov)
        summary = extract_provenance_summary(evidence)
        assert summary["kb_id"] == "kb1"
        assert summary["doc_id"] == "doc_1"
        assert summary["chunk_id"] == "chunk_1"
        assert summary["doc_title"] == "标题"

    def test_no_provenance_summary(self):
        """无 provenance 时只返回 evidence_id / source_type。"""
        evidence = _make_evidence()
        summary = extract_provenance_summary(evidence)
        assert summary["evidence_id"] == "ev_001"
        assert summary["source_type"] == "db"
        assert "db_id" not in summary

    def test_empty_evidence(self):
        """空 evidence 返回空 summary。"""
        summary = extract_provenance_summary({})
        assert summary["evidence_id"] == ""
        assert summary["source_type"] == ""
