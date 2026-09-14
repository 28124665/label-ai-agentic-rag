#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Evidence 标准化模块。

将所有工具结果（RAG/DB/Web/Report）统一转换为 Evidence 数据结构，
便于多源融合、答案充分性判断和最终 Prompt 组装。

设计原则（docs/受限ReAct子图落地设计.md §7）：
- 所有工具结果必须转换为统一 Evidence
- Evidence 不再是字符串，而是结构化对象（含 source_uri、confidence、metadata）
- 工具 Observation 必须截断和脱敏后转为 Evidence
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Literal, Optional, TypedDict

from agent.langgraph.evidence.provenance import (
    EvidenceProvenance,
    attach_provenance_to_evidence,
    build_db_provenance,
    build_rag_provenance,
)


class Evidence(TypedDict, total=False):
    """标准化证据数据结构。

    所有工具结果（RAG/DB/Web/Report）统一为此结构。

    Attributes:
        evidence_id: 唯一 ID（hash(source_uri + content)）
        source_type: rag / db / web / report
        title: 标题
        content: 文本内容（已截断/脱敏）
        structured_data: 结构化数据（DB rows 等）
        source_uri: 来源 URI
        tenant_id: 租户 ID
        confidence: 置信度 0.0 ~ 1.0
        relevance_score: 相关性 0.0 ~ 1.0
        authority_score: 权威性 0.0 ~ 1.0
        freshness_score: 时效性 0.0 ~ 1.0
        created_at: 创建时间（ISO 字符串）
        metadata: 额外元数据
    """

    evidence_id: str
    source_type: Literal["rag", "db", "web", "rest", "report", "graph"]
    title: str
    content: str
    structured_data: dict
    source_uri: str
    tenant_id: str
    confidence: float
    relevance_score: float
    authority_score: float
    freshness_score: float
    created_at: str
    metadata: dict


# ========== 工具到 source_type 映射 ==========
TOOL_SOURCE_TYPE_MAP = {
    "rag_search": "rag",
    "rag": "rag",
    "db_query": "db",
    "database": "db",
    "web_search": "web",
    "web": "web",
    "rest": "rest",
    "rest_search": "rest",
    "graph": "graph",
    "graph_tool": "graph",
    "report": "report",
}

# 各 source_type 默认权威性（与 docs §7 思想一致）
DEFAULT_AUTHORITY_SCORE = {
    "db": 0.95,  # 数据库事实最权威
    "rest": 0.90,  # ERP 系统数据权威性高（介于 DB 和 RAG 之间）
    "graph": 0.90,  # 图谱结构化结果（经 LLM 合成 answer，二次加工，略低于 db）
    "rag": 0.80,  # 知识库次之
    "report": 0.85,  # 报告权威性视来源
    "web": 0.50,  # Web 可信度最低
}


def _make_evidence_id(source_uri: str, content: str) -> str:
    """生成 Evidence 唯一 ID（v2.1 修复：使用 SHA-256 替代 MD5，P1-E 稳定 ID）。

    SHA-256 确保跨进程/跨重启稳定，同内容必同 ID。
    """
    raw = f"{source_uri}|{content[:200]}"
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _make_sql_fingerprint(sql: str) -> str:
    """生成 SQL 指纹（规范化后哈希，P1-E 稳定 ID）。

    将 SQL 转大写、去除空白、去除具体值后哈希，
    确保同一查询模板生成相同指纹。

    Args:
        sql: 原始 SQL

    Returns:
        str: 16 位哈希指纹
    """
    import re

    if not sql:
        return "unknown"
    # 规范化：转大写 + 压缩空白 + 去除字符串字面值
    normalized = re.sub(r"\s+", " ", sql.upper().strip())
    normalized = re.sub(r"'[^']*'", "?", normalized)  # 字符串字面值 → ?
    normalized = re.sub(r"\b\d+\b", "?", normalized)    # 数字字面值 → ?
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _truncate_content(content: str, max_chars: int = 2000) -> tuple[str, bool]:
    """截断过长的内容，返回 (截断后内容, 是否被截断)。"""
    if not content:
        return "", False
    if len(content) <= max_chars:
        return content, False
    return content[:max_chars] + "…", True


