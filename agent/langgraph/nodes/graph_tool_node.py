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
"""GraphTool LangGraph 执行节点。

在 Agent 工作流中作为独立节点执行图问答（GraphRAG）调用：
1. 从 state 提取图查询参数
2. 调用 get_graph_tool().invoke()
3. 将结果写入 state

设计文档: docs/GraphTool接入设计文档.md §4.2
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState
from agent.langgraph.tools.graph_tool import get_graph_tool

logger = logging.getLogger(__name__)


async def graph_tool_node(state: AgentState) -> dict[str, Any]:
    """GraphTool 执行节点。

    流程：
    1. 从 state 提取图查询参数
    2. 调用 get_graph_tool().invoke()
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
        graph_steps = [s for s in execution_plan.steps if s.tool == "graph"]
        if graph_steps:
            step = graph_steps[0]
            query = step.args.query or state.get("user_question", "")
            source_type = step.args.source_type or "Meeting"
            enable_pg = step.args.enable_pg
            max_rows = step.args.max_rows
            max_result_chars = step.args.max_result_chars
            enable_hybrid_retrieval = step.args.enable_hybrid_retrieval
            timeout_ms = step.args.timeout_ms
        else:
            query = state.get("user_question", "")
            source_type = "Meeting"
            enable_pg = False
            max_rows = 20
            max_result_chars = 12000
            enable_hybrid_retrieval = False
            timeout_ms = 30000
    else:
        # 简单路由：从 route_decision.metadata 中提取
        query = state.get("user_question", "")
        metadata = route_decision.metadata if route_decision else {}
        source_type = metadata.get("source_type", "Meeting")
        enable_pg = metadata.get("enable_pg", False)
        max_rows = metadata.get("max_rows", 20)
        max_result_chars = metadata.get("max_result_chars", 12000)
        enable_hybrid_retrieval = metadata.get("enable_hybrid_retrieval", False)
        timeout_ms = metadata.get("timeout_ms", 30000)

    input_data = {
        "query": query,
        "query_simplified": state.get("query_simplified", ""),
        "source_type": source_type,
        "max_rows": max_rows,
        "max_result_chars": max_result_chars,
        "enable_hybrid_retrieval": enable_hybrid_retrieval,
        "enable_pg": enable_pg,
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "timeout_ms": timeout_ms,
    }

    logger.info(
        f"[graph_tool_node] 开始执行: source_type={source_type}, "
        f"max_rows={max_rows}"
    )

    # base_url 注入：agent_config.graph_config.base_url > 环境变量 > 默认值
    agent_config = state.get("agent_config", {}) or {}
    graph_config = agent_config.get("graph_config", {}) or {}
    base_url = graph_config.get("base_url")

    graph_tool = get_graph_tool(base_url)
    result = await graph_tool.invoke(input_data)

    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(
        f"[graph_tool_node] 执行完成: success={result.get('success')}, "
        f"row_count={result.get('row_count', 0)}, latency={elapsed_ms}ms"
    )

    return {
        "graph_result": {
            "answer": result.get("answer", ""),
            "rows": result.get("rows", []),
            "row_count": result.get("row_count", 0),
            "error": result.get("error", ""),
            "quality_score": result.get("quality_score", 0.0),
            "repair_trace": result.get("repair_trace", []),
            "query_stats": result.get("query_stats", {}),
            "planner": result.get("planner", {}),
            "cypher_query": result.get("cypher_query", ""),
            "cypher_params": result.get("cypher_params", {}),
            "pg_rows": result.get("pg_rows", []),
            "pg_row_count": result.get("pg_row_count", 0),
            "pg_query_log": result.get("pg_query_log", []),
            "pg_query_stats": result.get("pg_query_stats", {}),
            "pg_repair_trace": result.get("pg_repair_trace", []),
        },
        "graph_quality_score": result.get("quality_score", 0.0),
        "graph_score_source": "graph",
        "graph_has_result": bool(result.get("row_count", 0) > 0),
        "graph_row_count": result.get("row_count", 0),
        "agent_iteration_count": state.get("agent_iteration_count", 1) + 1,
        "node_timings": {"graph_tool": elapsed_ms},
    }