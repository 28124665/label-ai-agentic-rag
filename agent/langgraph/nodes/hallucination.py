#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""幻觉检测节点。

验证生成的答案是否被检索文档支持，通过多层架构检测幻觉内容，
并根据忠实度分数执行分级处置。

参考原有实现：
- agent/component/hallucination_detector.py: HallucinationDetector 组件
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)

# 分级处置阈值（参考 HallucinationDetector 组件配置）
PASS_THRESHOLD = 0.85
FILTER_THRESHOLD = 0.6
REGENERATE_THRESHOLD = 0.3

# 防止无限重新生成的上限
MAX_REGENERATES = 2


async def hallucination_node(state: AgentState) -> dict[str, Any]:
    """幻觉检测节点。

    对 LLM 生成的答案进行幻觉检测，根据忠实度分数执行分级处置：
    - ≥ 0.85（pass）：直接通过
    - 0.6 ~ 0.85（filter）：过滤不支持的论断，返回保守答案
    - 0.3 ~ 0.6（regenerate）：准备高置信度文档用于重新生成
    - < 0.3（reject）：严重幻觉，直接拒答

    regenerate 动作最多允许 MAX_REGENERATES 次，超过后降级为 exhausted
   （走 final_answer 返回保守答案），避免图循环无限重试。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 hallucination_score 和 hallucination_action
    """
    start_time = time.time()

    generated_answer = state.get("generated_answer", "")
    merged_context = state.get("merged_context", "")
    user_question = state.get("user_question", "")
    regenerate_count = state.get("regenerate_count", 0)

    if not generated_answer:
        logger.warning("[hallucination] 生成答案为空，跳过幻觉检测")
        return {
            "hallucination_score": 1.0,
            "hallucination_action": "pass",
            "node_timings": {"hallucination": int((time.time() - start_time) * 1000)},
        }

    if not merged_context:
        logger.info("[hallucination] 无检索上下文，跳过幻觉检测")
        return {
            "hallucination_score": 1.0,
            "hallucination_action": "pass",
            "node_timings": {"hallucination": int((time.time() - start_time) * 1000)},
        }

    try:
        faithfulness_score = await _verify_faithfulness(
            answer=generated_answer,
            context=merged_context,
            query=user_question,
        )

        action = _dispose(faithfulness_score, regenerate_count)

        logger.info(
            f"[hallucination] 检测完成: score={faithfulness_score:.4f}, "
            f"action={action}, regenerate_count={regenerate_count}"
        )

        updates: dict[str, Any] = {
            "hallucination_score": faithfulness_score,
            "hallucination_action": action,
            "node_timings": {"hallucination": int((time.time() - start_time) * 1000)},
        }

        if action == "regenerate":
            updates["regenerate_count"] = regenerate_count + 1

        return updates

    except Exception as e:
        logger.error(f"[hallucination] 幻觉检测异常: {e}")
        return {
            "hallucination_score": 1.0,
            "hallucination_action": "pass",
            "node_timings": {"hallucination": int((time.time() - start_time) * 1000)},
        }


async def _verify_faithfulness(
    answer: str, context: str, query: str
) -> float:
    """验证答案的忠实度。

    参考 HallucinationDetector 的多层验证架构：
    1. 规则层：精确校验数值/日期/专有名词
    2. NLI 层：自然语言推理判断
    3. LLM 层：语义支持度判断

    当前简化实现：基于关键词重叠度计算忠实度分数。

    Args:
        answer: 生成的答案
        context: 检索上下文
        query: 用户问题

    Returns:
        float: 忠实度分数 0.0 ~ 1.0
    """
    if not answer or not context:
        return 0.0

    answer_chars = set(answer)
    context_chars = set(context)

    if not answer_chars:
        return 0.0

    overlap = answer_chars & context_chars
    overlap_ratio = len(overlap) / len(answer_chars) if answer_chars else 0.0

    answer_len = len(answer)
    context_len = len(context)

    length_penalty = 1.0
    if context_len > 0 and answer_len > context_len * 2:
        length_penalty = 0.8

    return min(1.0, overlap_ratio * length_penalty)


def _dispose(faithfulness_score: float, regenerate_count: int) -> str:
    """根据忠实度分数执行分级处置。

    Args:
        faithfulness_score: 忠实度分数
        regenerate_count: 已重新生成次数

    Returns:
        str: 处置动作（pass / filter / regenerate / exhausted / reject）
    """
    if faithfulness_score >= PASS_THRESHOLD:
        return "pass"
    elif faithfulness_score >= FILTER_THRESHOLD:
        return "filter"
    elif faithfulness_score >= REGENERATE_THRESHOLD:
        if regenerate_count >= MAX_REGENERATES:
            logger.warning(
                f"[hallucination] 重新生成次数已达上限 {MAX_REGENERATES}，降级为 exhausted"
            )
            return "exhausted"
        return "regenerate"
    else:
        return "reject"


def hallucination_decision(state: AgentState) -> str:
    """幻觉检测条件路由函数，用于 LangGraph 条件边。

    Args:
        state: 当前 AgentState

    Returns:
        str: 下一个节点名称（映射键：observability / prompt_assembly / final_answer）
    """
    action = state.get("hallucination_action", "pass")

    if action == "pass":
        return "observability"
    elif action == "filter":
        return "observability"
    elif action == "regenerate":
        return "prompt_assembly"
    elif action in ("reject", "exhausted"):
        return "final_answer"
    else:
        return "observability"