def _scrub_sensitive(content: str) -> str:
    """轻量脱敏：去除常见敏感字段模式。

    仅做基础防御（手机号/身份证/银行卡号脱敏），
    完整脱敏由专门的 SensitiveDataFilter 负责。
    """
    import re

    if not content:
        return content
    # 11 位手机号（粗略匹配）
    content = re.sub(r"\b1[3-9]\d{9}\b", "1XX-XXXX-XXXX", content)
    # 18 位身份证
    content = re.sub(r"\b\d{17}[\dXx]\b", "ID-XXXXXXXX", content)
    return content


def _now_iso() -> str:
    """当前时间 ISO 字符串。"""
    from datetime import datetime

    return datetime.utcnow().isoformat() + "Z"


# ========== Normalizer 工厂 ==========
def normalize_rag_evidence(
    rag_docs: list[dict],
    tenant_id: str = "",
    query: str = "",
) -> list[Evidence]:
    """将 RAG 检索结果标准化为 Evidence 列表。

    Args:
        rag_docs: RAG 工具返回的 doc 列表，每项含 content/score/chunk_id 等
        tenant_id: 租户 ID
        query: 检索 query（用于计算 relevance_score）

    Returns:
        list[Evidence]: 标准化后的 Evidence 列表
    """
    evidences: list[Evidence] = []
    for doc in rag_docs or []:
        content_raw = doc.get("content") or doc.get("text") or ""
        content, truncated = _truncate_content(content_raw, max_chars=2000)
        content = _scrub_sensitive(content)

        score = float(doc.get("score", 0.0) or 0.0)
        kb_id = doc.get("kb_id", "")
        doc_id = doc.get("doc_id") or doc.get("document_id", "")
        chunk_id = doc.get("chunk_id", "")

        source_uri = f"kb://{kb_id}/{doc_id}/{chunk_id}" if kb_id else f"rag://{chunk_id or 'unknown'}"

        ev: Evidence = {
            "evidence_id": _make_evidence_id(source_uri, content_raw),
            "source_type": "rag",
            "title": doc.get("title", "") or f"知识库文档片段 {chunk_id}",
            "content": content,
            "structured_data": {},
            "source_uri": source_uri,
            "tenant_id": tenant_id,
            "confidence": max(0.0, min(1.0, score)),
            "relevance_score": max(0.0, min(1.0, score)),
            "authority_score": DEFAULT_AUTHORITY_SCORE["rag"],
            "freshness_score": 1.0,  # 知识库内容视为稳定
            "created_at": _now_iso(),
            "metadata": {
                "kb_id": kb_id,
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "page": doc.get("page", ""),
                "truncated": truncated,
                "query": query,
            },
        }
        # 写入 RAG 血缘（按 docs §4.1）
        rag_prov: EvidenceProvenance = build_rag_provenance(
            kb_id=kb_id,
            doc_id=doc_id,
            chunk_id=chunk_id,
            doc_title=doc.get("title", ""),
            doc_uri=doc.get("doc_uri", "") or doc.get("uri", ""),
            retrieval_query=query,
            step_id=doc.get("step_id", ""),
        )
        attach_provenance_to_evidence(ev, rag_prov)
        evidences.append(ev)
    return evidences


