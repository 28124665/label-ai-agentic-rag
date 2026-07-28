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
"""LangGraph 运行器模块。

提供 LangGraphRunner 类，负责编译并执行 LangGraph 状态图。
支持同步执行、流式输出和状态查询。
"""

import asyncio
import logging
from typing import Any, Optional

from agent.langgraph.graph import build_and_compile_agent_graph
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


class LangGraphRunner:
    """LangGraph 运行器。

    负责编译并执行 LangGraph 状态图。
    支持同步执行、流式输出和状态查询。

    注意：由于底层节点均为异步实现，应优先使用 arun / arun_stream。
    run / run_stream 通过 asyncio.run 提供同步入口，仅适用于无事件循环的环境。
    """

    def __init__(self):
        """初始化 LangGraphRunner。"""
        self._compiled_graph = None

    def _get_compiled_graph(self):
        """获取编译后的状态图（懒加载）。"""
        if self._compiled_graph is None:
            self._compiled_graph = build_and_compile_agent_graph()
        return self._compiled_graph

    def _build_initial_state(
        self,
        user_question: str,
        query_lang: str = "zh_CN",
        tenant_id: str = "",
        llm_id: str = "",
        kb_ids: Optional[list[str]] = None,
        db_id: str = "",
        mcp_server_name: str = "database_mcp_server",
        **kwargs,
    ) -> AgentState:
        """构建初始状态。"""
        return {
            "user_question": user_question,
            "query_lang": query_lang,
            "tenant_id": tenant_id,
            "llm_id": llm_id,
            "kb_ids": kb_ids or [],
            "db_id": db_id,
            "mcp_server_name": mcp_server_name,
            "retry_count": 0,
            "max_retries": 3,
            "regenerate_count": 0,
            **kwargs,
        }

    async def arun(
        self,
        user_question: str,
        query_lang: str = "zh_CN",
        tenant_id: str = "",
        llm_id: str = "",
        kb_ids: Optional[list[str]] = None,
        db_id: str = "",
        mcp_server_name: str = "database_mcp_server",
        **kwargs,
    ) -> dict[str, Any]:
        """异步执行 LangGraph 工作流。

        Args:
            user_question: 用户问题
            query_lang: 查询语言
            tenant_id: 租户 ID
            llm_id: LLM 模型 ID
            kb_ids: 知识库 ID 列表
            db_id: 数据库 ID
            mcp_server_name: MCP Server 名称
            **kwargs: 其他参数

        Returns:
            dict: 最终状态，包含 final_answer 等字段
        """
        compiled = self._get_compiled_graph()
        initial_state = self._build_initial_state(
            user_question=user_question,
            query_lang=query_lang,
            tenant_id=tenant_id,
            llm_id=llm_id,
            kb_ids=kb_ids,
            db_id=db_id,
            mcp_server_name=mcp_server_name,
            **kwargs,
        )

        logger.info(f"[LangGraphRunner] 开始执行: question='{user_question}'")

        final_state = await compiled.ainvoke(initial_state)

        logger.info(
            f"[LangGraphRunner] 执行完成: "
            f"answer_len={len(final_state.get('final_answer', ''))}"
        )

        return final_state

    def run(
        self,
        user_question: str,
        query_lang: str = "zh_CN",
        tenant_id: str = "",
        llm_id: str = "",
        kb_ids: Optional[list[str]] = None,
        db_id: str = "",
        mcp_server_name: str = "database_mcp_server",
        **kwargs,
    ) -> dict[str, Any]:
        """同步执行 LangGraph 工作流。

        通过 asyncio.run 在内部创建事件循环运行 arun。如果当前环境
        已有事件循环，请改用 arun 以避免 RuntimeError。

        Returns:
            dict: 最终状态，包含 final_answer 等字段
        """
        return asyncio.run(
            self.arun(
                user_question=user_question,
                query_lang=query_lang,
                tenant_id=tenant_id,
                llm_id=llm_id,
                kb_ids=kb_ids,
                db_id=db_id,
                mcp_server_name=mcp_server_name,
                **kwargs,
            )
        )

    async def arun_stream(
        self,
        user_question: str,
        query_lang: str = "zh_CN",
        tenant_id: str = "",
        llm_id: str = "",
        kb_ids: Optional[list[str]] = None,
        db_id: str = "",
        mcp_server_name: str = "database_mcp_server",
        **kwargs,
    ):
        """异步流式执行 LangGraph 工作流。

        Yields:
            tuple: (node_name, node_output) 元组
        """
        compiled = self._get_compiled_graph()
        initial_state = self._build_initial_state(
            user_question=user_question,
            query_lang=query_lang,
            tenant_id=tenant_id,
            llm_id=llm_id,
            kb_ids=kb_ids,
            db_id=db_id,
            mcp_server_name=mcp_server_name,
            **kwargs,
        )

        logger.info(f"[LangGraphRunner] 开始流式执行: question='{user_question}'")

        async for output in compiled.astream(initial_state):
            for node_name, node_output in output.items():
                logger.info(f"[LangGraphRunner] 节点完成: {node_name}")
                yield node_name, node_output

    def run_stream(
        self,
        user_question: str,
        query_lang: str = "zh_CN",
        tenant_id: str = "",
        llm_id: str = "",
        kb_ids: Optional[list[str]] = None,
        db_id: str = "",
        mcp_server_name: str = "database_mcp_server",
        **kwargs,
    ):
        """同步流式执行 LangGraph 工作流。

        通过 asyncio.run 在内部创建事件循环运行 arun_stream。
        如果当前环境已有事件循环，请改用 arun_stream。

        Yields:
            tuple: (node_name, node_output) 元组
        """
        yield from asyncio.run(
            self._collect_stream(
                user_question=user_question,
                query_lang=query_lang,
                tenant_id=tenant_id,
                llm_id=llm_id,
                kb_ids=kb_ids,
                db_id=db_id,
                mcp_server_name=mcp_server_name,
                **kwargs,
            )
        )

    async def _collect_stream(self, **kwargs) -> list[tuple[str, Any]]:
        """收集流式输出为列表，供同步入口使用。"""
        items: list[tuple[str, Any]] = []
        async for node_name, node_output in self.arun_stream(**kwargs):
            items.append((node_name, node_output))
        return items

    async def aget_state(self, thread_id: str = "default") -> dict[str, Any]:
        """异步获取当前状态。

        Args:
            thread_id: 线程 ID

        Returns:
            dict: 当前状态
        """
        compiled = self._get_compiled_graph()
        try:
            state = await compiled.aget_state(
                {"configurable": {"thread_id": thread_id}}
            )
            return state.values if hasattr(state, "values") else {}
        except Exception as e:
            logger.warning(f"[LangGraphRunner] 获取状态失败: {e}")
            return {}

    def get_state(self, thread_id: str = "default") -> dict[str, Any]:
        """同步获取当前状态。

        如果当前环境已有事件循环，请改用 aget_state。
        """
        return asyncio.run(self.aget_state(thread_id))


# 全局实例
_runner_instance: LangGraphRunner | None = None


def get_runner() -> LangGraphRunner:
    """获取 LangGraphRunner 单例实例。"""
    global _runner_instance
    if _runner_instance is None:
        _runner_instance = LangGraphRunner()
    return _runner_instance
