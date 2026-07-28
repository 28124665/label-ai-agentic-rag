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

调用 DatabaseTool 进行数据库查询。
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState
from agent.langgraph.tools.database_tool import get_database_tool

logger = logging.getLogger(__name__)


async def db_tool_node(state: AgentState) -> dict[str, Any]:
    """数据库工具节点。

    调用 DatabaseTool.invoke()，将结果写入 AgentState。
    如果 state 中未指定 db_id，DatabaseTool 会根据问题意图自动路由。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()

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

    db_tool = get_database_tool()

    input_data = {
        "query": user_question,
        "query_simplified": query_simplified or user_question,
        "query_lang": query_lang,
        "db_id": state.get("db_id", ""),
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "mcp_server_name": state.get("mcp_server_name", "database_mcp_server"),
        "enable_self_healing": True,
        "enable_template": True,
    }

    try:
        result = await db_tool.invoke(input_data)

        logger.info(
            f"[db_tool] 查询完成: rows={result.get('row_count', 0)}, "
            f"score={result.get('quality_score', 0.0):.2f}, "
            f"db_id={result.get('db_id', '')}"
        )

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