def normalize_db_evidence(
    db_result: dict,
    tenant_id: str = "",
    query: str = "",
) -> list[Evidence]:
    """将 DB 查询结果标准化为 Evidence 列表（v2.1 修复：行级粒度 + 零行 + 聚合 SQL）。

    v2.1 修订（P1-E + 验收项 21/22）：
    - 行级粒度：每行数据生成独立 Evidence（而非整 SQL 一条），支持 claim 级引用
    - 零行结果：DB 返回 0 行时生成 zero_rows Evidence，避免误判为"无证据"（验收项 21）
    - 聚合 SQL：COUNT/SUM/AVG 等聚合结果作为整体 Evidence，不按行拆分（验收项 22）
    - 稳定 source_uri：query_id + sql_fingerprint + row_key，跨进程稳定（P1-E）

    Args:
        db_result: DB 工具返回的 {sql, rows, row_count, source, ...}
        tenant_id: 租户 ID
        query: 原始查询（自然语言）

    Returns:
        list[Evidence]: 行级 Evidence 列表（零行时返回 1 条 zero_rows Evidence）
    """
    if not db_result:
        return []

    import json

    sql = db_result.get("sql", "") or ""
    rows = db_result.get("rows", []) or []
    row_count = db_result.get("row_count", len(rows))
    source = db_result.get("source", "")
    tables = db_result.get("tables", []) or []
    execution_time_ms = db_result.get("execution_time_ms", 0)
    formatted = db_result.get("formatted_result", "")
    query_id = db_result.get("query_id", "")
    sql_fingerprint = db_result.get("sql_fingerprint", "") or _make_sql_fingerprint(sql)

    # 表路径（用于 source_uri）
    table_path = "/".join(tables) if tables else "unknown"
    # as_of 时间戳（用于数据版本标识）
    as_of = db_result.get("as_of", _now_iso())

    # ★ v2.1 验收项 21：零行 DB 结果处理
    # DB 查询返回 0 行时，不生成 DB Evidence，但记录 query 级 provenance
    # 避免误判为"无证据"——可能是"数据库中确实没有匹配记录"
    if not rows:
        source_uri = f"db://{table_path}/{query_id}/{sql_fingerprint}?as_of={as_of}&zero_rows=true"
        ev: Evidence = {
            "evidence_id": _make_evidence_id(source_uri, "ZERO_ROWS"),
            "source_type": "db",
            "title": f"数据库查询结果（0 行）",
            "content": "",
            "structured_data": {"row_count": 0},
            "source_uri": source_uri,
            "tenant_id": tenant_id,
            "confidence": 1.0,  # 查询成功执行，confidence 高
            "relevance_score": 0.3,  # 但相关性低（无匹配数据）
            "authority_score": DEFAULT_AUTHORITY_SCORE["db"],
            "freshness_score": 1.0,
            "created_at": _now_iso(),
            "metadata": {
                "sql": sql,
                "tables": tables,
                "row_count": 0,
                "query_time_ms": execution_time_ms,
                "source": source,
                "natural_query": query,
                "query_id": query_id,
                "sql_fingerprint": sql_fingerprint,
                "as_of": as_of,
                "zero_rows": True,  # ★ 标记零行结果
            },
        }
        # 写入 DB 血缘
        db_prov: EvidenceProvenance = build_db_provenance(
            db_id=source or (tables[0].split(".")[0] if tables else ""),
            table_name=tables[0] if tables else "",
            query_id=query_id,
            sql_fingerprint=sql_fingerprint,
            row_keys=[],
            step_id=db_result.get("step_id", ""),
        )
        attach_provenance_to_evidence(ev, db_prov)
        return [ev]

    # ★ v2.1 验收项 22：聚合 SQL 结果处理
    # 聚合 SQL（COUNT/SUM/AVG/MAX/MIN）的结果作为整体 Evidence
    sql_upper = sql.upper().strip()
    is_aggregate = any(
        kw in sql_upper for kw in ["COUNT(", "SUM(", "AVG(", "MAX(", "MIN(", "GROUP BY"]
    )

    quality = float(db_result.get("quality_score", 0.8) or 0.8)

    # 聚合 SQL 或行数少（<=5）：作为整体 Evidence，不按行拆分
    if is_aggregate and len(rows) <= 5:
        evidences: list[Evidence] = []
        for row in rows:
            # 聚合结果的行键
            row_key = "aggregate_" + "_".join(f"{k}={v}" for k, v in row.items())
            source_uri = f"db://{table_path}/{query_id}/{sql_fingerprint}/{row_key}?as_of={as_of}"
            content_raw = json.dumps(row, ensure_ascii=False, default=str)
            content, truncated = _truncate_content(content_raw, max_chars=2000)
            content = _scrub_sensitive(content)

            ev = {
                "evidence_id": _make_evidence_id(source_uri, content_raw),
                "source_type": "db",
                "title": f"数据库聚合查询结果（{len(rows)} 行）",
                "content": content,
                "structured_data": row,
                "source_uri": source_uri,
                "tenant_id": tenant_id,
                "confidence": max(0.0, min(1.0, quality)),
                "relevance_score": max(0.0, min(1.0, quality)),
                "authority_score": DEFAULT_AUTHORITY_SCORE["db"],
                "freshness_score": 1.0,
                "created_at": _now_iso(),
                "metadata": {
                    "sql": sql,
                    "tables": tables,
                    "row_count": row_count,
                    "query_time_ms": execution_time_ms,
                    "source": source,
                    "truncated": truncated,
                    "natural_query": query,
                    "query_id": query_id,
                    "sql_fingerprint": sql_fingerprint,
                    "as_of": as_of,
                    "is_aggregate": True,  # ★ 标记聚合结果
                    "row_key": row_key,
                },
            }
            # 写入 DB 血缘
            db_prov = build_db_provenance(
                db_id=source or (tables[0].split(".")[0] if tables else ""),
                table_name=tables[0] if tables else "",
                query_id=query_id,
                sql_fingerprint=sql_fingerprint,
                row_keys=[row_key],
                step_id=db_result.get("step_id", ""),
            )
            attach_provenance_to_evidence(ev, db_prov)
            evidences.append(ev)
        return evidences

    # ★ v2.1 P1-E：行级粒度 Evidence（每行一个 Evidence）
    # 支持claim级引用——每个事实声明可绑定到具体数据行
    evidences = []
    # 行键提取：优先用主键列，否则用第一列
    first_col = list(rows[0].keys())[0] if rows else ""

    for row in rows:
        # 行键：主键列值（如 exception_id=EX001）
        row_key = _extract_row_key(row, first_col)

        source_uri = f"db://{table_path}/{query_id}/{sql_fingerprint}/{row_key}?as_of={as_of}"
        content_raw = json.dumps(row, ensure_ascii=False, default=str)
        content, truncated = _truncate_content(content_raw, max_chars=2000)
        content = _scrub_sensitive(content)

        ev = {
            "evidence_id": _make_evidence_id(source_uri, content_raw),
            "source_type": "db",
            "title": f"数据库查询结果行（{first_col}={row.get(first_col, '')}）",
            "content": content,
            "structured_data": row,  # 单行数据
            "source_uri": source_uri,
            "tenant_id": tenant_id,
            "confidence": max(0.0, min(1.0, quality)),
            "relevance_score": max(0.0, min(1.0, quality)),
            "authority_score": DEFAULT_AUTHORITY_SCORE["db"],
            "freshness_score": 1.0,  # DB 实时数据
            "created_at": _now_iso(),
            "metadata": {
                "sql": sql,
                "tables": tables,
                "row_count": row_count,
                "query_time_ms": execution_time_ms,
                "source": source,
                "truncated": truncated,
                "natural_query": query,
                "query_id": query_id,
                "sql_fingerprint": sql_fingerprint,
                "as_of": as_of,
                "row_key": row_key,
            },
        }
        # 写入 DB 血缘
        db_prov = build_db_provenance(
            db_id=source or (tables[0].split(".")[0] if tables else ""),
            table_name=tables[0] if tables else "",
            query_id=query_id,
            sql_fingerprint=sql_fingerprint,
            row_keys=[row_key],
            step_id=db_result.get("step_id", ""),
        )
        attach_provenance_to_evidence(ev, db_prov)
        evidences.append(ev)

    return evidences


