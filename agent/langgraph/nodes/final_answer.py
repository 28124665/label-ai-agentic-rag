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
"""最终答案节点。

返回最终答案给用户，支持流式输出。
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


def final_answer_node(state: AgentState) -> dict[str, Any]:
    """最终答案节点。

    根据幻觉检测结果返回最终答案：
    - pass/filter：返回生成的答案
    - regenerate：返回保守答案或拒答
    - reject：返回拒答消息

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 final_answer
    """
    start_time = time.time()

    generated_answer = state.get("generated_answer", "")
    hallucination_action = state.get("hallucination_action", "pass")
    query_lang = state.get("query_lang", "zh_CN")

    # 根据幻觉检测动作决定最终答案
    if hallucination_action == "reject":
        # 严重幻觉，直接拒答
        final_answer = _get_rejection_message(query_lang)
        logger.info("[final_answer] 幻觉检测拒答")
    elif hallucination_action == "regenerate":
        # 需要重新生成，但当前已无更多重试机会
        # 返回保守答案（基于检索上下文）
        final_answer = _get_conservative_answer(
            state.get("merged_context", ""),
            query_lang,
        )
        logger.info("[final_answer] 幻觉检测需要重新生成，返回保守答案")
    elif hallucination_action == "filter":
        # 过滤后的答案（当前简化为直接返回原答案）
        # TODO: 集成 HallucinationDetector 的过滤逻辑
        final_answer = generated_answer or ""
        logger.info("[final_answer] 幻觉检测过滤，返回原答案")
    else:
        # pass：直接返回生成的答案
        final_answer = generated_answer or ""
        logger.info("[final_answer] 幻觉检测通过，返回生成答案")

    logger.info(
        f"[final_answer] 最终答案: "
        f"length={len(final_answer)}, action={hallucination_action}"
    )

    return {
        "final_answer": final_answer,
        "node_timings": {"final_answer": int((time.time() - start_time) * 1000)},
    }


def _get_rejection_message(query_lang: str) -> str:
    """获取拒答消息。

    Args:
        query_lang: 查询语言

    Returns:
        str: 拒答消息
    """
    messages = {
        "zh_CN": "抱歉，根据现有参考资料无法回答您的问题。建议您提供更多相关信息。",
        "zh_TW": "抱歉，根據現有參考資料無法回答您的問題。建議您提供更多相關資訊。",
        "en": "I'm sorry, I cannot answer your question based on the available references. Please provide more relevant information.",
    }
    return messages.get(query_lang, messages["zh_CN"])


def _get_conservative_answer(merged_context: str, query_lang: str) -> str:
    """获取保守答案（基于检索上下文）。

    当幻觉检测需要重新生成但无法重新生成时，
    返回一个保守的答案，仅包含检索到的信息。

    Args:
        merged_context: 合并后的上下文
        query_lang: 查询语言

    Returns:
        str: 保守答案
    """
    if not merged_context:
        return _get_rejection_message(query_lang)

    # 保守策略：直接返回检索上下文摘要
    prefix = {
        "zh_CN": "根据现有参考资料，以下是相关信息：\n\n",
        "zh_TW": "根據現有參考資料，以下是相關資訊：\n\n",
        "en": "Based on the available references, here is the relevant information:\n\n",
    }
    return prefix.get(query_lang, prefix["zh_CN"]) + merged_context[:2000]
