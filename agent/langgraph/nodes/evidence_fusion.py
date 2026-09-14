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
"""Evidence Fusion 节点（v2.2 修订：Token 预算压缩 + 重试证据重建 + 来源配额 + 不可变快照）。

v2.2 修订要点：
1. 预算计算提前到标准化之前，支持 moderate/severe/compact 级别的压缩
2. RAG LLM 抽取式压缩：在标准化前对 chunk 内容进行 LLM 压缩
3. DB 行级截断 + 列级裁剪 + 表级配额：在标准化前对 DB 结果进行压缩
4. 压缩后的数据进入标准化和融合管道，下游代码无感知

v2.1 修订要点（P0-2 修复 + 任务二建议 6）：
1. 每次调用都从工具结果重新标准化（不再只在 evidence 为空时补充）
2. 引入 tool_run_id/attempt_id，重试时基于 (source_type, tool_run_id) 精确替换旧证据
3. 来源配额：避免 DB 行级 Evidence 或单一来源挤掉其他反证（任务二建议 6）
4. 生成不可变 EvidenceSnapshot，Prompt/verifier/references 使用同一 snapshot_id
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agent.langgraph.evidence.fusion import fuse_evidences
from agent.langgraph.evidence.models import normalize_tool_result
from agent.langgraph.evidence.snapshot import EvidenceSnapshot
from agent.langgraph.evidence.token_budget import (
    TokenBudgetScheduler,
    _detect_active_tools,
)
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)

# Evidence Fusion 默认参数
DEFAULT_MAX_EVIDENCE_COUNT = 20
DEFAULT_TOKEN_BUDGET = 4000

# v2.1 任务二建议 6：来源配额（避免单一来源挤掉其他反证）
DEFAULT_SOURCE_QUOTA = {
    "rag": 10,   # RAG 最多 10 条
    "db": 10,    # DB 最多 10 条（行级粒度下需要限制）
    "web": 5,    # Web 最多 5 条
    "report": 5, # Report 最多 5 条
    "graph": 8,  # Graph 最多 8 条（图问答结果，设计文档 §3.2.2）
}


async def evidence_fusion_node(state: AgentState) -> dict[str, Any]:
    """Evidence 融合节点（v2.2 修订：Token 预算压缩 + 重试证据重建 + 不可变快照）。

    流程：
    1. 检测激活工具 + 计算 Token 预算（提前到标准化之前）
    2. 根据压缩级别压缩原始工具结果（RAG LLM 压缩 + DB 行级截断）
    3. 从压缩后的工具结果重新标准化 Evidence（每次都重建，P0-2 修复）
    4. 与旧 evidence 合并：基于 (source_type, tool_run_id) 精确替换
    5. 来源配额：按 source_type 限制数量，避免单一来源挤掉反证
    6. 调用 fuse_evidences() 融合去重排序
    7. 构建不可变 EvidenceSnapshot

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段（含 evidence/evidence_snapshot_id/evidence_snapshot/
               tool_run_id/attempt_id/active_tools/tool_token_counts/
               overflown_tools/compression_level）
    """
    start_time = time.time()
    node_name = "evidence_fusion"

    tenant_id = state.get("tenant_id", "")
    user_question = state.get("user_question", "")

    # P1-2: 记录 evidence 快照重建次数
    try:
        from api.utils import metrics
        metrics.rag_evidence_snapshot_rebuild_total.inc()
    except Exception:
        pass

    # ═══════════════════════════════════════════════════════════════════
    # ★ v2.2 Step 1: 检测激活工具 + 统计 Token + 溢出判定
    # ═══════════════════════════════════════════════════════════════════
    active_tools = _detect_active_tools(state)
    total_budget = state.get("total_token_budget", 110000)
    scheduler = TokenBudgetScheduler(total_budget=total_budget)

    locked_tokens = state.get("locked_system_tokens", 0) + state.get(
        "locked_history_tokens", 0
    )

    # ★ v2.0: 按实际溢出比例判定压缩级别（替代固定权重分配）
    tool_token_counts = scheduler._count_tool_tokens(state)
    overflow_ratio = scheduler._calculate_overflow_ratio(
        tool_token_counts, locked_tokens
    )
    level = scheduler.determine_level(overflow_ratio)
    overflown_tools, exempt_tools = scheduler._identify_overflown_tools(
        tool_token_counts
    )

    # ═══════════════════════════════════════════════════════════════════
    # ★ v2.2 Step 2: 根据压缩级别压缩原始工具结果
    # ═══════════════════════════════════════════════════════════════════
    # 创建可变的 state 副本，用于存放压缩后的数据
    working_state = dict(state) if not isinstance(state, dict) else {**state}

    if level in ("moderate", "severe", "compact"):

        # ── 2a. RAG LLM 抽取式压缩（仅超标工具） ──
        if "rag" in overflown_tools:
            rag_docs = list(state.get("rag_docs", []) or [])
            if rag_docs:
                compression_llm = await _get_compression_llm(state)
                if compression_llm is not None:
                    logger.info(
                        f"[{node_name}] RAG LLM 压缩开始: level={level}, "
                        f"chunks={len(rag_docs)}"
                    )
                    try:
                        compressed_rag = await scheduler.compress_rag_chunks(
                            chunks=rag_docs,
                            question=user_question,
                            level=level,
                            llm=compression_llm,
                        )
                        # 过滤掉压缩后 content 为空的 chunk（标记为"无相关内容"）
                        working_state["rag_docs"] = [
                            c for c in compressed_rag if c.get("content")
                        ]
                        dropped = len(compressed_rag) - len(working_state["rag_docs"])
                        logger.info(
                            f"[{node_name}] RAG LLM 压缩完成: "
                            f"original={len(rag_docs)}, "
                            f"kept={len(working_state['rag_docs'])}, "
                            f"dropped={dropped}"
                        )
                    except Exception:
                        logger.warning(
                            f"[{node_name}] RAG LLM 压缩异常，保留原文",
                            exc_info=True,
                        )
                else:
                    logger.info(
                        f"[{node_name}] RAG LLM 压缩跳过: 无可用压缩模型"
                    )

        # ── 2b. DB 行级截断 + 列级裁剪 + 表级配额（仅超标工具） ──
        if "db" in overflown_tools:
            db_result = state.get("db_result") or {}
            if db_result.get("rows"):
                db_quota = tool_token_counts.get("db", 0)
                logger.info(
                    f"[{node_name}] DB 压缩开始: level={level}, "
                    f"rows={len(db_result.get('rows', []))}, "
                    f"quota={db_quota}"
                )
                compressed_db = _compress_db_for_fusion(
                    db_result=db_result,
                    state=state,
                    scheduler=scheduler,
                    level=level,
                    db_quota=db_quota,
                )
                working_state["db_result"] = compressed_db
                logger.info(
                    f"[{node_name}] DB 压缩完成: "
                    f"original_rows={compressed_db.get('_original_row_count', 0)}, "
                    f"kept_rows={len(compressed_db.get('rows', []))}"
                )

    # ═══════════════════════════════════════════════════════════════════
    # Step 3: 标准化（使用压缩后的数据）
    # ═══════════════════════════════════════════════════════════════════
    new_evidences = _normalize_current_tool_results(
        working_state, tenant_id, user_question
    )

    # 获取旧 evidence（上一轮融合结果）
    old_evidences = list(state.get("evidence", []) or [])

    # ★ v2.1 P0-2 修复：基于 (source_type, tool_run_id) 精确替换
    # 本轮重试的工具产出新证据 → 该工具上一轮的旧证据整体失效
    # 未参与本轮重试的工具 → 旧证据完整保留
    if new_evidences and old_evidences:
        merged_evidences = _merge_with_replacement(old_evidences, new_evidences)
    elif new_evidences:
        merged_evidences = new_evidences
    else:
        merged_evidences = old_evidences

    # ★ v2.1 任务二建议 6：来源配额
    # 避免 DB 行级 Evidence 或单一来源挤掉其他反证
    quota = _get_source_quota(state)
    quota_applied = _apply_source_quota(merged_evidences, quota)

    # 融合时传入实际 Token 预算（而非固定 DEFAULT_TOKEN_BUDGET）
    fusion_result = fuse_evidences(
        evidences=quota_applied,
        max_count=DEFAULT_MAX_EVIDENCE_COUNT,
        token_budget=sum(tool_token_counts.values()),
    )

    fused_evidences = fusion_result.get("fused", [])
    conflicts = fusion_result.get("conflicts", [])

    # ★ v2.1 构建不可变 EvidenceSnapshot
    attempt_id = state.get("attempt_id", 1)
    tool_run_id = state.get("tool_run_id", "")
    snapshot = EvidenceSnapshot.build(
        evidences=fused_evidences,
        tool_run_id=tool_run_id,
        attempt_id=attempt_id,
    )

    logger.info(
        f"[{node_name}] Evidence 融合完成: "
        f"new={len(new_evidences)}, old={len(old_evidences)}, "
        f"merged={len(merged_evidences)}, quota_applied={len(quota_applied)}, "
        f"fused={len(fused_evidences)}, conflicts={len(conflicts)}, "
        f"snapshot_id={snapshot.snapshot_id}, attempt_id={attempt_id}, "
        f"active_tools={active_tools}, level={level}, "
        f"overflow_ratio={overflow_ratio:.2f}, "
        f"tool_token_counts={tool_token_counts}, "
        f"overflown_tools={overflown_tools}, "
        f"exempt_tools={exempt_tools}"
    )

    return {
        "evidence": fused_evidences,
        "evidence_snapshot_id": snapshot.snapshot_id,
        "evidence_snapshot": snapshot.to_dict(),
        "active_tools": active_tools,
        "tool_token_counts": tool_token_counts,
        "overflown_tools": overflown_tools,
        "compression_level": level,
        "node_timings": {node_name: int((time.time() - start_time) * 1000)},
    }


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════


async def _get_compression_llm(state: dict) -> Any:
    """获取压缩用的低成本 LLM 实例。

    优先使用压缩专用模型配置，回退到默认 chat 模型。
    返回的 LLM 需实现 async chat(prompt) -> str 接口。

    Args:
        state: AgentState dict

    Returns:
        LLM 实例（有 chat 方法），或 None（不可用时）
    """
    try:
        from agent.langgraph.gateways.llm_bundle_adapter import LLMBundleAdapter

        tenant_id = state.get("tenant_id", "")
        # 尝试获取压缩专用模型，回退到默认 chat 模型
        llm_id = state.get("compression_llm_id", "") or state.get("chat_llm_id", "")

        bundle = LLMBundleAdapter._resolve_chat_model(tenant_id, llm_id)
        if bundle is None:
            logger.warning("[evidence_fusion] 无法获取压缩 LLM")
            return None

        # 封装为 compress_rag_chunks 期望的接口
        return _CompressionLLMWrapper(bundle)
    except Exception:
        logger.warning("[evidence_fusion] 获取压缩 LLM 异常", exc_info=True)
        return None


class _CompressionLLMWrapper:
    """LLMBundle → compress_rag_chunks 期望的 async chat(prompt) -> str 接口适配器。"""

    def __init__(self, bundle):
        self._bundle = bundle

    async def chat(self, prompt: str) -> str:
        result = await self._bundle.async_chat(prompt, [], {})
        if isinstance(result, tuple):
            # ChatModel 返回 (content, token_count) 元组
            return result[0] if result[0] else ""
        # LLMBundle 返回裸 str
        return result if result else ""


def _compress_db_for_fusion(
    db_result: dict,
    state: dict,
    scheduler: TokenBudgetScheduler,
    level: str,
    db_quota: int,
) -> dict:
    """对 DB 查询结果执行行级截断 + 列级裁剪 + 表级配额。

    支持多次 DB 查询：从 tool_results 中提取每个查询的独立结果，
    按表级配额分配压缩，最后合并压缩后的 rows。

    Args:
        db_result: 聚合后的 DB 结果（平面 rows）
        state: AgentState dict（用于读取 tool_results）
        scheduler: TokenBudgetScheduler 实例
        level: 压缩级别
        db_quota: DB 工具总 Token 配额

    Returns:
        压缩后的 db_result dict（rows 被替换为压缩后的行）
    """
    # 提取多次 DB 查询的独立结果
    table_results = _split_db_to_tables(state, db_result)
    if not table_results:
        return db_result

    original_row_count = len(db_result.get("rows", []) or [])

    if len(table_results) == 1:
        # 单表/单查询：直接压缩
        compressed = scheduler._compress_db_result(
            db_result=table_results[0],
            quota=db_quota,
            level=level,
        )
        return {
            **db_result,
            "rows": compressed["rows"],
            "columns": compressed.get("columns", db_result.get("columns", [])),
            "_compressed": compressed["truncated"],
            "_original_row_count": original_row_count,
        }

    # 多表/多查询：分配表级配额后分别压缩
    table_quotas = scheduler.allocate_db_quota(table_results, db_quota)

    compressed_rows = []
    compressed_columns = db_result.get("columns", [])
    all_truncated = False

    for table_result, quota in zip(table_results, table_quotas):
        compressed = scheduler._compress_db_result(
            db_result=table_result,
            quota=quota,
            level=level,
        )
        compressed_rows.extend(compressed["rows"])
        if compressed.get("columns"):
            compressed_columns = compressed["columns"]
        if compressed["truncated"]:
            all_truncated = True

    return {
        **db_result,
        "rows": compressed_rows,
        "columns": compressed_columns,
        "_compressed": all_truncated,
        "_original_row_count": original_row_count,
    }


def _split_db_to_tables(state: dict, db_result: dict) -> list[dict]:
    """从 tool_results 中提取多次 DB 查询的独立结果。

    优先使用 tool_results（每个 ToolResult 对应一次 SQL 执行），
    回退到平面的 db_result。

    Args:
        state: AgentState dict
        db_result: 聚合后的 DB 结果

    Returns:
        每次 DB 查询的独立结果列表
    """
    tool_results = state.get("tool_results", []) or []
    db_results = []

    for tr in tool_results:
        tool = tr.get("tool", "")
        if tool in ("database", "db", "db_query"):
            tr_db = tr.get("result") or tr.get("db_result") or {}
            if tr_db.get("rows"):
                db_results.append(tr_db)

    if db_results:
        return db_results

    # 回退到平面 db_result
    if db_result.get("rows"):
        return [db_result]

    return []


# ═══════════════════════════════════════════════════════════════════════
# 以下为 v2.1 原有函数（未修改）
# ═══════════════════════════════════════════════════════════════════════


def _normalize_current_tool_results(
    state: AgentState,
    tenant_id: str,
    query: str,
) -> list:
    """从当前轮次工具结果重新标准化 Evidence（v2.1 P0-2 修复）。

    每次调用都检查 rag_docs/db_result/web_docs，重新生成 Evidence。
    不依赖旧 evidence 是否为空。

    Args:
        state: 当前 AgentState（可能已包含压缩后的数据）
        tenant_id: 租户 ID
        query: 用户问题

    Returns:
        list[Evidence]: 本轮工具结果标准化的 Evidence 列表
    """
    evidences = []

    # RAG
    rag_docs = state.get("rag_docs", []) or []
    if rag_docs:
        evidences.extend(
            normalize_tool_result(
                tool_name="rag_search",
                result={"docs": rag_docs},
                tenant_id=tenant_id,
                query=query,
            )
        )

    # DB
    db_result = state.get("db_result") or {}
    if db_result:
        evidences.extend(
            normalize_tool_result(
                tool_name="db_query",
                result=db_result,
                tenant_id=tenant_id,
                query=query,
            )
        )

    # Web
    web_docs = state.get("web_docs", []) or []
    if web_docs:
        evidences.extend(
            normalize_tool_result(
                tool_name="web_search",
                result={"docs": web_docs},
                tenant_id=tenant_id,
                query=query,
            )
        )

    # Graph（图谱问答，设计文档 §3.2.2）
    graph_result = state.get("graph_result") or {}
    if graph_result:
        evidences.extend(
            normalize_tool_result(
                tool_name="graph",
                result=graph_result,
                tenant_id=tenant_id,
                query=query,
            )
        )

    return evidences


def _merge_with_replacement(old: list, new: list) -> list:
    """合并新旧证据：本轮重试工具的旧证据被替换，其他工具的旧证据保留（v2.1 P0-2 修复）。

    v2.1 修正（P0-2 真实问题修复）：
    - v2.0 原实现用 ``uri_prefix = "/".join(source_uri.split("/")[:3])`` 做替换键，
      对 RAG（kb://kb1/doc1/chunk3 → kb://kb1）和 Web（web://example.com/page → web://example.com）
      过于激进，会导致同 KB / 同域名的所有旧证据被错误替换，丢失其他查询的反证。
    - v2.1 改为基于 ``(source_type, tool_run_id)`` 精确替换：
      本轮重试的工具产出新证据 → 该工具上一轮的旧证据整体失效；
      未参与本轮重试的工具 → 旧证据完整保留。

    替换规则（明确旧证据失效边界）：
    - 同 source_type + 同 tool_run_id 前缀 → 旧证据被替换（本轮重试同一工具）
    - 不同 source_type → 累积（RAG + DB + Web 可共存）
    - 同 source_type 不同 tool_run_id → 累积（多次 RAG 检索不同 query 的结果共存）

    类比 Java 中的 Map 合并：
        ``newMap.putAll(oldMap filtered by not in newMap's key set)``

    Args:
        old: 旧 Evidence 列表
        new: 新 Evidence 列表

    Returns:
        合并后的 Evidence 列表
    """
    if not new:
        return list(old)
    if not old:
        return list(new)

    # 提取本轮新证据的 (source_type, tool_run_id) 集合
    new_keys = set()
    for ev in new:
        st = ev.get("source_type", "")
        rid = _extract_tool_run_id(ev)
        if st:
            new_keys.add((st, rid))

    # 保留未被本轮替换的旧证据
    kept_old = []
    for ev in old:
        st = ev.get("source_type", "")
        rid = _extract_tool_run_id(ev)
        if (st, rid) not in new_keys:
            kept_old.append(ev)

    return kept_old + list(new)


def _extract_tool_run_id(ev: dict) -> str:
    """从 Evidence 中提取 tool_run_id（用于替换判断）。

    Args:
        ev: Evidence dict

    Returns:
        tool_run_id 字符串，提取不到则返回空字符串
    """
    metadata = ev.get("metadata") or {}
    rid = metadata.get("tool_run_id") or metadata.get("run_id") or ""
    return rid


def _get_source_quota(state: dict) -> dict[str, int]:
    """获取来源配额配置。

    优先使用 state 中的覆盖配置，回退到默认值。

    Args:
        state: AgentState dict

    Returns:
        {"rag": 10, "db": 10, "web": 5, "report": 5}
    """
    return state.get("source_quota") or DEFAULT_SOURCE_QUOTA


def _apply_source_quota(evidences: list, quota: dict[str, int]) -> list:
    """按来源类型限制 Evidence 数量（v2.1 任务二建议 6）。

    避免 DB 行级 Evidence 或单一来源挤掉其他反证。
    每个来源类型按权重降序保留前 N 条。

    Args:
        evidences: 合并后的 Evidence 列表
        quota: 来源配额 {"rag": 10, "db": 10, "web": 5, "report": 5}

    Returns:
        配额截断后的 Evidence 列表
    """
    if not evidences:
        return []

    from agent.langgraph.evidence.fusion import _evidence_weight

    # 按 source_type 分组
    by_source: dict[str, list] = {}
    for ev in evidences:
        st = ev.get("source_type", "unknown")
        by_source.setdefault(st, []).append(ev)

    result = []
    for st, group in by_source.items():
        limit = quota.get(st, 5)
        # 组内按权重降序排序
        group.sort(key=_evidence_weight, reverse=True)
        kept = group[:limit]
        dropped = len(group) - len(kept)
        if dropped > 0:
            logger.info(
                f"[evidence_fusion] 来源配额截断: source={st}, "
                f"total={len(group)}, kept={len(kept)}, dropped={dropped}"
            )
        result.extend(kept)

    return result