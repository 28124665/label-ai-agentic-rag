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
"""ReAct Executor — 工具执行和 Observation 标准化。

执行 ReAct Action 决定的工具调用，并生成标准化的 ReactObservation。

设计原则：
- 工具结果统一通过 evidence.normalizer 标准化为 Evidence
- 失败/超时不抛出异常，而是返回 success=False 的 observation（便于 ReAct 继续推理）
- 通过 _call_tool_* 私有方法对接现有的 RAGTool / DatabaseTool / WebTool
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from agent.langgraph.evidence.models import Evidence, normalize_tool_result
from agent.langgraph.react.models import ReactAction, ReactObservation

logger = logging.getLogger(__name__)

# 单次工具调用超时（秒）
DEFAULT_TOOL_TIMEOUT = 30.0
# Observation 文本最大字符数
MAX_OBSERVATION_CHARS = 3000


def _make_error_observation(
    action_type: str,
    error: str,
    latency_ms: int = 0,
    risk_level: str = "low",
) -> ReactObservation:
    """构造失败 observation。"""
    return ReactObservation(
        action_type=action_type,
        success=False,
        content="",
        structured_data={},
        error=error,
        latency_ms=latency_ms,
        risk_level=risk_level,
        truncated=False,
        metadata={},
    )


async def _call_rag_tool(
    arguments: dict,
    tenant_id: str,
    llm_id: str,
    kb_ids: list[str],
    query_lang: str = "zh_CN",
) -> dict:
    """调用 RAG Tool。

    复用 agent.langgraph.tools.rag_tool.get_rag_tool()，
    与 LangGraph 主干的 rag_tool_node 保持一致的接口。
    """
    from agent.langgraph.tools.rag_tool import get_rag_tool

    rag_tool = get_rag_tool()
    input_data = {
        "query": arguments.get("query") or arguments.get("query_text") or "",
        "query_simplified": arguments.get("query_simplified", ""),
        "top_k": int(arguments.get("top_k", 5)),
        "enable_rewrite": arguments.get("enable_rewrite", True),
        "enable_rerank": arguments.get("enable_rerank", True),
        "kb_ids": arguments.get("kb_ids") or kb_ids,
        "tenant_id": tenant_id,
        "llm_id": llm_id,
    }
    return await rag_tool.invoke(input_data)


async def _call_db_tool(
    arguments: dict,
    tenant_id: str,
    llm_id: str,
    db_id: str = "",
    mcp_server_name: str = "",
) -> dict:
    """调用 Database Tool。"""
    from agent.langgraph.tools.database_tool import get_database_tool

    db_tool = get_database_tool()
    input_data = {
        "query": arguments.get("query") or arguments.get("sql") or "",
        "db_id": arguments.get("db_id") or db_id,
        "tenant_id": tenant_id,
        "llm_id": llm_id,
        "mcp_server_name": arguments.get("mcp_server_name") or mcp_server_name,
        "enable_self_healing": arguments.get("enable_self_healing", True),
    }
    return await db_tool.invoke(input_data)


async def _call_web_tool(arguments: dict, tenant_id: str) -> dict:
    """调用 Web Tool。"""
    from agent.langgraph.tools.web_tool import get_web_tool

    web_tool = get_web_tool()
    input_data = {
        "query": arguments.get("query") or "",
        "max_results": int(arguments.get("max_results", 5)),
        "search_engine": arguments.get("search_engine", "tavily"),
        "search_depth": arguments.get("search_depth", "basic"),
    }
    return await web_tool.invoke(input_data)


def _truncate_observation_content(content: str) -> tuple[str, bool]:
    """截断 observation 文本。"""
    if not content:
        return "", False
    if len(content) <= MAX_OBSERVATION_CHARS:
        return content, False
    return content[:MAX_OBSERVATION_CHARS] + "…", True


class ReactExecutor:
    """ReAct 工具执行器。

    单例模式：与 LangGraph 的 Tool 风格一致（get_rag_tool 等）。
    """

    _instance: Optional["ReactExecutor"] = None

    def __init__(self):
        pass

    @classmethod
    def get_instance(cls) -> "ReactExecutor":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def execute(
        self,
        action: ReactAction,
        tenant_id: str = "",
        user_id: str = "",
        llm_id: str = "",
        kb_ids: Optional[list[str]] = None,
        db_id: str = "",
        mcp_server_name: str = "",
        query_lang: str = "zh_CN",
    ) -> tuple[ReactObservation, list[Evidence]]:
        """执行一个工具 action。

        Args:
            action: 待执行的 action
            tenant_id: 租户 ID
            user_id: 用户 ID
            llm_id: LLM 模型 ID
            kb_ids: 知识库 ID 列表（rag 工具 fallback）
            db_id: 数据库 ID（db 工具 fallback）
            mcp_server_name: MCP 服务名
            query_lang: 查询语言

        Returns:
            (observation, evidences) 元组：
            - observation: 标准化的执行结果
            - evidences: 由执行结果标准化得到的 Evidence 列表
        """
        action_type = action.get("type", "")
        arguments = action.get("arguments", {}) or {}
        start = time.time()

        if action_type in ("finish", "ask_clarification"):
            # 终态 action 不需要执行工具
            return (
                ReactObservation(
                    action_type=action_type,
                    success=True,
                    content=action.get("purpose", ""),
                    structured_data={},
                    error="",
                    latency_ms=0,
                    risk_level="low",
                    truncated=False,
                    metadata={"purpose": action.get("purpose", "")},
                ),
                [],
            )

        try:
            if action_type == "rag_search":
                raw = await asyncio.wait_for(
                    _call_rag_tool(arguments, tenant_id, llm_id, kb_ids or [], query_lang),
                    timeout=DEFAULT_TOOL_TIMEOUT,
                )
            elif action_type == "db_query":
                raw = await asyncio.wait_for(
                    _call_db_tool(arguments, tenant_id, llm_id, db_id, mcp_server_name),
                    timeout=DEFAULT_TOOL_TIMEOUT,
                )
            elif action_type == "web_search":
                raw = await asyncio.wait_for(
                    _call_web_tool(arguments, tenant_id),
                    timeout=DEFAULT_TOOL_TIMEOUT,
                )
            else:
                return (
                    _make_error_observation(
                        action_type,
                        f"未知 action_type: {action_type}",
                        int((time.time() - start) * 1000),
                    ),
                    [],
                )
        except asyncio.TimeoutError:
            return (
                _make_error_observation(
                    action_type,
                    f"工具调用超时（>{DEFAULT_TOOL_TIMEOUT}s）",
                    int((time.time() - start) * 1000),
                    risk_level="medium",
                ),
                [],
            )
        except Exception as e:
            logger.error(f"[react_executor] 工具执行异常: {e}")
            return (
                _make_error_observation(
                    action_type,
                    f"工具执行异常: {e}",
                    int((time.time() - start) * 1000),
                    risk_level="medium",
                ),
                [],
            )

        latency_ms = int((time.time() - start) * 1000)

        # 标准化为 Evidence
        evidences = normalize_tool_result(
            tool_name=action_type,
            result=raw if isinstance(raw, dict) else {"raw": raw},
            tenant_id=tenant_id,
            query=arguments.get("query", ""),
        )

        # 构造 observation 文本（基于 evidences 摘要）
        if evidences:
            obs_text = "\n\n".join(
                f"[{e.get('source_type', '')}] {e.get('title', '')}: {e.get('content', '')[:500]}"
                for e in evidences
            )
        else:
            obs_text = raw.get("formatted_result", "") if isinstance(raw, dict) else str(raw)
        obs_text, truncated = _truncate_observation_content(obs_text)

        observation: ReactObservation = {
            "action_type": action_type,
            "success": True,
            "content": obs_text,
            "structured_data": {},
            "error": "",
            "latency_ms": latency_ms,
            "risk_level": "low",
            "truncated": truncated,
            "metadata": {
                "raw_keys": list(raw.keys()) if isinstance(raw, dict) else [],
                "evidence_count": len(evidences),
            },
        }

        return observation, evidences


# 模块级便捷函数
def get_react_executor() -> ReactExecutor:
    """获取 ReAct Executor 单例。"""
    return ReactExecutor.get_instance()
