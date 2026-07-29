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
"""Answerability Check 节点。

判断当前 Evidence 是否充分回答用户问题。
仅作"轻量级"判断（不调用 LLM），主要基于：
- evidence 数量
- 综合 coverage_score
- 是否存在冲突

输出 answerability_result，主干据此决定后续路径：
- generate: 进入 prompt_assembly
- partial_answer: 进入 prompt_assembly 并标记"部分回答"
- ask_clarification: 进入 clarification 节点
- react_continue: 回到 react_subgraph 补充（暂未实现回流边）
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agent.langgraph.evidence.answerability import check_answerability
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


async def answerability_check_node(state: AgentState) -> dict[str, Any]:
    """Answerability Check 节点。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段（含 answerability_result）
    """
    start_time = time.time()
    node_name = "answerability_check"

    evidences = state.get("evidence", []) or []
    user_question = state.get("user_question", "")

    result = check_answerability(
        evidences=evidences,
        user_question=user_question,
    )

    logger.info(
        f"[{node_name}] 答案充分性: answerable={result.get('answerable')}, "
        f"coverage={result.get('coverage_score')}, action={result.get('recommended_action')}"
    )

    return {
        "answerability_result": result,
        "node_timings": {node_name: int((time.time() - start_time) * 1000)},
    }


def answerability_routing(state: AgentState) -> str:
    """Answerability 条件路由函数。

    Returns:
        str: 下一节点名（quality_check / clarification）
    """
    result = state.get("answerability_result") or {}
    action = result.get("recommended_action", "generate")

    # 当前阶段：仅支持 generate / partial_answer → quality_check
    # ask_clarification 暂不主动打断主流程（保留与现有 clarification_node 解耦）
    if action in ("generate", "partial_answer", "react_continue"):
        return "quality_check"
    if action == "ask_clarification":
        # 默认不打断主流程，沿用 quality_check
        return "quality_check"
    return "quality_check"
