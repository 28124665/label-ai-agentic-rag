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
"""Web 工具节点。

调用 WebTool 进行 Web 搜索。
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState
from agent.langgraph.tools.web_tool import get_web_tool

logger = logging.getLogger(__name__)


async def web_tool_node(state: AgentState) -> dict[str, Any]:
    """Web 工具节点。

    调用 WebTool.invoke()，将结果写入 AgentState。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()

    user_question = state.get("user_question", "")
    query_lang = state.get("query_lang", "zh_CN")

    if not user_question:
        logger.warning("[web_tool] 用户问题为空")
        return {
            "web_docs": [],
            "node_timings": {"web_tool": int((time.time() - start_time) * 1000)},
        }

    web_tool = get_web_tool()

    input_data = {
        "query": user_question,
        "query_lang": query_lang,
        "search_engine": "tavily",
        "max_results": 6,
        "api_key": "",
    }

    try:
        result = await web_tool.invoke(input_data)

        logger.info(f"[web_tool] 搜索完成: docs={result.get('result_count', 0)}")

        return {
            "web_docs": result.get("docs", []),
            "node_timings": {"web_tool": int((time.time() - start_time) * 1000)},
        }

    except Exception as e:
        logger.error(f"[web_tool] 搜索失败: {e}")
        return {
            "web_docs": [],
            "node_timings": {"web_tool": int((time.time() - start_time) * 1000)},
        }
