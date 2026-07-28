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
"""RAG 工具节点。

调用 RAGTool 进行知识库检索。
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState
from agent.langgraph.tools.rag_tool import get_rag_tool

logger = logging.getLogger(__name__)


async def rag_tool_node(state: AgentState) -> dict[str, Any]:
    """RAG 工具节点。

    调用 RAGTool.invoke()，将结果写入 AgentState。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()

    user_question = state.get("user_question", "")
    query_simplified = state.get("query_simplified", "")

    if not user_question:
        logger.warning("[rag_tool] 用户问题为空")
        return {
            "rag_docs": [],
            "rag_quality_score": 0.0,
            "rag_has_relevant": False,
            "rag_relevant_count": 0,
            "rag_top_score": 0.0,
            "node_timings": {"rag_tool": int((time.time() - start_time) * 1000)},
        }

    rag_tool = get_rag_tool()

    input_data = {
        "query": user_question,
        "query_simplified": query_simplified or user_question,
        "top_k": 5,
        "enable_rewrite": True,
        "enable_rerank": True,
        "kb_ids": state.get("kb_ids", []),
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "cross_languages": [],
    }

    try:
        result = await rag_tool.invoke(input_data)

        logger.info(
            f"[rag_tool] 检索完成: docs={len(result.get('docs', []))}, "
            f"score={result.get('quality_score', 0.0):.2f}, "
            f"detected_lang={result.get('detected_lang', 'zh_CN')}"
        )

        return {
            "rag_docs": result.get("docs", []),
            "rag_quality_score": result.get("quality_score", 0.0),
            "rag_has_relevant": result.get("has_relevant", False),
            "rag_relevant_count": result.get("relevant_count", 0),
            "rag_top_score": result.get("top_score", 0.0),
            "query_lang": result.get("detected_lang", state.get("query_lang", "zh_CN")),
            "query_simplified": result.get("query_simplified", query_simplified),
            "node_timings": {"rag_tool": int((time.time() - start_time) * 1000)},
        }

    except Exception as e:
        logger.error(f"[rag_tool] 检索失败: {e}")
        return {
            "rag_docs": [],
            "rag_quality_score": 0.0,
            "rag_has_relevant": False,
            "rag_relevant_count": 0,
            "rag_top_score": 0.0,
            "node_timings": {"rag_tool": int((time.time() - start_time) * 1000)},
        }
