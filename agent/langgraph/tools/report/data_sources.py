"""DataSourceRef 收集与 source_summary 构造（按 docs §5）。

DataSourceRef 是 ReportArtifact.data_sources 数组中的元素，
用于在报告末尾展示"数据来源"区块。

DataSourceCollector 从 evidence 列表收集所有数据来源，
标注 used_by_sections / used_by_charts / used_by_tables / used_by_claims。

source_summary 是人类可读的来源摘要（label / source / query_id / used_for），
用于报告末尾的 Markdown/HTML 区块。
"""
from __future__ import annotations

import logging
from typing import Any

from agent.langgraph.tools.report.models import DataSourceRef, SourceSummaryEntry

logger = logging.getLogger(__name__)


def _make_source_id(source_type: str, ev_id: str) -> str:
    """生成 source_id。"""
    return f"src_{source_type}_{ev_id[:12]}"


class DataSourceCollector:
    """数据来源收集器（按 docs §5.1）。

    从 evidence 列表收集所有 DataSourceRef，
    标注 used_by_sections / used_by_charts / used_by_tables / used_by_claims。
    """

    def collect(
        self,
        evidence_list: list[dict],
        sections: list[dict],
        charts: list[dict],
        tables: list[dict],
        claims: list[dict],
    ) -> list[DataSourceRef]:
        """收集数据来源。

        Args:
            evidence_list: 标准化 Evidence 列表
            sections: 章节列表
            charts: 图表列表
            tables: 表格列表
            claims: Claim 列表

        Returns:
            list[DataSourceRef]: 去重后的数据来源列表
        """
        # 1. 建立 evidence_id → {section_ids, chart_ids, table_ids, claim_ids} 反向索引
        ev_to_sections: dict[str, set[str]] = {}
        ev_to_charts: dict[str, set[str]] = {}
        ev_to_tables: dict[str, set[str]] = {}
        ev_to_claims: dict[str, set[str]] = {}

        for sec in sections or []:
            for ref in sec.get("evidence_refs") or []:
                ev_to_sections.setdefault(ref, set()).add(sec.get("section_id", ""))
        for chart in charts or []:
            for ref in chart.get("evidence_refs") or []:
                ev_to_charts.setdefault(ref, set()).add(chart.get("chart_id", ""))
        for tbl in tables or []:
            for ref in tbl.get("evidence_refs") or []:
                ev_to_tables.setdefault(ref, set()).add(tbl.get("table_id", ""))
        for claim in claims or []:
            for ref in claim.get("evidence_refs") or []:
                ev_to_claims.setdefault(ref, set()).add(claim.get("claim_id", ""))

        # 2. 遍历 evidence 生成 DataSourceRef
        sources: list[DataSourceRef] = []
        for ev in evidence_list or []:
            ev_id = ev.get("evidence_id", "")
            if not ev_id:
                continue
            source_type = ev.get("source_type", "")
            metadata = ev.get("metadata") or {}
            provenance = metadata.get("provenance") or {}

            ref: DataSourceRef = {
                "source_id": _make_source_id(source_type, ev_id),
                "source_type": source_type if source_type in ("db", "rag", "web") else "db",
                "used_by_sections": sorted(ev_to_sections.get(ev_id, set())),
                "used_by_charts": sorted(ev_to_charts.get(ev_id, set())),
                "used_by_tables": sorted(ev_to_tables.get(ev_id, set())),
                "used_by_claims": sorted(ev_to_claims.get(ev_id, set())),
            }

            # DB 字段
            if source_type == "db":
                ref["db_id"] = provenance.get("db_id", "") or ev.get("source_uri", "").split("://")[-1].split("/")[0]
                ref["table_name"] = provenance.get("table_name", "")
                ref["query_id"] = provenance.get("query_id", "")
                ref["query_template_id"] = provenance.get("query_template_id", "")
                row_keys = provenance.get("row_keys") or []
                ref["row_count"] = len(row_keys)

            # RAG 字段
            if source_type == "rag":
                ref["kb_id"] = provenance.get("kb_id", "")
                ref["doc_id"] = provenance.get("doc_id", "")
                ref["chunk_id"] = provenance.get("chunk_id", "")
                ref["doc_title"] = provenance.get("doc_title", "")
                ref["doc_uri"] = provenance.get("doc_uri", "")

            sources.append(ref)

        logger.info(
            f"[DataSourceCollector] 收集到 {len(sources)} 个数据来源"
        )
        return sources


def build_source_summary(
    data_sources: list[dict],
    sections: list[dict],
) -> list[SourceSummaryEntry]:
    """构造人类可读的数据来源摘要（按 docs §5.2）。

    Args:
        data_sources: DataSourceRef 列表
        sections: 章节列表（用于反查 section title → used_for）

    Returns:
        list[SourceSummaryEntry]
    """
    # 章节 ID → title 反查
    sec_titles = {s.get("section_id", ""): s.get("title", "") for s in sections or []}

    summary: list[SourceSummaryEntry] = []
    for src in data_sources or []:
        source_type = src.get("source_type", "")
        if source_type == "db":
            db_id = src.get("db_id", "")
            table_name = src.get("table_name", "")
            label = table_name or "数据库查询"
            source = f"数据库 {db_id}.{table_name}" if table_name else f"数据库 {db_id}"
        elif source_type == "rag":
            doc_title = src.get("doc_title", "")
            kb_id = src.get("kb_id", "")
            label = doc_title or "知识库文档"
            source = f"知识库 {kb_id} / 文档《{doc_title}》" if doc_title else f"知识库 {kb_id}"
        else:
            label = f"{source_type} 来源"
            source = source_type

        # used_for：从 used_by_sections 反查章节标题
        used_for: list[str] = []
        for sec_id in src.get("used_by_sections", []) or []:
            title = sec_titles.get(sec_id, sec_id)
            if title and title not in used_for:
                used_for.append(title)

        entry: SourceSummaryEntry = {
            "label": label,
            "source": source,
            "query_id": src.get("query_id", ""),
            "used_for": used_for,
        }
        # RAG 来源附加 doc_id 便于追溯
        if source_type == "rag" and src.get("doc_id"):
            entry["doc_id"] = src.get("doc_id", "")
        if source_type == "rag" and src.get("chunk_id"):
            entry["chunk_id"] = src.get("chunk_id", "")

        summary.append(entry)

    return summary
