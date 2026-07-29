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
"""ReAct 子图主循环。

实现受限 ReAct 子图的核心调度循环：
1. 初始化 ReactState + Budget + Policy Guard
2. 循环执行 reason → policy → execute → observe → budget_check
3. 任一终止条件触发即退出循环
4. 产出 ReactExecutionResult（不直接生成最终答案）

设计原则（docs §5）：
- 所有工具调用经过 Policy Guard
- 多维度预算控制
- 每一步可观测、可审计
- 不直接生成最终答案
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from agent.langgraph.evidence.models import Evidence
from agent.langgraph.react.budget import (
    BudgetCheckResult,
    BudgetController,
    build_budget_from_config,
)
from agent.langgraph.react.executor import get_react_executor
from agent.langgraph.react.models import (
    ACTION_TYPE_FINISH,
    DEFAULT_REACT_BUDGET,
    ReactExecutionResult,
    ReactObservation,
    ReactState,
    get_default_budget,
)
from agent.langgraph.react.policy import (
    PolicyContext,
    PolicyDecisionType,
    PolicyGuard,
)
from agent.langgraph.react.step import parse_llm_output

logger = logging.getLogger(__name__)


# LLM 调用的简化接口（不直接依赖现有 LLM 客户端）
LLMCallable = Any  # async def llm_callable(prompt: str) -> str


async def _default_llm_callable(prompt: str) -> str:
    """默认 LLM 调用：用于兜底（无外部注入时降级为简单 finish）。

    生产环境应在 graph node 中通过 TenantLLMService 注入。
    """
    import json

    return json.dumps(
        {
            "thought_summary": "默认 LLM 未注入，直接结束",
            "action": {
                "type": "finish",
                "tool_name": "finish",
                "purpose": "默认 LLM 未注入",
                "arguments": {"summary": "Default LLM not provided"},
            },
            "stop": True,
        },
        ensure_ascii=False,
    )


class ReactSubgraph:
    """受限 ReAct 子图执行器。

    使用方式：
    ```python
        subgraph = ReactSubgraph(agent_config=...)
        result = await subgraph.run(
            user_question="...",
            tenant_id="...",
            user_id="...",
            llm_id="...",
            llm_callable=my_llm_func,  # 注入 LLM
        )
    ```
    """

    def __init__(
        self,
        agent_config: Optional[dict] = None,
        react_config: Optional[dict] = None,
    ):
        """初始化 ReAct 子图。

        Args:
            agent_config: 完整 agent 配置（用于 Policy Guard + 工具权限）
            react_config: react 子配置（覆盖默认预算/白名单）
        """
        self.agent_config = agent_config or {}
        self.react_config = react_config or self.agent_config.get("react", {}) or {}
        self.budget = BudgetController(
            build_budget_from_config(self.react_config)
        )
        self.policy_guard = PolicyGuard(self.agent_config)
        self.executor = get_react_executor()

    async def run(
        self,
        user_question: str,
        tenant_id: str = "",
        user_id: str = "",
        llm_id: str = "",
        kb_ids: Optional[list[str]] = None,
        db_id: str = "",
        mcp_server_name: str = "",
        query_lang: str = "zh_CN",
        llm_callable: Optional[LLMCallable] = None,
        max_latency_ms: Optional[int] = None,
    ) -> ReactExecutionResult:
        """执行 ReAct 子图主循环。

        Args:
            user_question: 用户问题
            tenant_id: 租户 ID
            user_id: 用户 ID
            llm_id: LLM 模型 ID
            kb_ids: 知识库 ID 列表
            db_id: 数据库 ID
            mcp_server_name: MCP 服务名
            query_lang: 查询语言
            llm_callable: LLM 调用函数（async (prompt: str) -> str）
            max_latency_ms: 可选的最大耗时覆盖

        Returns:
            ReactExecutionResult
        """
        if max_latency_ms is not None:
            self.budget.budget["deadline_ts"] = time.time() + max_latency_ms / 1000.0

        # 初始化 ReactState
        allowed_tools = self.react_config.get(
            "allowed_tools",
            ["rag_search", "db_query", "web_search"],
        )

        state: ReactState = {
            "trace_id": f"react_{int(time.time() * 1000)}",
            "user_question": user_question,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "query_lang": query_lang,
            "budget": self.budget.snapshot(),
            "allowed_tools": allowed_tools,
            "action_history": [],
            "observations": [],
            "failures": [],
            "evidence": [],
        }

        llm_fn = llm_callable or _default_llm_callable
        finish_reason = "completed"

        # ========== 主循环 ==========
        while True:
            # 1. 预算检查
            check = self.budget.can_proceed()
            if not check.allowed:
                finish_reason = "budget_exhausted"
                logger.info(f"[react_subgraph] 预算超限终止: {check.reason}")
                break

            # 2. LLM 推理
            self.budget.increment_llm_call()
            try:
                action = await _reason_step(user_question, state, llm_fn)
            except Exception as e:
                logger.error(f"[react_subgraph] LLM 推理失败: {e}")
                state["failures"].append({"step": "reason", "error": str(e)})
                finish_reason = "error"
                break

            action_type = action.get("type", "")
            thought_summary = action.get("thought_summary", "")

            # 3. 终态判定
            if action_type == ACTION_TYPE_FINISH or action.get("stop"):
                finish_reason = "completed"
                logger.info(
                    f"[react_subgraph] LLM 主动 finish: {thought_summary[:100]}"
                )
                break

            if action_type == "ask_clarification":
                state["needs_clarification"] = True
                state["clarification_question"] = (action.get("arguments") or {}).get(
                    "question", ""
                )
                finish_reason = "needs_clarification"
                logger.info(
                    f"[react_subgraph] LLM 请求澄清: {state['clarification_question'][:100]}"
                )
                break

            # 4. Policy Guard
            policy_ctx = PolicyContext(
                tenant_id=tenant_id,
                user_id=user_id,
                tool_name=action_type,
                action_type=action_type,
                purpose=action.get("purpose", ""),
                arguments=action.get("arguments", {}),
                allowed_tools=allowed_tools,
                agent_config=self.agent_config,
            )
            policy_result = self.policy_guard.evaluate(policy_ctx)

            if policy_result.decision == PolicyDecisionType.DENY:
                # 记录拒绝原因
                state["failures"].append(
                    {
                        "step": "policy",
                        "action_type": action_type,
                        "reason": policy_result.reason,
                    }
                )
                # 把失败写入 action history，方便下一步 LLM 调整
                state["action_history"].append(
                    {
                        "action_type": action_type,
                        "thought_summary": thought_summary,
                        "result_summary": f"[POLICY DENIED] {policy_result.reason}",
                    }
                )
                self.budget.increment_step()
                # 连续拒绝超过 3 次也终止
                denied_count = sum(
                    1
                    for f in state["failures"][-5:]
                    if f.get("step") == "policy"
                )
                if denied_count >= 3:
                    finish_reason = "policy_denied"
                    logger.warning(
                        f"[react_subgraph] 连续 {denied_count} 次 policy 拒绝，终止"
                    )
                    break
                continue

            if policy_result.decision == PolicyDecisionType.NEEDS_APPROVAL:
                # 当前不支持人工审批 inline，降级为拒绝
                state["failures"].append(
                    {
                        "step": "policy",
                        "action_type": action_type,
                        "reason": f"NEEDS_APPROVAL: {policy_result.reason}",
                    }
                )
                state["action_history"].append(
                    {
                        "action_type": action_type,
                        "thought_summary": thought_summary,
                        "result_summary": f"[NEEDS_APPROVAL] {policy_result.reason}",
                    }
                )
                self.budget.increment_step()
                continue

            # 使用 sanitized_arguments（可能为原 arguments）
            action["arguments"] = policy_result.sanitized_arguments or action.get(
                "arguments", {}
            )

            # 5. 工具执行
            self.budget.increment_tool_call(action_type)
            observation, evidences = await self.executor.execute(
                action,
                tenant_id=tenant_id,
                user_id=user_id,
                llm_id=llm_id,
                kb_ids=kb_ids,
                db_id=db_id,
                mcp_server_name=mcp_server_name,
                query_lang=query_lang,
            )

            state["observations"].append(observation)
            # 累积 evidence（去重）
            for ev in evidences:
                if not any(
                    e.get("evidence_id") == ev.get("evidence_id")
                    for e in state["evidence"]
                ):
                    state["evidence"].append(ev)

            # 写入 action history
            result_summary = (
                observation.get("content", "")[:300]
                if observation.get("success")
                else f"[ERROR] {observation.get('error', '')}"
            )
            state["action_history"].append(
                {
                    "action_type": action_type,
                    "thought_summary": thought_summary,
                    "purpose": action.get("purpose", ""),
                    "arguments": action.get("arguments", {}),
                    "result_summary": result_summary,
                    "latency_ms": observation.get("latency_ms", 0),
                    "evidence_count": len(evidences),
                }
            )

            # 记录失败
            if not observation.get("success"):
                state["failures"].append(
                    {
                        "step": "tool",
                        "action_type": action_type,
                        "error": observation.get("error", ""),
                    }
                )

            self.budget.increment_step()

        # ========== 构造结果 ==========
        return ReactExecutionResult(
            success=finish_reason == "completed" and bool(state["evidence"]),
            evidence=list(state["evidence"]),
            step_count=int(self.budget.snapshot().get("step_count", 0)),
            finish_reason=finish_reason,
            summary=_build_summary(user_question, state, finish_reason),
        )


async def _reason_step(
    goal: str,
    state: ReactState,
    llm_fn: LLMCallable,
):
    """执行一次 ReAct 推理（带 LLM 调用计数）。"""
    from agent.langgraph.react.step import reason_step as _reason

    return await _reason(goal, state, llm_fn)


def _build_summary(user_question: str, state: ReactState, finish_reason: str) -> str:
    """构造 ReAct 子图执行摘要。"""
    evidence_count = len(state.get("evidence", []))
    action_count = len(state.get("action_history", []))
    return (
        f"问题: {user_question[:80]}\n"
        f"完成步数: {action_count}\n"
        f"收集证据: {evidence_count}\n"
        f"终止原因: {finish_reason}"
    )


# 模块级便捷入口
_default_subgraph: Optional[ReactSubgraph] = None


def get_react_subgraph(agent_config: Optional[dict] = None) -> ReactSubgraph:
    """获取 ReAct 子图实例（懒加载单例）。

    Args:
        agent_config: agent 配置（首次调用时生效）
    """
    global _default_subgraph
    if _default_subgraph is None or agent_config is not None:
        _default_subgraph = ReactSubgraph(agent_config=agent_config)
    return _default_subgraph