def _extract_row_key(row: dict, first_col: str = "") -> str:
    """提取行键（用于 source_uri 稳定标识，P1-E）。

    优先使用主键列，否则用第一列。

    Args:
        row: 单行数据
        first_col: 第一列名

    Returns:
        str: 行键字符串（如 "exception_id=EX001"）
    """
    if not row or not isinstance(row, dict):
        return "unknown"

    # 尝试常见主键列名
    for pk in ("id", "ID", "Id", "exception_id", "uid", "uuid", "key"):
        if pk in row and row[pk] != "":
            return f"{pk}={row[pk]}"

    # 回退到第一列
    if first_col and first_col in row:
        return f"{first_col}={row[first_col]}"

    # 全部列值拼接（兜底）
    return "_".join(f"{k}={v}" for k, v in list(row.items())[:3])


def normalize_web_evidence(
    web_docs: list[dict],
    tenant_id: str = "",
    query: str = "",
) -> list[Evidence]:
    """将 Web 搜索结果标准化为 Evidence 列表。

    Args:
        web_docs: Web 工具返回的 docs，每项含 content/url/title
        tenant_id: 租户 ID
        query: 搜索 query

    Returns:
        list[Evidence]: 标准化后的 Evidence 列表
    """
    evidences: list[Evidence] = []
    for doc in web_docs or []:
        content_raw = doc.get("content") or doc.get("snippet") or ""
        content, truncated = _truncate_content(content_raw, max_chars=2000)
        content = _scrub_sensitive(content)

        score = float(doc.get("score", 0.5) or 0.5)
        url = doc.get("url", "")
        title = doc.get("title", "")
        published_at = doc.get("published_at", "")
        fetched_at = doc.get("fetched_at", _now_iso())

        ev: Evidence = {
            "evidence_id": _make_evidence_id(url, content_raw),
            "source_type": "web",
            "title": title or url or "Web 搜索结果",
            "content": content,
            "structured_data": {},
            "source_uri": url or "web://unknown",
            "tenant_id": tenant_id,
            "confidence": max(0.0, min(1.0, score)),
            "relevance_score": max(0.0, min(1.0, score)),
            "authority_score": DEFAULT_AUTHORITY_SCORE["web"],
            "freshness_score": 0.7,  # Web 视为中等时效
            "created_at": fetched_at,
            "metadata": {
                "url": url,
                "title": title,
                "published_at": published_at,
                "fetched_at": fetched_at,
                "truncated": truncated,
                "query": query,
            },
        }
        evidences.append(ev)
    return evidences


