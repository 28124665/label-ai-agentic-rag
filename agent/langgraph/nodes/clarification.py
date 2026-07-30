#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""主动澄清交互节点。

当意图路由判断需要用户澄清时（confidence < 0.6），通过 LangGraph interrupt 机制
暂停图执行，向用户返回澄清问题。用户回答后通过 Command(resume=...) 恢复执行，
重新进入 intent_router 进行路由。

交互流程：
  intent_router (needs_clarification) → clarification_node → [interrupt]
  → 前端展示澄清问题 → 用户回答 → Command(resume=answer)
  → clarification_node 恢复 → intent_router (重新路由)
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


async def clarification_node(state: AgentState) -> dict[str, Any]:
    """澄清交互节点。

    使用 langgraph interrupt 机制暂停执行，等待用户回答。
    用户回答后，将回答与原始问题合并作为新问题，清空路由决策以触发重新路由。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 clarification_request 与合并后的 user_question
    """
    start_time = time.time()

    route_decision = state.get("route_decision")
    if not route_decision:
        logger.warning("[clarification] 无 route_decision，跳过澄清")
        return {
            "route_target": "chitchat",
            "node_timings": {"clarification": int((time.time() - start_time) * 1000)},
        }

    # 从 route_decision.metadata 提取澄清信息
    metadata = route_decision.metadata or {}
    question = metadata.get("clarification_question", "您的问题不够明确，请补充更多细节以便我更好地为您服务。")
    options = metadata.get("clarification_options", [])
    original_question = state.get("user_question", "")

    clarification_request = {
        "question": question,
        "options": options,
        "original_question": original_question,
    }

    logger.info(f"[clarification] 发起澄清: question='{question}'")

    # 使用 langgraph interrupt 暂停执行
    # interrupt 会暂停图执行，将 clarification_request 返回给调用方
    # 调用方（前端）收到后展示给用户，用户回答后通过 Command(resume=user_answer) 恢复
    try:
        from langgraph.types import interrupt

        user_answer = interrupt(clarification_request)
    except ImportError:
        # 如果 langgraph 版本不支持 interrupt，降级为直接使用原始决策继续
        logger.warning("[clarification] langgraph interrupt 不可用，降级为直接路由")
        return {
            "route_target": route_decision.target,
            "clarification_request": clarification_request,
            "node_timings": {"clarification": int((time.time() - start_time) * 1000)},
        }

    # 用户回答后恢复执行
    # 将用户回答与原始问题合并，作为新的问题重新路由
    if user_answer and isinstance(user_answer, str):
        # 如果用户回答较短，将其附加到原始问题后
        if len(user_answer) < 50:
            new_question = f"{original_question}（补充说明：{user_answer}）"
        else:
            new_question = user_answer
    else:
        new_question = original_question

    logger.info(f"[clarification] 用户回答: '{user_answer}', 新问题: '{new_question}'")

    # 清空 route_target 和 route_decision，触发重新路由
    return {
        "user_question": new_question,
        "clarification_context": user_answer if isinstance(user_answer, str) else "",
        "clarification_request": clarification_request,
        "route_target": "",  # 清空，触发 intent_router 重新路由
        "route_decision": None,
        "node_timings": {"clarification": int((time.time() - start_time) * 1000)},
    }
