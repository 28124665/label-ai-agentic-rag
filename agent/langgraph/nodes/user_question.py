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
from api.utils.tracing import get_current_trace_id, get_tracer

logger = logging.getLogger(__name__)

# 获取 OTel tracer(OTel 未初始化时为 None)
_tracer = get_tracer(__name__)


def user_question_node(state: AgentState) -> dict[str, Any]:
    """用户问题入口节点。

    接收用户输入，调用历史 RAGFlow 语言检测与繁转简能力初始化 AgentState。

    P1-1: trace_id 优先从 OTel context 获取(与 HTTP 请求 trace_id 对齐),
    OTel 未初始化或无 active span 时兜底用 uuid。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()
    graph_start_time = start_time

    # P1-1: 优先使用 OTel trace_id,兜底 uuid
    otel_trace_id = get_current_trace_id()
    trace_id = otel_trace_id or str(uuid.uuid4())

    # 为整个 Agent 执行建立 span
    if _tracer is not None:
        with _tracer.start_as_current_span("agent.user_question") as span:
            span.set_attribute("agent.trace_id", trace_id)
            return _do_user_question(state, start_time, graph_start_time, trace_id)
    return _do_user_question(state, start_time, graph_start_time, trace_id)


def _do_user_question(state: AgentState, start_time: float, graph_start_time: float, trace_id: str) -> dict[str, Any]:
    """实际执行用户问题处理。"""
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
            "trace_id": trace_id,
            "graph_start_time": graph_start_time,
            "react_enabled": False,
            "node_timings": {"question_input": int((time.time() - start_time) * 1000)},
        }

    query_lang, historical_lang = detect_query_language(user_question)
    query_simplified = get_query_simplified(user_question, historical_lang)

    # ★ ReAct 子图能力开关注入（从 agent_config.react.enabled 读取，默认 False）
    agent_config = state.get("agent_config", {}) or {}
    react_config = agent_config.get("react", {}) or {}
    react_enabled = react_config.get("enabled", False) if isinstance(react_config, dict) else False

    logger.info(f"[user_question] 用户输入: '{user_question}', 语言: {query_lang} ({historical_lang}), 简体: '{query_simplified}'")

    return {
        "user_question": user_question,
        "query_lang": query_lang,
        "query_simplified": query_simplified,
        "retry_count": 0,
        "max_retries": 3,
        "trace_id": trace_id,
        "graph_start_time": graph_start_time,
        "react_enabled": react_enabled,
        "node_timings": {"question_input": int((time.time() - start_time) * 1000)},
    }
