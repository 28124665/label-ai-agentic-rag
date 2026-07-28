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
"""LLM 生成节点。

调用 LLM 生成答案，支持流式输出。

参考原有实现：
- agent/component/generate.py: Generate 组件
- api/db/services/llm_service.py: LLMBundle
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


async def llm_generate_node(state: AgentState) -> dict[str, Any]:
    """LLM 生成节点。

    调用 LLM 生成答案，支持流式输出。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 generated_answer
    """
    start_time = time.time()

    final_prompt = state.get("final_prompt", "")

    if not final_prompt:
        logger.warning("[llm_generate] Prompt 为空，跳过 LLM 生成")
        return {
            "generated_answer": "",
            "node_timings": {"llm_generate": int((time.time() - start_time) * 1000)},
        }

    try:
        chat_mdl = _get_chat_model(
            tenant_id=state.get("tenant_id", ""),
            llm_id=state.get("llm_id", ""),
        )

        if chat_mdl is None:
            logger.warning("[llm_generate] LLM 模型未配置，返回空答案")
            return {
                "generated_answer": "",
                "node_timings": {"llm_generate": int((time.time() - start_time) * 1000)},
            }

        history = [{"role": "user", "content": final_prompt}]

        answer = await chat_mdl.async_chat(
            "",
            history,
            {"temperature": 0.7, "max_tokens": 2048},
        )

        if not answer or "**ERROR**" in answer:
            logger.error(f"[llm_generate] LLM 生成失败: {answer}")
            answer = ""

        logger.info(f"[llm_generate] LLM 生成完成: answer_len={len(answer)}")

        return {
            "generated_answer": answer,
            "node_timings": {"llm_generate": int((time.time() - start_time) * 1000)},
        }

    except Exception as e:
        logger.error(f"[llm_generate] LLM 生成异常: {e}")
        return {
            "generated_answer": "",
            "node_timings": {"llm_generate": int((time.time() - start_time) * 1000)},
        }


def _get_chat_model(tenant_id: str, llm_id: str) -> Any:
    """获取 Chat 模型。

    参考 agent/component/generate.py 的模型获取逻辑。

    Args:
        tenant_id: 租户 ID
        llm_id: LLM 模型 ID

    Returns:
        Chat 模型实例，如果未配置则返回 None
    """
    if not tenant_id or not llm_id:
        return None

    try:
        from api.db.services.llm_service import LLMBundle
        from api.db.joint_services.tenant_model_service import (
            get_model_config_by_type_and_name,
        )
        from common.constants import LLMType

        chat_model_config = get_model_config_by_type_and_name(
            tenant_id, LLMType.CHAT, llm_id
        )
        if chat_model_config:
            return LLMBundle(tenant_id, chat_model_config)
        return None
    except Exception as e:
        logger.warning(f"[llm_generate] 获取 Chat 模型失败: {e}")
        return None
