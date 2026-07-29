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
"""ReAct 子图节点（LangGraph 主干节点）。

该节点是 ReAct 子图在 LangGraph 主干图中的入口。
内部调用 react.graph.ReactSubgraph 执行多步推理，
产出 ReactExecutionResult 并写回 AgentState。

设计原则（docs §2.1, §5.1）：
- ReAct 不作为默认路径，只处理复杂任务
- 不直接返回最终答案
- 失败时优雅降级（不阻塞主流程）
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from agent.langgraph.react.graph import ReactSubgraph, get_react_subgraph
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


async def _resolve_llm_callable(state: AgentState):
    """从 state 解析 LLM callable。

    优先级：
    1. state["react_state"].get("llm_callable")（由外部注入）
    2. 走 TenantLLMService 默认实现
    """
    react_state = state.get("react_state") or {}
    external = react_state.get("llm_callable")
    if external is not None:
        return external

    # 走 TenantLLMService
    try:
        from api.db.services.tenant_llm_service import TenantLLMService  # noqa
        from common.constants import LLMType

        llm_id = state.get("llm_id", "")
        tenant_id = state.get("tenant_id", "")

        if not (llm_id and tenant_id):
            logger.warning(
                f"[react_subgraph_node] 缺少 llm_id 或 tenant_id（llm_id={llm_id}, tenant_id={tenant_id}），使用默认 LLM"
            )
            return None

        # 异步调用 LLM
        async def _llm(prompt: str) -> str:
            try:
                from api.db.services.tenant_llm_service import TenantLLMService
                from common.constants import LLMType

                # TenantLLMService 内部可能是同步，统一 run_in_executor
                loop = asyncio.get_event_loop()
                # 由于现有 LLM 接口差异较大，这里仅作示例；生产建议注入统一 LLM
                return await loop.run_in_executor(
                    None,
                    lambda: TenantLLMService.inference(
                        tenant_id, llm_id, LLMType.CHAT, prompt
                    ),
                )
            except Exception as e:
                logger.error(f"[react_subgraph_node] TenantLLM 调用失败: {e}")
                # 失败时返回 finish
                import json

                return json.dumps(
                    {
                        "thought_summary": "LLM 调用失败",
                        "action": {
                            "type": "finish",
                            "tool_name": "finish",
                            "purpose": "LLM error fallback",
                            "arguments": {"summary": "LLM error"},
                        },
                        "stop": True,
                    },
                    ensure_ascii=False,
                )

        return _llm
    except ImportError:
        logger.warning("[react_subgraph_node] 无法导入 TenantLLMService，使用默认 LLM")
        return None


async def react_subgraph_node(state: AgentState) -> dict[str, Any]:
    """ReAct 子图节点（LangGraph 主干入口）。

    调用 ReactSubgraph.run() 执行多步推理，
    将结果写入 react_execution_result 和 evidence。

    失败时返回降级结果（finish_reason="error"），
    不抛出异常，避免阻塞主流程。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()
    node_name = "react_subgraph"

    user_question = state.get("user_question", "")
    tenant_id = state.get("tenant_id", "")
    user_id = state.get("user_id", "")
    llm_id = state.get("llm_id", "")
    kb_ids = state.get("kb_ids", [])
    db_id = state.get("db_id", "")
    mcp_server_name = state.get("mcp_server_name", "")
    query_lang = state.get("query_lang", "zh_CN")

    agent_config = state.get("agent_config", {}) or {}

    # 能力开关：react 禁用时直接返回降级结果（防御深度：路由层应该已经拦截）
    if not state.get("react_enabled", False):
        logger.warning(
            "[react_subgraph] react 已禁用，跳过 ReAct 子图（防御深度检查）"
        )
        return {
            "react_execution_result": {
                "success": False,
                "evidence": [],
                "step_count": 0,
                "finish_reason": "disabled",
                "summary": "ReAct 子图未启用",
            },
            "evidence": state.get("evidence", []),
            "node_timings": {node_name: int((time.time() - start_time) * 1000)},
        }

    if not user_question:
        logger.warning(f"[{node_name}] 用户问题为空，跳过 ReAct 子图")
        return {
            "react_execution_result": {
                "success": False,
                "evidence": [],
                "step_count": 0,
                "finish_reason": "empty_question",
                "summary": "用户问题为空",
            },
            "evidence": state.get("evidence", []),
            "node_timings": {node_name: int((time.time() - start_time) * 1000)},
        }

    # 解析 LLM callable
    llm_callable = await _resolve_llm_callable(state)

    # 构造子图
    subgraph = ReactSubgraph(agent_config=agent_config)

    try:
        result = await subgraph.run(
            user_question=user_question,
            tenant_id=tenant_id,
            user_id=user_id,
            llm_id=llm_id,
            kb_ids=kb_ids,
            db_id=db_id,
            mcp_server_name=mcp_server_name,
            query_lang=query_lang,
            llm_callable=llm_callable,
        )
    except Exception as e:
        logger.error(f"[{node_name}] ReAct 子图执行异常: {e}", exc_info=True)
        # 优雅降级
        result = {
            "success": False,
            "evidence": [],
            "step_count": 0,
            "finish_reason": "error",
            "summary": f"ReAct 子图执行异常: {e}",
        }

    # 合并 evidence（保留主干上已收集的 evidence，例如 rag_tool_node 的结果）
    existing_evidence = state.get("evidence", []) or []
    new_evidence = result.get("evidence", []) or []
    merged_evidence = list(existing_evidence)
    seen_ids: set[str] = set()
    for ev in existing_evidence:
        seen_ids.add(ev.get("evidence_id", ""))
    for ev in new_evidence:
        eid = ev.get("evidence_id", "")
        if eid and eid not in seen_ids:
            merged_evidence.append(ev)
            seen_ids.add(eid)
        elif not eid:
            merged_evidence.append(ev)

    logger.info(
        f"[{node_name}] ReAct 子图完成: success={result.get('success')}, "
        f"step_count={result.get('step_count')}, "
        f"evidence_count={len(merged_evidence)}, "
        f"finish_reason={result.get('finish_reason')}"
    )

    return {
        "react_execution_result": result,
        "evidence": merged_evidence,
        "node_timings": {node_name: int((time.time() - start_time) * 1000)},
    }
