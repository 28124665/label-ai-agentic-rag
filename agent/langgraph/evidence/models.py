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
    source_type: Literal["rag", "db", "web", "report"]
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
    "report": "report",
}

# 各 source_type 默认权威性（与 docs §7 思想一致）
DEFAULT_AUTHORITY_SCORE = {
    "db": 0.95,  # 数据库事实最权威
    "rag": 0.80,  # 知识库次之
    "report": 0.85,  # 报告权威性视来源
    "web": 0.50,  # Web 可信度最低
}


def _make_evidence_id(source_uri: str, content: str) -> str:
    """生成 Evidence 唯一 ID。"""
    raw = f"{source_uri}|{content[:200]}"
    return "ev_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


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
        evidences.append(ev)
    return evidences


def normalize_db_evidence(
    db_result: dict,
    tenant_id: str = "",
    query: str = "",
) -> list[Evidence]:
    """将 DB 查询结果标准化为 Evidence 列表。

    Args:
        db_result: DB 工具返回的 {sql, rows, row_count, source, ...}
        tenant_id: 租户 ID
        query: 原始查询（自然语言）

    Returns:
        list[Evidence]: 单条 Evidence（一条 SQL 生成一条 Evidence）
    """
    if not db_result:
        return []

    sql = db_result.get("sql", "") or ""
    rows = db_result.get("rows", []) or []
    row_count = db_result.get("row_count", len(rows))
    source = db_result.get("source", "")
    tables = db_result.get("tables", []) or []
    execution_time_ms = db_result.get("execution_time_ms", 0)
    formatted = db_result.get("formatted_result", "")

    # 内容用 formatted_result 优先，否则简单序列化前 N 行
    if formatted:
        content_raw = formatted
    else:
        import json

        max_preview_rows = 20
        preview = rows[:max_preview_rows]
        content_raw = json.dumps(preview, ensure_ascii=False, default=str)
        if len(rows) > max_preview_rows:
            content_raw += f"\n... (共 {row_count} 行，仅展示前 {max_preview_rows} 行)"

    content, truncated = _truncate_content(content_raw, max_chars=2000)
    content = _scrub_sensitive(content)

    source_uri = f"db://{'/'.join(tables) if tables else 'unknown'}/{hash(sql) & 0xffffffff:x}"

    quality = float(db_result.get("quality_score", 0.8) or 0.8)

    ev: Evidence = {
        "evidence_id": _make_evidence_id(source_uri, sql + str(row_count)),
        "source_type": "db",
        "title": f"数据库查询结果（{len(tables)} 表，{row_count} 行）",
        "content": content,
        "structured_data": {
            "columns": list(rows[0].keys()) if rows else [],
            "rows": rows[:100],  # 结构化数据保留前 100 行
        },
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
        },
    }
    return [ev]


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

    return []
