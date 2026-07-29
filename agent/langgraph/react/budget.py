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
"""Budget Controller — ReAct 子图多维度预算控制。

ReAct 不允许无限自由推理和调用工具，必须在多维度预算内执行：
- 最大步骤数（max_steps）
- 最大工具调用次数（max_tool_calls）
- 最大 DB 查询次数（max_db_queries）
- 最大 LLM 调用次数（max_llm_calls）
- 最大 token 消耗（token_budget）
- 最大耗时（deadline_ts / max_latency_ms）

任一维度超限立即终止，避免成本失控和延迟升高。

参考：docs/受限ReAct子图落地设计.md §5.6（终止条件）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agent.langgraph.react.models import ReactBudget, get_default_budget

logger = logging.getLogger(__name__)


class BudgetViolationType(str, Enum):
    """预算超限类型。"""

    STEPS_EXCEEDED = "steps_exceeded"
    TOOL_CALLS_EXCEEDED = "tool_calls_exceeded"
    DB_QUERIES_EXCEEDED = "db_queries_exceeded"
    LLM_CALLS_EXCEEDED = "llm_calls_exceeded"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    DEADLINE_EXCEEDED = "deadline_exceeded"


@dataclass
class BudgetCheckResult:
    """预算检查结果。"""

    allowed: bool
    violation_type: Optional[BudgetViolationType] = None
    reason: str = ""


class BudgetController:
    """Budget Controller — 控制 ReAct 子图的多维度预算。

    使用方式：
    1. 构造时传入初始 ReactBudget（或使用默认值）
    2. 每次 step / tool_call / db_query / llm_call 之前调用 can_proceed()
    3. 执行后调用相应 increment() 方法更新计数
    4. can_proceed() 返回 False 时，子图必须立即终止

    线程/协程安全：本类为可变对象，假设单次 ReAct 执行内单线程使用。
    """

    def __init__(self, budget: Optional[ReactBudget] = None):
        """初始化 Budget Controller。

        Args:
            budget: 初始预算（None 时使用默认配置）
        """
        self.budget: ReactBudget = budget or get_default_budget()

    # ========== 预算检查 ==========
    def can_proceed(self) -> BudgetCheckResult:
        """检查是否还有预算可继续执行。

        Returns:
            BudgetCheckResult: allowed=True 继续；False 立即终止（含 violation_type）
        """
        if self.budget.get("step_count", 0) >= self.budget.get("max_steps", 8):
            return BudgetCheckResult(
                allowed=False,
                violation_type=BudgetViolationType.STEPS_EXCEEDED,
                reason=f"step_count={self.budget.get('step_count')} >= max_steps={self.budget.get('max_steps')}",
            )

        if self.budget.get("tool_call_count", 0) >= self.budget.get("max_tool_calls", 6):
            return BudgetCheckResult(
                allowed=False,
                violation_type=BudgetViolationType.TOOL_CALLS_EXCEEDED,
                reason=f"tool_call_count={self.budget.get('tool_call_count')} >= max_tool_calls={self.budget.get('max_tool_calls')}",
            )

        if self.budget.get("db_query_count", 0) >= self.budget.get("max_db_queries", 3):
            return BudgetCheckResult(
                allowed=False,
                violation_type=BudgetViolationType.DB_QUERIES_EXCEEDED,
                reason=f"db_query_count={self.budget.get('db_query_count')} >= max_db_queries={self.budget.get('max_db_queries')}",
            )

        if self.budget.get("llm_call_count", 0) >= self.budget.get("max_llm_calls", 5):
            return BudgetCheckResult(
                allowed=False,
                violation_type=BudgetViolationType.LLM_CALLS_EXCEEDED,
                reason=f"llm_call_count={self.budget.get('llm_call_count')} >= max_llm_calls={self.budget.get('max_llm_calls')}",
            )

        deadline = self.budget.get("deadline_ts", 0.0)
        if deadline and time.time() > deadline:
            return BudgetCheckResult(
                allowed=False,
                violation_type=BudgetViolationType.DEADLINE_EXCEEDED,
                reason=f"deadline_ts={deadline} 已过当前时间={time.time():.2f}",
            )

        token_used = self.budget.get("token_used", 0)
        token_budget = self.budget.get("token_budget", 0)
        if token_budget and token_used >= token_budget:
            return BudgetCheckResult(
                allowed=False,
                violation_type=BudgetViolationType.TOKEN_BUDGET_EXCEEDED,
                reason=f"token_used={token_used} >= token_budget={token_budget}",
            )

        return BudgetCheckResult(allowed=True)

    def can_db_query(self) -> BudgetCheckResult:
        """检查是否可以执行 DB 查询（除通用检查外，还需 db_query_count 限制）。"""
        general = self.can_proceed()
        if not general.allowed:
            return general

        if self.budget.get("db_query_count", 0) >= self.budget.get("max_db_queries", 3):
            return BudgetCheckResult(
                allowed=False,
                violation_type=BudgetViolationType.DB_QUERIES_EXCEEDED,
                reason=f"db_query_count={self.budget.get('db_query_count')} >= max_db_queries={self.budget.get('max_db_queries')}",
            )
        return BudgetCheckResult(allowed=True)

    # ========== 计数递增 ==========
    def increment_step(self) -> None:
        """递增 step 计数。每次 reason → action → observation 循环完成时调用。"""
        self.budget["step_count"] = self.budget.get("step_count", 0) + 1

    def increment_tool_call(self, tool_type: str = "") -> None:
        """递增工具调用计数。DB 查询时同时递增 db_query_count。"""
        self.budget["tool_call_count"] = self.budget.get("tool_call_count", 0) + 1
        if tool_type == "db_query":
            self.budget["db_query_count"] = self.budget.get("db_query_count", 0) + 1

    def increment_llm_call(self, tokens_used: int = 0) -> None:
        """递增 LLM 调用计数，并累加 token 消耗。"""
        self.budget["llm_call_count"] = self.budget.get("llm_call_count", 0) + 1
        if tokens_used > 0:
            self.budget["token_used"] = self.budget.get("token_used", 0) + tokens_used

    # ========== 状态查询 ==========
    def snapshot(self) -> dict:
        """返回当前预算快照（用于日志/可观测性）。"""
        return dict(self.budget)

    def remaining_steps(self) -> int:
        """返回剩余可用步数。"""
        return max(0, self.budget.get("max_steps", 8) - self.budget.get("step_count", 0))

    def remaining_tool_calls(self) -> int:
        """返回剩余可用工具调用次数。"""
        return max(0, self.budget.get("max_tool_calls", 6) - self.budget.get("tool_call_count", 0))

    def remaining_db_queries(self) -> int:
        """返回剩余可用 DB 查询次数。"""
        return max(0, self.budget.get("max_db_queries", 3) - self.budget.get("db_query_count", 0))


def build_budget_from_config(react_config: Optional[dict], max_latency_ms: Optional[int] = None) -> ReactBudget:
    """从 agent_config.react 配置构造 ReactBudget。

    Args:
        react_config: agent_config 中的 react 配置子节
        max_latency_ms: 可选的最大耗时覆盖（None 时使用配置或默认 30000ms）

    Returns:
        ReactBudget: 初始化的预算
    """
    cfg = react_config or {}
    max_latency = max_latency_ms or cfg.get("max_latency_ms", 30000)
    budget = get_default_budget(deadline_offset_ms=max_latency)

    # 覆盖用户配置（仅覆盖提供的字段）
    if "max_steps" in cfg:
        budget["max_steps"] = int(cfg["max_steps"])
    if "max_tool_calls" in cfg:
        budget["max_tool_calls"] = int(cfg["max_tool_calls"])
    if "max_db_queries" in cfg:
        budget["max_db_queries"] = int(cfg["max_db_queries"])
    if "max_llm_calls" in cfg:
        budget["max_llm_calls"] = int(cfg["max_llm_calls"])
    if "token_budget" in cfg:
        budget["token_budget"] = int(cfg["token_budget"])

    return budget