def normalize_rest_evidence(
    rest_docs: list[dict],
    tenant_id: str = "",
    query: str = "",
    erp_domain: str = "",
) -> list[Evidence]:
    """将 RestTool/ERP 结果标准化为 Evidence 列表。

    设计原则（docs/RestTool与MCP服务对接设计方案.md §4）：
    - authority_score = 0.90（介于 DB 0.95 和 RAG 0.80 之间）
    - ERP 系统数据视为实时数据，freshness_score = 1.0
    - source_uri 格式：rest://{erp_domain}/{function}/{endpoint}

    Args:
        rest_docs: RestTool 返回的 docs 列表
        tenant_id: 租户 ID
        query: 查询语句
        erp_domain: ERP 领域（hr/supply_chain/finance）

    Returns:
        list[Evidence]: 标准化后的 Evidence 列表
    """
    evidences: list[Evidence] = []
    for doc in rest_docs or []:
        content_raw = doc.get("content") or doc.get("data") or ""
        if isinstance(content_raw, dict):
            import json
            content_raw = json.dumps(content_raw, ensure_ascii=False, default=str)
        content, truncated = _truncate_content(content_raw, max_chars=2000)
        content = _scrub_sensitive(content)

        quality = float(doc.get("quality_score", 0.85) or 0.85)
        function_name = doc.get("function", "")
        endpoint = doc.get("endpoint", "")
        method = doc.get("method", "GET")
        doc_erp_domain = doc.get("erp_domain", erp_domain)

        source_uri = f"rest://{doc_erp_domain}/{function_name}/{endpoint}" if doc_erp_domain else f"rest://{function_name or 'unknown'}"

        ev: Evidence = {
            "evidence_id": _make_evidence_id(source_uri, content_raw),
            "source_type": "rest",
            "title": doc.get("title", "") or f"ERP 查询结果（{function_name or '未知'}）",
            "content": content,
            "structured_data": doc.get("data") if isinstance(doc.get("data"), dict) else {},
            "source_uri": source_uri,
            "tenant_id": tenant_id,
            "confidence": max(0.0, min(1.0, quality)),
            "relevance_score": max(0.0, min(1.0, quality)),
            "authority_score": DEFAULT_AUTHORITY_SCORE["rest"],
            "freshness_score": 1.0,  # ERP 系统数据视为实时
            "created_at": _now_iso(),
            "metadata": {
                "function": function_name,
                "endpoint": endpoint,
                "method": method,
                "erp_domain": doc_erp_domain,
                "truncated": truncated,
                "query": query,
            },
        }
        evidences.append(ev)
    return evidences


