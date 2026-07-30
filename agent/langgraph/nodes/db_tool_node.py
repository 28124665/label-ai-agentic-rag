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
"""数据库工具节点。

调用 SqlAgentRunner 进行数据库查询（内部含模板短路 + SQL Agent 子图 +
legacy 降级链，docs/数据库Tool渐进式披露LLM化落地设计.md）。
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState
from agent.langgraph.tools.sql_agent.runner import get_sql_agent_runner

logger = logging.getLogger(__name__)


async def db_tool_node(state: AgentState) -> dict[str, Any]:
    """数据库工具节点。

    调用 SqlAgentRunner.invoke()，将结果写入 AgentState。
    如果 state 中未指定 db_id，SQL Agent 会通过 list_databases 自主选库。

    当 db_tool_enabled=False 时，本节点直接跳过（防御深度：路由层应该
    已经拦截，节点层作为兜底，避免在某些特殊路径下被误调用）。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()

    # 能力开关：db_tool 禁用时直接跳过（防御深度）
    if not state.get("db_tool_enabled", True):
        logger.warning(
            "[db_tool] db_tool 已禁用，跳过数据库查询（防御深度检查）"
        )
        return {
            "db_result": {},
            "db_quality_score": 0.0,
            "node_timings": {"db_tool": int((time.time() - start_time) * 1000)},
        }

    user_question = state.get("user_question", "")
    query_simplified = state.get("query_simplified", "")
    query_lang = state.get("query_lang", "zh_CN")

    if not user_question:
        logger.warning("[db_tool] 用户问题为空")
        return {
            "db_result": {},
            "db_quality_score": 0.0,
            "node_timings": {"db_tool": int((time.time() - start_time) * 1000)},
        }

    runner = get_sql_agent_runner()

    # 从 agent_config 读取数据库工具参数，保留默认值兜底
    agent_config = state.get("agent_config", {}) or {}
    db_config = agent_config.get("database_config", {}) or {}

    input_data = {
        "query": user_question,
        "query_simplified": query_simplified or user_question,
        "query_lang": query_lang,
        "db_id": state.get("db_id", ""),
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "mcp_server_name": state.get("mcp_server_name", "database_mcp_server"),
        "enable_self_healing": db_config.get("enable_self_healing", True),
        "enable_template": db_config.get("enable_template", True),
        # SQL Agent 扩展参数（空值不传，由 runner 走文件配置/默认值兜底）
        **{
            key: value
            for key, value in {
                "exploration_mode": db_config.get("exploration_mode"),
                "sql_agent_config": db_config.get("sql_agent_config"),
            }.items()
            if value
        },
    }

    try:
        result = await runner.invoke(input_data)

        logger.info(f"[db_tool] 查询完成: rows={result.get('row_count', 0)}, score={result.get('quality_score', 0.0):.2f}, db_id={result.get('db_id', '')}")

        return {
            "db_result": result,
            "db_quality_score": result.get("quality_score", 0.0),
            "node_timings": {"db_tool": int((time.time() - start_time) * 1000)},
        }

    except Exception as e:
        logger.error(f"[db_tool] 查询失败: {e}")
        return {
            "db_result": {},
            "db_quality_score": 0.0,
            "node_timings": {"db_tool": int((time.time() - start_time) * 1000)},
        }
