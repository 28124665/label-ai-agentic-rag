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
"""用户问题入口节点。

负责接收用户输入，检测语言并做繁转简，初始化 AgentState。
"""

import logging
import time
import uuid
from typing import Any

from agent.langgraph.state import AgentState
from agent.langgraph.utils.lang_utils import (
    detect_query_language,
    get_query_simplified,
)

logger = logging.getLogger(__name__)


def user_question_node(state: AgentState) -> dict[str, Any]:
    """用户问题入口节点。

    接收用户输入，调用历史 RAGFlow 语言检测与繁转简能力初始化 AgentState。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()
    graph_start_time = start_time

    user_question = state.get("user_question", "")

    if not user_question:
        logger.warning("[user_question] 用户问题为空")
        return {
            "user_question": "",
            "query_lang": "zh_CN",
            "query_simplified": "",
            "route_target": "chitchat",
            "retry_count": 0,
            "max_retries": 3,
            "trace_id": str(uuid.uuid4()),
            "graph_start_time": graph_start_time,
            "node_timings": {"question_input": int((time.time() - start_time) * 1000)},
        }

    query_lang, historical_lang = detect_query_language(user_question)
    query_simplified = get_query_simplified(user_question, historical_lang)

    logger.info(f"[user_question] 用户输入: '{user_question}', 语言: {query_lang} ({historical_lang}), 简体: '{query_simplified}'")

    return {
        "user_question": user_question,
        "query_lang": query_lang,
        "query_simplified": query_simplified,
        "retry_count": 0,
        "max_retries": 3,
        "trace_id": str(uuid.uuid4()),
        "graph_start_time": graph_start_time,
        "node_timings": {"question_input": int((time.time() - start_time) * 1000)},
    }
