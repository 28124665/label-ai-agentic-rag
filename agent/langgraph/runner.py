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
        conversation_history: Optional[list[dict]] = None,
        conversation_summary: str = "",
        agent_config: Optional[dict] = None,
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
            "conversation_history": conversation_history or [],
            "conversation_summary": conversation_summary,
            "agent_config": agent_config or {},
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
        conversation_history: Optional[list[dict]] = None,
        conversation_summary: str = "",
        agent_config: Optional[dict] = None,
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
            conversation_history: 对话历史（供 prompt_assembly 注入上下文）
            conversation_summary: 对话历史摘要（超出 Token 窗口历史的 LLM 压缩摘要）
            agent_config: Agent 配置（工具参数等）
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
            conversation_history=conversation_history,
            conversation_summary=conversation_summary,
            agent_config=agent_config,
            **kwargs,
        )

        logger.info(f"[LangGraphRunner] 开始执行: question='{user_question}'")

        try:
            final_state = await compiled.ainvoke(initial_state)
        except Exception as e:
            # P1-3: 异常上报到 Sentry(带 trace_id 关联)
            try:
                from api.utils.error_reporter import capture_exception
                capture_exception(
                    e,
                    agent_route_target=initial_state.get("route_target", ""),
                    agent_trace_id=initial_state.get("trace_id", ""),
                )
            except Exception:
                pass
            raise

        logger.info(f"[LangGraphRunner] 执行完成: answer_len={len(final_state.get('final_answer', ''))}")

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
        conversation_history: Optional[list[dict]] = None,
        conversation_summary: str = "",
        agent_config: Optional[dict] = None,
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
                conversation_history=conversation_history,
                conversation_summary=conversation_summary,
                agent_config=agent_config,
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
        conversation_history: Optional[list[dict]] = None,
        conversation_summary: str = "",
        agent_config: Optional[dict] = None,
        **kwargs,
    ):
        """异步流式执行 LangGraph 工作流。

        逐节点产出 (node_name, node_output) 元组，调用方可以基于节点
        名称将输出翻译为 SSE 事件或其它协议。

        Args:
            user_question: 用户问题
            query_lang: 查询语言
            tenant_id: 租户 ID
            llm_id: LLM 模型 ID
            kb_ids: 知识库 ID 列表
            db_id: 数据库 ID
            mcp_server_name: MCP Server 名称
            conversation_history: 对话历史（供 prompt_assembly 注入上下文）
            conversation_summary: 对话历史摘要（超出 Token 窗口历史的 LLM 压缩摘要）
            agent_config: Agent 配置（工具参数等）
            **kwargs: 其他参数

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
            conversation_history=conversation_history,
            conversation_summary=conversation_summary,
            agent_config=agent_config,
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
        conversation_history: Optional[list[dict]] = None,
        conversation_summary: str = "",
        agent_config: Optional[dict] = None,
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
                conversation_history=conversation_history,
                conversation_summary=conversation_summary,
                agent_config=agent_config,
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
            state = await compiled.aget_state({"configurable": {"thread_id": thread_id}})
            return state.values if hasattr(state, "values") else {}
        except Exception as e:
            logger.warning(f"[LangGraphRunner] 获取状态失败: {e}")
            return {}

    def get_state(self, thread_id: str = "default") -> dict[str, Any]:
        """同步获取当前状态。

        如果当前环境已有事件循环，请改用 aget_state。
        """
        return asyncio.run(self.aget_state(thread_id))

    def _get_compiled_graph_with_checkpointer(self, checkpointer):
        """获取带 checkpointer 的编译图（用于澄清中断/恢复）。

        每次创建新的编译图实例，因为 checkpointer 需要绑定到特定 thread_id。

        Args:
            checkpointer: LangGraph checkpointer 实例（如 MemorySaver）

        Returns:
            CompiledGraph: 带 checkpointer 的编译图
        """
        from agent.langgraph.graph import build_agent_graph

        graph = build_agent_graph()
        compiled = graph.compile(checkpointer=checkpointer)
        return compiled

    async def arun_with_checkpointer(
        self,
        user_question: str,
        thread_id: str = "",
        checkpointer=None,
        **kwargs,
    ) -> dict[str, Any]:
        """支持 checkpointer 的异步执行（用于澄清中断/恢复）。

        使用 checkpointer 持久化图状态，当 clarification_node 触发 interrupt 时，
        可以通过 aresume 方法恢复执行。

        Args:
            user_question: 用户问题
            thread_id: 线程 ID（用于状态恢复），为空则自动生成
            checkpointer: LangGraph checkpointer 实例，为 None 则使用 MemorySaver
            **kwargs: 其他参数（同 arun）

        Returns:
            dict: 包含 thread_id 和 final_state 的结果
        """
        import uuid

        if checkpointer is None:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()

        thread_id = thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}

        compiled = self._get_compiled_graph_with_checkpointer(checkpointer)
        initial_state = self._build_initial_state(user_question, **kwargs)

        logger.info(
            f"[LangGraphRunner] 开始带 checkpointer 执行: "
            f"question='{user_question}', thread_id={thread_id}"
        )

        try:
            final_state = await compiled.ainvoke(initial_state, config=config)
        except Exception as e:
            # P1-3: 异常上报到 Sentry(带 trace_id 关联)
            try:
                from api.utils.error_reporter import capture_exception
                capture_exception(
                    e,
                    agent_route_target=initial_state.get("route_target", ""),
                    agent_trace_id=initial_state.get("trace_id", ""),
                    agent_thread_id=thread_id,
                )
            except Exception:
                pass
            raise

        logger.info(f"[LangGraphRunner] 执行完成: thread_id={thread_id}")

        return {"thread_id": thread_id, "final_state": final_state}

    async def aresume(self, thread_id: str, user_answer: str, checkpointer=None):
        """恢复中断的图执行（用户提交澄清回答后调用）。

        Args:
            thread_id: 中断时的线程 ID
            user_answer: 用户对澄清问题的回答
            checkpointer: 与 arun_with_checkpointer 相同的 checkpointer 实例

        Yields:
            tuple: (node_name, node_output) 元组
        """
        from langgraph.types import Command

        if checkpointer is None:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()

        config = {"configurable": {"thread_id": thread_id}}
        compiled = self._get_compiled_graph_with_checkpointer(checkpointer)

        logger.info(
            f"[LangGraphRunner] 恢复执行: thread_id={thread_id}, answer='{user_answer}'"
        )

        async for output in compiled.astream(Command(resume=user_answer), config=config):
            for node_name, node_output in output.items():
                logger.info(f"[LangGraphRunner] 节点完成: {node_name}")
                yield node_name, node_output

    def run_with_checkpointer(
        self,
        user_question: str,
        thread_id: str = "",
        checkpointer=None,
        **kwargs,
    ) -> dict[str, Any]:
        """同步版本的带 checkpointer 执行。

        通过 asyncio.run 在内部创建事件循环。
        """
        return asyncio.run(
            self.arun_with_checkpointer(
                user_question=user_question,
                thread_id=thread_id,
                checkpointer=checkpointer,
                **kwargs,
            )
        )


# 全局实例
_runner_instance: LangGraphRunner | None = None


def get_runner() -> LangGraphRunner:
    """获取 LangGraphRunner 单例实例。"""
    global _runner_instance
    if _runner_instance is None:
        _runner_instance = LangGraphRunner()
    return _runner_instance