def normalize_graph_evidence(
    graph_result: dict,
    tenant_id: str = "",
    query: str = "",
) -> list[Evidence]:
    """将 GraphTool/GraphQAPipeline 结果标准化为 Evidence 列表。

    设计原则（docs/GraphTool接入设计文档.md §3.3.1）：
    - authority_score = 0.90（结构化可追溯，略低于 db，因 answer 由 LLM 二次合成）
    - freshness_score = 1.0（图谱数据视为实时）
    - source_uri 格式：graph://neo4j/<Label>/<Key>（缺省 graph://neo4j/graph）
    - row_count == 0 或 error 非空时跳过（交由质量检查降级）

    Args:
        graph_result: GraphTool 返回的 GraphQAResult 结构
        tenant_id: 租户 ID
        query: 查询语句

    Returns:
        list[Evidence]: 标准化后的 Evidence 列表（空列表表示无有效证据）
    """
    if not graph_result:
        return []

    import json

    error = graph_result.get("error", "") or ""
    rows = graph_result.get("rows", []) or []
    row_count = graph_result.get("row_count", len(rows))
    answer = graph_result.get("answer", "") or ""
    quality = float(graph_result.get("quality_score", 0.85) or 0.85)

    # error 非空或空结果：不产出 Evidence，交由质量检查降级
    if error or row_count == 0:
        return []

    # content 构造：优先 answer 文本，否则逐行拼接
    if answer:
        content_raw = answer
    else:
        parts: list[str] = []
        for row in rows:
            if isinstance(row, dict):
                parts.append(json.dumps(row, ensure_ascii=False, default=str))
            else:
                parts.append(str(row))
        content_raw = "\n".join(parts)

    content, truncated = _truncate_content(content_raw, max_chars=2000)
    content = _scrub_sensitive(content)

    # source_uri 提取：从 rows 首行提取 Label/Key
    source_uri = _extract_graph_source_uri(rows)
    evidence_id = _make_evidence_id(source_uri, content_raw)

    ev: Evidence = {
        "evidence_id": evidence_id,
        "source_type": "graph",
        "title": graph_result.get("title", "") or f"图谱查询结果（{row_count} 行）",
        "content": content,
        "structured_data": {"rows": rows, "row_count": row_count},
        "source_uri": source_uri,
        "tenant_id": tenant_id,
        "confidence": max(0.0, min(1.0, quality)),
        "relevance_score": max(0.0, min(1.0, quality)),
        "authority_score": DEFAULT_AUTHORITY_SCORE["graph"],
        "freshness_score": 1.0,  # 图谱数据视为实时
        "created_at": _now_iso(),
        "metadata": {
            "row_count": row_count,
            "source_type": graph_result.get("source_type", ""),
            "truncated": truncated,
            "query": query,
        },
    }
    return [ev]


def _extract_graph_source_uri(rows: list) -> str:
    """从图谱结果 rows 首行提取 Label/Key，构造 source_uri。

    Args:
        rows: 图谱查询结果行列表

    Returns:
        str: graph://neo4j/<Label>/<Key>，缺省 graph://neo4j/graph
    """
    if not rows:
        return "graph://neo4j/graph"

    first = rows[0]
    if isinstance(first, dict):
        # 优先显式 Label/Key 字段
        label = first.get("label") or first.get("Label") or first.get("_label") or ""
        key = first.get("key") or first.get("Key") or first.get("_key") or ""
        if not key:
            # 尝试常见主键列
            for pk in ("id", "ID", "Id", "name", "Name", "uuid", "uid"):
                if pk in first and first[pk] not in ("", None):
                    key = f"{pk}={first[pk]}"
                    break
        if label or key:
            return f"graph://neo4j/{label or 'graph'}/{key or 'unknown'}"
        return "graph://neo4j/graph"
    return "graph://neo4j/graph"


def normalize_tool_result(
    tool_name: str,
    result: dict,
    tenant_id: str = "",
    query: str = "",
) -> list[Evidence]:
    """统一的工具结果 → Evidence 转换入口。

    Args:
        tool_name: 工具名（rag_search / db_query / web_search / report）
        result: 工具原始结果
        tenant_id: 租户 ID
        query: 查询语句

    Returns:
        list[Evidence]: 标准化后的 Evidence 列表（空列表表示无证据）
    """
    source_type = TOOL_SOURCE_TYPE_MAP.get(tool_name, "")

    if source_type == "rag":
        docs = result.get("docs") or result.get("rag_docs") or []
        return normalize_rag_evidence(docs, tenant_id=tenant_id, query=query)
    if source_type == "db":
        return normalize_db_evidence(result, tenant_id=tenant_id, query=query)
    if source_type == "web":
        docs = result.get("docs") or result.get("web_docs") or []
        return normalize_web_evidence(docs, tenant_id=tenant_id, query=query)
    if source_type == "report":
        # 报表 Evidence 暂复用 DB 标准化逻辑
        return normalize_db_evidence(result, tenant_id=tenant_id, query=query)
    if source_type == "rest":
        docs = result.get("docs") or result.get("rest_docs") or []
        erp_domain = result.get("erp_domain", "")
        return normalize_rest_evidence(docs, tenant_id=tenant_id, query=query, erp_domain=erp_domain)
    if source_type == "graph":
        return normalize_graph_evidence(result, tenant_id=tenant_id, query=query)

    return []
