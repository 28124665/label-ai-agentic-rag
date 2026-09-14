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
"""统一终止兜底节点。

当主图 termination_router 检测到终止原因时，生成保守答案并直接进入
answer_renderer → observability → answer_output 流程。

设计原则（docs §6）：
- 收集所有可用证据（即使不充分）
- 生成保守、诚实的兜底回答
- 标注终止原因和来源，便于用户理解
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)

# 终止原因 → 用户友好前缀映射
_TERMINATION_PREFIX_MAP = {
    "same_action_loop": "检索未发现新信息",
    "rerank_declining": "检索质量持续下降",
    "budget_exhausted": "已达到推理上限",
    "policy_denied": "部分操作被安全策略限制",
    "retrieval_infra_error": "检索服务暂时不可用",
}

# 默认兜底消息
_DEFAULT_FALLBACK_MESSAGE = "当前信息不足以回答，建议人工介入"


async def fallback_node(state: AgentState) -> dict[str, Any]:
    """统一终止兜底节点。

    职责：
    1. 收集当前所有可用证据
    2. 根据终止原因生成保守答案
    3. 设置 final_answer 进入 answer_renderer

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 final_answer 和 fallback 元数据
    """
    start_time = time.time()
    node_name = "fallback"

    user_question = state.get("user_question", "")
    termination_reason = state.get("termination_reason", "unknown")
    termination_source = state.get("termination_source", "")
    fallback_message = state.get("fallback_message", "")

    # 收集可用证据
    evidence = state.get("evidence", [])
    rag_docs = state.get("rag_docs", [])
    db_result = state.get("db_result", {})
    web_docs = state.get("web_docs", [])

    # 构造保守回答
    reason_prefix = _TERMINATION_PREFIX_MAP.get(
        termination_reason, "处理过程被中断"
    )

    answer_parts = [f"**{reason_prefix}**"]

    # 添加终止详情
    if termination_source:
        answer_parts.append(f"（来源：{termination_source}）")

    if fallback_message:
        answer_parts.append(f"\n{fallback_message}")
    else:
        answer_parts.append(f"\n{_DEFAULT_FALLBACK_MESSAGE}")

    # 如果有可用证据，在兜底回答中列出关键发现
    available_evidence = evidence or rag_docs or web_docs
    if available_evidence:
        answer_parts.append("\n\n### 已获取的部分信息")
        if evidence:
            for ev in evidence[:3]:
                content = ev.get("content", "") or ev.get("text", "") or ""
                if content:
                    answer_parts.append(f"- {content[:200]}...")
        elif rag_docs:
            for doc in rag_docs[:3]:
                content = doc.get("content", "") or doc.get("text", "") or ""
                if content:
                    answer_parts.append(f"- {content[:200]}...")

    if db_result and db_result.get("row_count", 0) > 0:
        answer_parts.append(
            f"\n- 数据库查询返回 {db_result.get('row_count', 0)} 条记录"
        )

    final_answer = "\n".join(answer_parts)

    logger.info(
        f"[{node_name}] 兜底回答已生成: reason={termination_reason}, "
        f"source={termination_source}, "
        f"evidence_count={len(available_evidence)}, "
        f"answer_length={len(final_answer)}"
    )

    return {
        "final_answer": final_answer,
        "quality_decision": "fallback",
        "node_timings": {node_name: int((time.time() - start_time) * 1000)},
    }