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
"""RestTool LangGraph 执行节点。

在 Agent 工作流中作为独立节点执行 REST 调用：
1. 从 state 提取 REST 调用参数
2. 调用 get_rest_tool().invoke()
3. 将结果写入 state

设计文档: docs/RestTool与MCP服务对接设计方案.md §4.5
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState
from agent.langgraph.tools.rest_tool import get_rest_tool

logger = logging.getLogger(__name__)


async def rest_tool_node(state: AgentState) -> dict[str, Any]:
    """RestTool 执行节点。

    流程：
    1. 从 state 提取 REST 调用参数
    2. 调用 get_rest_tool().invoke()
    3. 将结果写入 state

    参数提取策略：
    - 优先使用 PlanStep 参数（复杂任务场景）
    - 否则从 route_decision.metadata 中提取（简单路由场景）
    """
    start_time = time.time()

    route_decision = state.get("route_decision")
    execution_plan = state.get("execution_plan")

    # 优先使用 PlanStep 参数（复杂任务场景）
    if execution_plan and execution_plan.steps:
        rest_steps = [s for s in execution_plan.steps if s.tool == "rest"]
        if rest_steps:
            step = rest_steps[0]
            query = step.args.query or state.get("user_question", "")
            function = step.args.function or ""
            function_params = step.args.function_params or {}
            rest_endpoint = step.args.rest_endpoint
            rest_method = step.args.rest_method or "GET"
            rest_payload = step.args.rest_payload
            erp_domain = step.args.erp_domain
        else:
            query = state.get("user_question", "")
            function = ""
            function_params = {}
            rest_endpoint = ""
            rest_method = "GET"
            rest_payload = None
            erp_domain = ""
    else:
        # 简单路由：从 route_decision.metadata 中提取
        query = state.get("user_question", "")
        metadata = route_decision.metadata if route_decision else {}
        function = metadata.get("function", "")
        function_params = metadata.get("function_params", {})
        rest_endpoint = metadata.get("rest_endpoint", "")
        rest_method = metadata.get("rest_method", "GET")
        rest_payload = metadata.get("rest_payload")
        erp_domain = metadata.get("erp_domain", "")

    input_data = {
        "query": query,
        "query_simplified": state.get("query_simplified", ""),
        "query_lang": state.get("query_lang", "zh_CN"),
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "mcp_server_name": state.get("mcp_server_name", "erp_mcp_server"),
        "function": function,
        "function_params": function_params,
        "rest_endpoint": rest_endpoint,
        "rest_method": rest_method,
        "rest_payload": rest_payload,
        "erp_domain": erp_domain,
    }

    logger.info(
        f"[rest_tool_node] 开始执行: function={function}, "
        f"endpoint={rest_endpoint}, method={rest_method}"
    )

    rest_tool = get_rest_tool()
    result = await rest_tool.invoke(input_data)

    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(
        f"[rest_tool_node] 执行完成: success={result.get('success')}, "
        f"docs={result.get('result_count', 0)}, latency={elapsed_ms}ms"
    )

    return {
        "rest_result": {
            "status_code": result.get("rest_status_code", 0),
            "response": result.get("rest_response", {}),
            "response_text": result.get("rest_response_text", ""),
            "docs": result.get("docs", []),
            "endpoint": result.get("rest_endpoint", ""),
            "method": result.get("rest_method", "GET"),
            "erp_domain": result.get("erp_domain", ""),
        },
        "rest_quality_score": result.get("quality_score", 0.0),
        "agent_iteration_count": state.get("agent_iteration_count", 1) + 1,
        "node_timings": {"rest_tool": elapsed_ms},
    }