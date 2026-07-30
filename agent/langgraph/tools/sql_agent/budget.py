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
"""SQL Agent 探索预算（硬约束）。

预算项与默认值（docs/数据库Tool渐进式披露LLM化落地设计.md §3.3）：
- max_agent_steps=10 / max_llm_calls=10 / max_sql_executions=4
- max_describe_calls=6 / max_wall_time_sec=90 / max_result_rows=500
任一预算耗尽，budget_guard 强制子图收敛到 finalize（verdict=budget_exhausted）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExplorationBudget:
    """单次 SQL Agent 运行的预算约束与计数。"""

    max_agent_steps: int = 10
    max_llm_calls: int = 10
    max_sql_executions: int = 4
    max_describe_calls: int = 6
    max_wall_time_sec: int = 90
    max_result_rows: int = 500
    start_ts: float = field(default_factory=time.time)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "ExplorationBudget":
        """从配置字典构造（缺失项使用默认值）。"""
        config = config or {}
        budget_section = config.get("budget", config)
        kwargs = {}
        for field_name in (
            "max_agent_steps",
            "max_llm_calls",
            "max_sql_executions",
            "max_describe_calls",
            "max_wall_time_sec",
            "max_result_rows",
        ):
            if field_name in budget_section:
                kwargs[field_name] = budget_section[field_name]
        return cls(**kwargs)

    def elapsed_sec(self) -> float:
        """已消耗墙钟时间（秒）。"""
        return time.time() - self.start_ts

    def check(
        self,
        step_count: int,
        llm_call_count: int,
        sql_exec_count: int,
        describe_count: int,
    ) -> tuple[bool, str]:
        """检查是否可继续探索。

        Returns:
            tuple: (allowed, reason)。allowed=False 时 reason 为耗尽项说明。
        """
        if step_count >= self.max_agent_steps:
            return False, f"agent 步数达到上限 {self.max_agent_steps}"
        if llm_call_count >= self.max_llm_calls:
            return False, f"LLM 调用次数达到上限 {self.max_llm_calls}"
        if sql_exec_count >= self.max_sql_executions:
            return False, f"SQL 执行次数达到上限 {self.max_sql_executions}"
        if describe_count >= self.max_describe_calls:
            return False, f"describe 调用次数达到上限 {self.max_describe_calls}"
        if self.elapsed_sec() >= self.max_wall_time_sec:
            return False, f"墙钟时间超过上限 {self.max_wall_time_sec}s"
        return True, ""

    def snapshot(
        self,
        step_count: int,
        llm_call_count: int,
        sql_exec_count: int,
        describe_count: int,
    ) -> dict[str, Any]:
        """预算使用快照（透出到 exploration_stats）。"""
        return {
            "agent_steps": step_count,
            "llm_calls": llm_call_count,
            "sql_execs": sql_exec_count,
            "describe_calls": describe_count,
            "wall_time_ms": int(self.elapsed_sec() * 1000),
            "limits": {
                "max_agent_steps": self.max_agent_steps,
                "max_llm_calls": self.max_llm_calls,
                "max_sql_executions": self.max_sql_executions,
                "max_describe_calls": self.max_describe_calls,
                "max_wall_time_sec": self.max_wall_time_sec,
                "max_result_rows": self.max_result_rows,
            },
        }
