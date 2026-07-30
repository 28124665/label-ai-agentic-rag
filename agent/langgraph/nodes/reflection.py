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
"""工具结果反思节点。

在 RAG/DB/Web 工具执行完成后，对结果进行 LLM 驱动的结构化反思。
输出关键发现、信息充分性判断和建议下一步，供下游 quality_check 和 prompt_assembly 使用。

当 LLM 反思超时或失败时，自动降级为 fallback_reflection，不阻塞主流程。
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


async def reflection_node(state: AgentState) -> dict[str, Any]:
    """工具结果反思节点。

    收集 RAG/DB/Web 工具的执行结果，调用 LLM 进行结构化反思。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 reflection_result
    """
    start_time = time.time()

    # 1. 收集工具结果
    tool_results = _collect_tool_results(state)
    if not tool_results:
        logger.info("[reflection] 无工具结果，跳过反思")
        return {
            "reflection_result": None,
            "node_timings": {"reflection": int((time.time() - start_time) * 1000)},
        }

    # 2. 获取 LLM 配置
    tenant_id = state.get("tenant_id", "")
    llm_id = state.get("llm_id", "")
    user_question = state.get("user_question", "")

    if not tenant_id or not llm_id:
        logger.warning("[reflection] 缺少 tenant_id 或 llm_id，使用降级反思")
        result = _safe_fallback(tool_results)
        return {
            "reflection_result": result,
            "node_timings": {"reflection": int((time.time() - start_time) * 1000)},
        }

    # 3. 调用 LLM 反思（带超时降级）
    try:
        from api.utils.reflection import (
            call_reflection_llm,
            fallback_reflection,
            parse_reflection_result,
        )

        llm_output = await call_reflection_llm(
            tenant_id=tenant_id,
            llm_id=llm_id,
            question=user_question,
            tool_results=tool_results,
            timeout_s=5.0,
        )
        result = parse_reflection_result(llm_output)
        logger.info(f"[reflection] 反思完成: info_sufficient={result.get('info_sufficient')}, next_action={result.get('next_action')}")
    except ImportError as e:
        # 共享模块不可用时使用本地降级（不依赖 api.utils.reflection）
        logger.warning(f"[reflection] reflection 模块导入失败，本地降级: {e}")
        result = _safe_fallback(tool_results)
    except Exception as e:
        # LLM 调用失败但共享模块可用，使用共享模块的降级反思（生成 findings 预览）
        logger.warning(f"[reflection] LLM 反思失败，降级: {e}")
        result = fallback_reflection(tool_results)

    return {
        "reflection_result": result,
        "node_timings": {"reflection": int((time.time() - start_time) * 1000)},
    }


def _collect_tool_results(state: AgentState) -> list[dict]:
    """从 state 中收集所有工具执行结果。

    按工具类型收集 RAG/DB/Web 的结果，统一格式化为 {"tool": "...", "content": "..."}。
    同时处理计划执行器（tool_results）的多工具并行结果。
    """
    results = []

    # RAG 结果
    rag_docs = state.get("rag_docs", [])
    if rag_docs:
        rag_content = "\n".join(
            doc.get("content", "")[:500]
            for doc in rag_docs[:5]  # 取前5个文档，每个截断500字
        )
        results.append({"tool": "rag", "content": rag_content})

    # DB 结果
    db_result = state.get("db_result", {})
    if db_result:
        db_content = str(db_result.get("rows", db_result))[:2000]
        results.append({"tool": "database", "content": db_content})

    # Web 结果
    web_docs = state.get("web_docs", [])
    if web_docs:
        web_content = "\n".join(doc.get("content", "")[:500] for doc in web_docs[:5])
        results.append({"tool": "web", "content": web_content})

    # 计划执行器的多工具结果
    tool_results = state.get("tool_results", [])
    if tool_results:
        for tr in tool_results:
            tool = tr.get("tool", "unknown")
            if tool == "rag" and tr.get("rag_docs"):
                content = "\n".join(doc.get("content", "")[:500] for doc in tr["rag_docs"][:3])
            elif tool == "database":
                content = str(tr.get("db_result", ""))[:2000]
            elif tool == "web" and tr.get("web_docs"):
                content = "\n".join(doc.get("content", "")[:500] for doc in tr["web_docs"][:3])
            else:
                content = ""
            if content:
                results.append({"tool": tool, "content": content})

    return results


def _safe_fallback(tool_results: list[dict]) -> dict:
    """安全降级反思（不依赖 api.utils.reflection 模块）。

    当 api.utils.reflection 导入失败时使用此函数。
    返回 info_sufficient=True，让流程继续，不阻塞主流程。
    """
    return {
        "key_findings": [],
        "info_sufficient": True,
        "gaps": [],
        "next_action": "proceed_to_generate",
        "reflection_text": "反思降级：LLM 不可用，默认信息充分",
    }
