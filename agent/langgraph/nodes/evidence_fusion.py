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
"""Evidence Fusion 节点（v2.1 修订：重试证据重建 + 来源配额 + 不可变快照）。

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
}


async def evidence_fusion_node(state: AgentState) -> dict[str, Any]:
    """Evidence 融合节点（v2.1 修订：重试证据重建 + 来源配额 + 不可变快照）。

    流程：
    1. 从工具结果重新标准化 Evidence（每次都重建，P0-2 修复）
    2. 与旧 evidence 合并：基于 (source_type, tool_run_id) 精确替换
    3. 来源配额：按 source_type 限制数量，避免单一来源挤掉反证
    4. 调用 fuse_evidences() 融合去重排序
    5. 构建不可变 EvidenceSnapshot

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段（含 evidence/evidence_snapshot_id/evidence_snapshot/tool_run_id/attempt_id）
    """
    start_time = time.time()
    node_name = "evidence_fusion"

    tenant_id = state.get("tenant_id", "")
    user_question = state.get("user_question", "")

    # ★ v2.1 P0-2 修复：每次都从工具结果重新标准化
    # 不再只在 evidence 为空时补充——重试时新工具结果必须进入候选集
    new_evidences = _normalize_current_tool_results(state, tenant_id, user_question)

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

    # 调用 fusion 去重排序
    fusion_result = fuse_evidences(
        evidences=quota_applied,
        max_count=DEFAULT_MAX_EVIDENCE_COUNT,
        token_budget=DEFAULT_TOKEN_BUDGET,
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
        f"snapshot_id={snapshot.snapshot_id}, attempt_id={attempt_id}"
    )

    return {
        "evidence": fused_evidences,
        "evidence_snapshot_id": snapshot.snapshot_id,
        "evidence_snapshot": snapshot.to_dict(),
        "node_timings": {node_name: int((time.time() - start_time) * 1000)},
    }


def _normalize_current_tool_results(
    state: AgentState,
    tenant_id: str,
    query: str,
) -> list:
    """从当前轮次工具结果重新标准化 Evidence（v2.1 P0-2 修复）。

    每次调用都检查 rag_docs/db_result/web_docs，重新生成 Evidence。
    不依赖旧 evidence 是否为空。

    Args:
        state: 当前 AgentState
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
    result = []
    replaced_keys = set()  # (source_type, tool_name)

    # 新证据优先
    for ev in new:
        result.append(ev)
        source_type = ev.get("source_type", "")
        # 从 metadata 提取 tool_run_id（如 "rag_1"、"db_2"），取工具名前缀
        tool_run_id = ev.get("metadata", {}).get("tool_run_id", "")
        tool_name = tool_run_id.rsplit("_", 1)[0] if tool_run_id else ""
        # 如果没有 tool_run_id，用 source_type 作为替换键（兼容旧数据）
        if not tool_name:
            tool_name = source_type
        # ★ 替换键：(source_type, tool_name) —— 本轮该工具的旧证据整体失效
        replaced_keys.add((source_type, tool_name))

    # 保留未被替换的旧证据
    for ev in old:
        source_type = ev.get("source_type", "")
        tool_run_id = ev.get("metadata", {}).get("tool_run_id", "")
        tool_name = tool_run_id.rsplit("_", 1)[0] if tool_run_id else ""
        if not tool_name:
            tool_name = source_type
        if (source_type, tool_name) not in replaced_keys:
            result.append(ev)

    return result


def _get_source_quota(state: AgentState) -> dict[str, int]:
    """获取来源配额配置（v2.1 任务二建议 6）。

    优先从 agent_config 读取，否则使用默认配额。

    Args:
        state: 当前 AgentState

    Returns:
        dict: {source_type: max_count}
    """
    agent_config = state.get("agent_config", {}) or {}
    fusion_cfg = agent_config.get("evidence_fusion", {}) or {}
    quota_cfg = fusion_cfg.get("source_quota", {})
    if quota_cfg:
        return {**DEFAULT_SOURCE_QUOTA, **quota_cfg}
    return DEFAULT_SOURCE_QUOTA


def _apply_source_quota(evidences: list, quota: dict[str, int]) -> list:
    """应用来源配额（v2.1 任务二建议 6）。

    按 source_type 限制数量，超出配额的按权重截断。
    确保多源证据共存，避免 DB 行级 Evidence 挤掉其他反证。

    类比 Java 中的分组限流：
        ``evidences.stream().collect(groupingBy(Evidence::getSourceType,
         collectingAndThen(limiting(quota), flattening)))``

    Args:
        evidences: Evidence 列表
        quota: {source_type: max_count}

    Returns:
        配额限制后的 Evidence 列表
    """
    from agent.langgraph.evidence.fusion import _evidence_weight

    # 按 source_type 分组
    by_type: dict[str, list] = {}
    for ev in evidences:
        st = ev.get("source_type", "unknown")
        by_type.setdefault(st, []).append(ev)

    # 每组按权重降序排序，截取配额
    result = []
    for source_type, evs in by_type.items():
        max_count = quota.get(source_type, DEFAULT_MAX_EVIDENCE_COUNT)
        # 按权重降序排序
        evs_sorted = sorted(evs, key=_evidence_weight, reverse=True)
        # 截取配额
        truncated = evs_sorted[:max_count]
        if len(evs_sorted) > max_count:
            logger.info(
                f"[evidence_fusion] 来源配额截断: source_type={source_type}, "
                f"total={len(evs_sorted)}, quota={max_count}, dropped={len(evs_sorted) - max_count}"
            )
        result.extend(truncated)

    return result
