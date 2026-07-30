"""EvidenceProvenance 统一血缘模型。

按 docs/报告可信治理与人机协同补强设计.md §4.1 设计：
- DB 血缘：db_id / table_name / query_id / sql_fingerprint / row_keys
- RAG 血缘：kb_id / doc_id / chunk_id / doc_title / doc_uri / retrieval_query
- 通用血缘：source_target_id / query_template_id / skill_id / step_id

所有工具生成的 Evidence 都应通过 attach_provenance_to_evidence() 写入
metadata.provenance，确保 Claim / 报告 / 数据来源摘要可追溯到 query/chunk。
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class EvidenceProvenance(TypedDict, total=False):
    """证据来源血缘。

    嵌入在 Evidence.metadata["provenance"] 字段中，
    是 Claim 级血缘链路的根节点（Claim → Evidence → provenance → DB/RAG 原始来源）。
    """

    # ===== DB 血缘 =====
    db_id: str
    table_name: str
    query_id: str
    sql_fingerprint: str
    row_keys: list[str]

    # ===== RAG 血缘 =====
    kb_id: str
    doc_id: str
    chunk_id: str
    doc_title: str
    doc_uri: str
    retrieval_query: str

    # ===== 通用血缘 =====
    source_target_id: str
    query_template_id: str
    skill_id: str
    step_id: str


def build_db_provenance(
    db_id: str = "",
    table_name: str = "",
    query_id: str = "",
    sql_fingerprint: str = "",
    row_keys: Optional[list[str]] = None,
    skill_id: str = "",
    step_id: str = "",
) -> EvidenceProvenance:
    """构造 DB 类型 Evidence 的 provenance。

    Args:
        db_id: 数据库 ID
        table_name: 表名
        query_id: 查询 ID（DBTool 生成）
        sql_fingerprint: SQL 指纹
        row_keys: 命中的行键列表（如 ["exception_id=EX001"]）
        skill_id: 关联 Skill ID
        step_id: 关联 PlanStep ID

    Returns:
        EvidenceProvenance: 构造好的血缘对象
    """
    prov: EvidenceProvenance = {}
    if db_id:
        prov["db_id"] = db_id
    if table_name:
        prov["table_name"] = table_name
    if query_id:
        prov["query_id"] = query_id
    if sql_fingerprint:
        prov["sql_fingerprint"] = sql_fingerprint
    if row_keys:
        prov["row_keys"] = row_keys
    if skill_id:
        prov["skill_id"] = skill_id
    if step_id:
        prov["step_id"] = step_id
    return prov


def build_rag_provenance(
    kb_id: str = "",
    doc_id: str = "",
    chunk_id: str = "",
    doc_title: str = "",
    doc_uri: str = "",
    retrieval_query: str = "",
    skill_id: str = "",
    step_id: str = "",
) -> EvidenceProvenance:
    """构造 RAG 类型 Evidence 的 provenance。

    Args:
        kb_id: 知识库 ID
        doc_id: 文档 ID
        chunk_id: Chunk ID（如 "SOP-QA-001#c3"）
        doc_title: 文档标题
        doc_uri: 文档 URI
        retrieval_query: 检索 query
        skill_id: 关联 Skill ID
        step_id: 关联 PlanStep ID

    Returns:
        EvidenceProvenance: 构造好的血缘对象
    """
    prov: EvidenceProvenance = {}
    if kb_id:
        prov["kb_id"] = kb_id
    if doc_id:
        prov["doc_id"] = doc_id
    if chunk_id:
        prov["chunk_id"] = chunk_id
    if doc_title:
        prov["doc_title"] = doc_title
    if doc_uri:
        prov["doc_uri"] = doc_uri
    if retrieval_query:
        prov["retrieval_query"] = retrieval_query
    if skill_id:
        prov["skill_id"] = skill_id
    if step_id:
        prov["step_id"] = step_id
    return prov


def attach_provenance_to_evidence(
    evidence: dict[str, Any],
    provenance: EvidenceProvenance,
) -> dict[str, Any]:
    """将 provenance 写入 Evidence.metadata。

    不修改原 evidence 的其它字段。

    Args:
        evidence: 标准化后的 Evidence dict
        provenance: 血缘对象

    Returns:
        dict: 同一 evidence 引用（原地修改 metadata）
    """
    if not evidence:
        return evidence
    metadata = evidence.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    # 仅在 provenance 非空时覆盖
    if provenance:
        metadata["provenance"] = dict(provenance)
    evidence["metadata"] = metadata
    return evidence


def extract_provenance_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    """从 Evidence 提取血缘摘要（用于 Claim.evidence_sources）。

    Args:
        evidence: 标准化后的 Evidence

    Returns:
        dict: 血缘摘要，至少含 evidence_id / source_type + 摘要字段
    """
    summary: dict[str, Any] = {
        "evidence_id": evidence.get("evidence_id", ""),
        "source_type": evidence.get("source_type", ""),
    }
    metadata = evidence.get("metadata") or {}
    provenance = metadata.get("provenance") or {}

    # DB 字段摘要
    if provenance.get("db_id"):
        summary["db_id"] = provenance["db_id"]
    if provenance.get("table_name"):
        summary["table_name"] = provenance["table_name"]
    if provenance.get("query_id"):
        summary["query_id"] = provenance["query_id"]
    if provenance.get("row_keys"):
        summary["rows_used"] = len(provenance["row_keys"])

    # RAG 字段摘要
    if provenance.get("kb_id"):
        summary["kb_id"] = provenance["kb_id"]
    if provenance.get("doc_id"):
        summary["doc_id"] = provenance["doc_id"]
    if provenance.get("chunk_id"):
        summary["chunk_id"] = provenance["chunk_id"]
    if provenance.get("doc_title"):
        summary["doc_title"] = provenance["doc_title"]

    return summary
