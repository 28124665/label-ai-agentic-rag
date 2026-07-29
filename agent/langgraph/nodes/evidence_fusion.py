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
"""Evidence Fusion 节点。

将多个 source_type 的 Evidence 合并为有序、去重、可信度加权的列表。
为后续 prompt_assembly 提供高质量的证据集合。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agent.langgraph.evidence.fusion import fuse_evidences
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)

# Evidence Fusion 默认参数
DEFAULT_MAX_EVIDENCE_COUNT = 20
DEFAULT_TOKEN_BUDGET = 4000


async def evidence_fusion_node(state: AgentState) -> dict[str, Any]:
    """Evidence 融合节点。

    1. 收集所有 Evidence（state.evidence + 已有工具结果 rag_docs/db_result/web_docs）
    2. 调用 fuse_evidences() 融合
    3. 输出 fusion_result（含 fused list、conflicts、token_budget）
    4. 写回 state.evidence（替换为融合后的有序列表）

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()
    node_name = "evidence_fusion"

    evidences = list(state.get("evidence", []) or [])

    # 兼容：如果 state.evidence 为空，从工具结果补充
    if not evidences:
        from agent.langgraph.evidence.models import normalize_tool_result

        tenant_id = state.get("tenant_id", "")

        # RAG
        rag_docs = state.get("rag_docs", []) or []
        if rag_docs:
            evidences.extend(
                normalize_tool_result(
                    tool_name="rag_search",
                    result={"docs": rag_docs},
                    tenant_id=tenant_id,
                    query=state.get("user_question", ""),
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
                    query=state.get("user_question", ""),
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
                    query=state.get("user_question", ""),
                )
            )

    # 调用 fusion
    fusion_result = fuse_evidences(
        evidences=evidences,
        max_count=DEFAULT_MAX_EVIDENCE_COUNT,
        token_budget=DEFAULT_TOKEN_BUDGET,
    )

    fused_evidences = fusion_result.get("fused", [])
    conflicts = fusion_result.get("conflicts", [])

    logger.info(
        f"[{node_name}] Evidence 融合完成: fused_count={len(fused_evidences)}, "
        f"conflicts={len(conflicts)}, dropped={fusion_result.get('dropped_count', 0)}"
    )

    return {
        "evidence": fused_evidences,
        "evidence_fusion_result": {
            "fused_count": len(fused_evidences),
            "conflicts": conflicts,
            "token_budget": fusion_result.get("token_budget", DEFAULT_TOKEN_BUDGET),
            "dropped_count": fusion_result.get("dropped_count", 0),
        },
        "node_timings": {node_name: int((time.time() - start_time) * 1000)},
    }
