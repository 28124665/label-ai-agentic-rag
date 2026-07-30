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
"""SQLAgentSubgraph — 有界 ReAct 循环（LangGraph StateGraph）。

流程（docs/数据库Tool渐进式披露LLM化落地设计.md §3.3）：
    budget_guard → agent_llm → (有工具调用) → tool_exec → budget_guard（循环）
                      └──── (finish / give_up / 预算耗尽) → finalize → END

设计原则：
- LLM 每步输出 JSON action（与 react/step.py 同构协议）
- budget_guard 每一步前置检查，任一预算耗尽强制 finalize
- tool_exec 分发到 SqlAgentToolset，工具错误作为 Observation 回传（自愈输入）
- 全程记录 step_trace，可审计、可透出到 evidence provenance
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from langgraph.graph import END, StateGraph

from agent.langgraph.tools.sql_agent.budget import ExplorationBudget
from agent.langgraph.tools.sql_agent.prompts import (
    build_step_prompt,
    build_system_prompt,
    parse_agent_action,
)
from agent.langgraph.tools.sql_agent.state import (
    ACTION_DESCRIBE_TABLE,
    ACTION_EXECUTE_SQL,
    ACTION_FINISH,
    ACTION_GIVE_UP,
    ACTION_LIST_DATABASES,
    ACTION_LIST_TABLES,
    VERDICT_BUDGET_EXHAUSTED,
    VERDICT_DATA_FOUND,
    VERDICT_EXPLORATION_FAILED,
    VERDICT_NO_DATA_CONFIRMED,
    SqlAgentState,
)
from agent.langgraph.tools.sql_agent.tools import SqlAgentToolset

logger = logging.getLogger(__name__)

# LLM 调用接口（与 react/graph.py 同构）：async (prompt: str) -> str
LLMCallable = Callable[[str], Awaitable[str]]

# LLM 输出解析失败时，给 Agent 的容错机会次数
MAX_PARSE_RETRIES = 2


class SqlAgentSubgraph:
    """SQL Agent 有界 ReAct 子图。

    使用方式：
    ```python
        subgraph = SqlAgentSubgraph(toolset, budget, llm_callable)
        final_state = await subgraph.run(
            query="...", query_lang="zh_CN", db_id="", exploration_hints={}
        )
        verdict = final_state["exploration_verdict"]
    ```
    """

    def __init__(
        self,
        toolset: SqlAgentToolset,
        budget: ExplorationBudget,
        llm_callable: LLMCallable,
        system_prompt: Optional[str] = None,
    ):
        """初始化。

        Args:
            toolset: 细粒度工具集（含护栏）
            budget: 探索预算
            llm_callable: LLM 调用函数（async (prompt) -> str）
            system_prompt: 可选的自定义 System Prompt（默认由 build_system_prompt 生成）
        """
        self._toolset = toolset
        self._budget = budget
        self._llm_callable = llm_callable
        self._custom_system_prompt = system_prompt

    async def run(
        self,
        query: str,
        query_lang: str = "zh_CN",
        db_id: str = "",
        exploration_hints: Optional[dict[str, Any]] = None,
    ) -> SqlAgentState:
        """执行 SQL Agent 子图，返回终态。"""
        system_prompt = self._custom_system_prompt or build_system_prompt(
            query=query,
            query_lang=query_lang,
            db_id=db_id,
            exploration_hints=exploration_hints,
        )

        initial_state: SqlAgentState = {
            "query": query,
            "query_lang": query_lang,
            "db_id": db_id,
            "exploration_hints": exploration_hints or {},
            "step_trace": [],
            "observations": [],
            "step_count": 0,
            "llm_call_count": 0,
            "sql_exec_count": 0,
            "describe_count": 0,
            "budget_exhausted": False,
            "budget_stop_reason": "",
            "pending_action": {},
            "finish_summary": "",
            "give_up_reason": "",
            "give_up_type": "error",
            "llm_error": "",
            "final_db_id": db_id,
            "final_sql": "",
            "final_rows": [],
            "final_tables": [],
            "sql_succeeded": False,
            "exploration_verdict": "",
            "_system_prompt": system_prompt,
            "_parse_failures": 0,
        }

        graph = self._build_graph()
        app = graph.compile()
        final_state = await app.ainvoke(initial_state)
        return final_state

    # ========== 图构建 ==========
    def _build_graph(self) -> StateGraph:
        """构建子图：budget_guard → agent_llm ⇄ tool_exec → finalize。"""
        graph = StateGraph(SqlAgentState)

        graph.add_node("budget_guard", self._budget_guard_node)
        graph.add_node("agent_llm", self._agent_llm_node)
        graph.add_node("tool_exec", self._tool_exec_node)
        graph.add_node("finalize", self._finalize_node)

        graph.set_entry_point("budget_guard")

        graph.add_conditional_edges(
            "budget_guard",
            lambda state: "finalize" if state.get("budget_exhausted") else "agent_llm",
            {"finalize": "finalize", "agent_llm": "agent_llm"},
        )
        graph.add_conditional_edges(
            "agent_llm",
            self._route_after_llm,
            {"tool_exec": "tool_exec", "finalize": "finalize", "budget_guard": "budget_guard"},
        )
        graph.add_edge("tool_exec", "budget_guard")
        graph.add_edge("finalize", END)

        return graph

    # ========== 节点实现 ==========
    async def _budget_guard_node(self, state: SqlAgentState) -> dict[str, Any]:
        """预算护栏：每一步前置检查，超限强制收敛。"""
        allowed, reason = self._budget.check(
            step_count=state.get("step_count", 0),
            llm_call_count=state.get("llm_call_count", 0),
            sql_exec_count=state.get("sql_exec_count", 0),
            describe_count=state.get("describe_count", 0),
        )
        if not allowed:
            logger.warning(f"[sql_agent] 预算耗尽，强制收敛: {reason}")
            return {"budget_exhausted": True, "budget_stop_reason": reason}
        return {}

    async def _agent_llm_node(self, state: SqlAgentState) -> dict[str, Any]:
        """Agent 推理：构建 Prompt → 调 LLM → 解析 JSON action。"""
        prompt = build_step_prompt(
            system_prompt=state.get("_system_prompt", ""),
            query=state.get("query", ""),
            step_trace=state.get("step_trace", []),
        )

        llm_call_count = state.get("llm_call_count", 0) + 1
        try:
            llm_output = await self._llm_callable(prompt)
        except Exception as e:
            logger.error(f"[sql_agent] LLM 调用失败: {e}")
            return {"llm_error": str(e), "llm_call_count": llm_call_count}

        action = parse_agent_action(llm_output)
        if action is None:
            parse_failures = state.get("_parse_failures", 0) + 1
            logger.warning(
                f"[sql_agent] LLM 输出解析失败 ({parse_failures}/{MAX_PARSE_RETRIES}): "
                f"{llm_output[:200]}"
            )
            if parse_failures >= MAX_PARSE_RETRIES:
                return {
                    "llm_error": f"LLM 输出连续 {parse_failures} 次无法解析为 JSON action",
                    "llm_call_count": llm_call_count,
                    "_parse_failures": parse_failures,
                }
            # 给 Agent 一次纠错机会：把解析失败作为观察回传
            observation = {
                "type": "parse_error",
                "content": "你的输出不是合法 JSON action，请严格按格式重新输出。",
            }
            return {
                "llm_call_count": llm_call_count,
                "_parse_failures": parse_failures,
                "observations": [observation],
                "step_trace": [
                    {
                        "step": state.get("step_count", 0) + 1,
                        "thought": "",
                        "action": "parse_error",
                        "args": {},
                        "observation": observation["content"],
                        "latency_ms": 0,
                    }
                ],
                "pending_action": {},
            }

        logger.info(
            f"[sql_agent] step={state.get('step_count', 0) + 1} "
            f"action={action['action']} thought={action['thought'][:80]}"
        )
        return {"pending_action": action, "llm_call_count": llm_call_count}

    def _route_after_llm(self, state: SqlAgentState) -> str:
        """agent_llm 后的路由：工具调用 → tool_exec；终态动作 → finalize；解析重试 → budget_guard。"""
        if state.get("llm_error"):
            return "finalize"

        action = state.get("pending_action", {})
        action_type = action.get("action", "")

        if action_type in (ACTION_FINISH, ACTION_GIVE_UP):
            return "finalize"
        if not action_type:
            # 解析失败的容错步：回到 budget_guard 重新推理
            return "budget_guard"
        return "tool_exec"

    async def _tool_exec_node(self, state: SqlAgentState) -> dict[str, Any]:
        """工具执行：分发到 SqlAgentToolset，结果作为 Observation 回传。"""
        action = state.get("pending_action", {})
        action_type = action.get("action", "")
        args = action.get("args", {})
        step = state.get("step_count", 0) + 1
        start_time = time.time()

        updates: dict[str, Any] = {"step_count": step}

        observation_text, structured, success = await self._dispatch(
            action_type, args, state, updates
        )
        latency_ms = int((time.time() - start_time) * 1000)

        # 记录轨迹
        trace_entry = {
            "step": step,
            "thought": action.get("thought", ""),
            "action": action_type,
            "args": _summarize_args(args),
            "observation": observation_text[:500],
            "success": success,
            "latency_ms": latency_ms,
        }

        observation = {
            "type": action_type,
            "content": observation_text,
            "success": success,
            "latency_ms": latency_ms,
        }

        updates["step_trace"] = [trace_entry]
        updates["observations"] = [observation]

        # 结果状态更新
        if action_type == ACTION_EXECUTE_SQL and success:
            updates["sql_succeeded"] = True
            updates["final_sql"] = structured.get("executed_sql", args.get("sql", ""))
            updates["final_rows"] = structured.get("rows", [])
            updates["final_db_id"] = args.get("db_id", state.get("final_db_id", ""))
        elif action_type == ACTION_DESCRIBE_TABLE and not structured.get("cache_hit", True):
            updates["describe_count"] = state.get("describe_count", 0) + 1

        return updates

    async def _dispatch(
        self,
        action_type: str,
        args: dict[str, Any],
        state: SqlAgentState,
        updates: dict[str, Any],
    ) -> tuple[str, dict[str, Any], bool]:
        """分发动作到工具集，并更新预算计数。"""
        toolset = self._toolset

        if action_type == ACTION_LIST_DATABASES:
            return await toolset.invoke_list_databases()

        if action_type == ACTION_LIST_TABLES:
            db_id = args.get("db_id") or state.get("db_id", "")
            return await toolset.invoke_list_tables(db_id)

        if action_type == ACTION_DESCRIBE_TABLE:
            db_id = args.get("db_id") or state.get("db_id", "")
            table_name = args.get("table_name", "")
            if not table_name:
                return "[ERROR] describe_table 缺少 table_name 参数", {}, False
            return await toolset.invoke_describe_table(db_id, table_name)

        if action_type == ACTION_EXECUTE_SQL:
            db_id = args.get("db_id") or state.get("db_id", "")
            sql = args.get("sql", "")
            if not sql:
                return "[ERROR] execute_sql 缺少 sql 参数", {}, False
            updates["sql_exec_count"] = state.get("sql_exec_count", 0) + 1
            return await toolset.invoke_execute_sql(db_id, sql)

        return f"[ERROR] 未知动作: {action_type}", {}, False

    async def _finalize_node(self, state: SqlAgentState) -> dict[str, Any]:
        """收敛：计算 exploration_verdict 与终态摘要。"""
        updates: dict[str, Any] = {}
        action = state.get("pending_action", {})
        action_type = action.get("action", "")
        args = action.get("args", {})

        # 处理终态动作（finish / give_up 在路由前未记录，这里补录轨迹）
        if action_type == ACTION_FINISH:
            updates["finish_summary"] = str(args.get("summary", ""))
        elif action_type == ACTION_GIVE_UP:
            updates["give_up_reason"] = str(args.get("reason", ""))
            give_up_type = args.get("reason_type", "error")
            updates["give_up_type"] = give_up_type if give_up_type in ("no_data", "error") else "error"

        verdict = self._compute_verdict(state, updates)
        updates["exploration_verdict"] = verdict

        logger.info(
            f"[sql_agent] 收敛: verdict={verdict}, steps={state.get('step_count', 0)}, "
            f"llm_calls={state.get('llm_call_count', 0)}, "
            f"sql_execs={state.get('sql_exec_count', 0)}"
        )
        return updates

    def _compute_verdict(
        self, state: SqlAgentState, finalize_updates: dict[str, Any]
    ) -> str:
        """计算探索终态（优先级：预算 > LLM 错误 > give_up > finish > 兜底）。"""
        if state.get("budget_exhausted"):
            return VERDICT_BUDGET_EXHAUSTED

        if state.get("llm_error"):
            return VERDICT_EXPLORATION_FAILED

        give_up_type = finalize_updates.get("give_up_type") or state.get("give_up_type")
        give_up_reason = finalize_updates.get("give_up_reason") or state.get("give_up_reason")
        if give_up_reason:
            if give_up_type == "no_data":
                return VERDICT_NO_DATA_CONFIRMED
            return VERDICT_EXPLORATION_FAILED

        # finish 或未显式结束：依据是否拿到数据
        if state.get("final_rows"):
            return VERDICT_DATA_FOUND
        if state.get("sql_succeeded"):
            # SQL 执行成功但结果为空，Agent 判断后结束 → 确认无数据
            return VERDICT_NO_DATA_CONFIRMED
        return VERDICT_EXPLORATION_FAILED


def _summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    """摘要动作参数（SQL 截断，防轨迹爆炸）。"""
    summarized = dict(args)
    if "sql" in summarized and len(str(summarized["sql"])) > 300:
        summarized["sql"] = str(summarized["sql"])[:300] + "…"
    return summarized
