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
"""Budget Controller 单元测试。

覆盖：
- can_proceed 在 step / tool / db / llm 各种超限情况下的行为
- can_db_query 额外检查 db_query_count
- 计数递增正确性
- build_budget_from_config 配置覆盖逻辑
"""
import time

import pytest

from agent.langgraph.react.budget import (
    BudgetController,
    BudgetViolationType,
    build_budget_from_config,
)
from agent.langgraph.react.models import get_default_budget


class TestBudgetControllerBasics:
    """Budget Controller 基础行为测试。"""

    def test_initial_state_allows_proceed(self):
        """初始状态下应允许继续。"""
        bc = BudgetController()
        result = bc.can_proceed()
        assert result.allowed is True
        assert result.violation_type is None

    def test_step_count_increment(self):
        """step_count 递增后正确反映。"""
        bc = BudgetController()
        for _ in range(3):
            bc.increment_step()
        assert bc.budget["step_count"] == 3
        assert bc.remaining_steps() == 5  # 默认 max=8

    def test_tool_call_increment(self):
        """tool_call_count 递增正确。"""
        bc = BudgetController()
        bc.increment_tool_call("rag_search")
        bc.increment_tool_call("db_query")
        bc.increment_tool_call("db_query")
        assert bc.budget["tool_call_count"] == 3
        assert bc.budget["db_query_count"] == 2

    def test_llm_call_increment_with_tokens(self):
        """LLM 调用递增并累加 token。"""
        bc = BudgetController()
        bc.increment_llm_call(tokens_used=100)
        bc.increment_llm_call(tokens_used=50)
        assert bc.budget["llm_call_count"] == 2
        assert bc.budget["token_used"] == 150


class TestBudgetExhaustion:
    """Budget 超限终止测试。"""

    def test_steps_exceeded(self):
        """达到 max_steps 后禁止继续。"""
        budget = get_default_budget()
        budget["max_steps"] = 3
        bc = BudgetController(budget)
        for _ in range(3):
            bc.increment_step()
        result = bc.can_proceed()
        assert result.allowed is False
        assert result.violation_type == BudgetViolationType.STEPS_EXCEEDED

    def test_tool_calls_exceeded(self):
        """达到 max_tool_calls 后禁止继续。"""
        budget = get_default_budget()
        budget["max_tool_calls"] = 2
        bc = BudgetController(budget)
        bc.increment_tool_call("rag_search")
        bc.increment_tool_call("web_search")
        result = bc.can_proceed()
        assert result.allowed is False
        assert result.violation_type == BudgetViolationType.TOOL_CALLS_EXCEEDED

    def test_db_queries_exceeded(self):
        """达到 max_db_queries 后禁止继续。"""
        budget = get_default_budget()
        budget["max_db_queries"] = 2
        bc = BudgetController(budget)
        bc.increment_tool_call("db_query")
        bc.increment_tool_call("db_query")
        result = bc.can_proceed()
        assert result.allowed is False
        assert result.violation_type == BudgetViolationType.DB_QUERIES_EXCEEDED

    def test_llm_calls_exceeded(self):
        """达到 max_llm_calls 后禁止继续。"""
        budget = get_default_budget()
        budget["max_llm_calls"] = 1
        bc = BudgetController(budget)
        bc.increment_llm_call()
        result = bc.can_proceed()
        assert result.allowed is False
        assert result.violation_type == BudgetViolationType.LLM_CALLS_EXCEEDED

    def test_deadline_exceeded(self):
        """超过 deadline 后禁止继续。"""
        budget = get_default_budget()
        budget["deadline_ts"] = time.time() - 1.0  # 1 秒前已过期
        bc = BudgetController(budget)
        result = bc.can_proceed()
        assert result.allowed is False
        assert result.violation_type == BudgetViolationType.DEADLINE_EXCEEDED

    def test_token_budget_exceeded(self):
        """超过 token 预算后禁止继续。"""
        budget = get_default_budget()
        budget["token_budget"] = 100
        budget["token_used"] = 100
        bc = BudgetController(budget)
        result = bc.can_proceed()
        assert result.allowed is False
        assert result.violation_type == BudgetViolationType.TOKEN_BUDGET_EXCEEDED

    def test_can_db_query_additional_check(self):
        """can_db_query 在 db_query_count 达到 max 时也拒绝。"""
        budget = get_default_budget()
        budget["max_db_queries"] = 1
        bc = BudgetController(budget)
        bc.increment_tool_call("db_query")
        result = bc.can_db_query()
        assert result.allowed is False
        assert result.violation_type == BudgetViolationType.DB_QUERIES_EXCEEDED

    def test_can_db_query_when_other_limit_exceeded(self):
        """can_db_query 在其他维度超限时也拒绝。"""
        budget = get_default_budget()
        budget["max_steps"] = 1
        bc = BudgetController(budget)
        bc.increment_step()
        result = bc.can_db_query()
        assert result.allowed is False
        assert result.violation_type == BudgetViolationType.STEPS_EXCEEDED


class TestBudgetSnapshot:
    """Budget 快照与剩余查询测试。"""

    def test_snapshot_returns_dict(self):
        """snapshot 返回 dict 副本。"""
        bc = BudgetController()
        bc.increment_step()
        snapshot = bc.snapshot()
        assert isinstance(snapshot, dict)
        assert snapshot["step_count"] == 1

    def test_remaining_helpers(self):
        """remaining_* 辅助函数正确性。"""
        budget = get_default_budget()
        budget["max_steps"] = 5
        budget["max_tool_calls"] = 4
        budget["max_db_queries"] = 2
        bc = BudgetController(budget)
        bc.increment_step()
        bc.increment_tool_call("rag_search")
        bc.increment_tool_call("db_query")
        assert bc.remaining_steps() == 4
        assert bc.remaining_tool_calls() == 2
        assert bc.remaining_db_queries() == 1


class TestBuildBudgetFromConfig:
    """build_budget_from_config 配置构造测试。"""

    def test_default_when_no_config(self):
        """无配置时使用默认值。"""
        budget = build_budget_from_config(None)
        assert budget["max_steps"] == 8
        assert budget["max_tool_calls"] == 6
        assert budget["max_db_queries"] == 3

    def test_override_from_config(self):
        """用户配置覆盖默认。"""
        cfg = {
            "max_steps": 10,
            "max_tool_calls": 8,
            "max_db_queries": 5,
            "max_llm_calls": 4,
            "token_budget": 8000,
            "max_latency_ms": 60000,
        }
        budget = build_budget_from_config(cfg)
        assert budget["max_steps"] == 10
        assert budget["max_tool_calls"] == 8
        assert budget["max_db_queries"] == 5
        assert budget["max_llm_calls"] == 4
        assert budget["token_budget"] == 8000

    def test_max_latency_override(self):
        """max_latency_ms 参数覆盖配置。"""
        budget = build_budget_from_config({"max_latency_ms": 1000}, max_latency_ms=500)
        # 1 秒内的 deadline
        assert budget["deadline_ts"] > 0
        diff = budget["deadline_ts"] - time.time()
        assert 0 < diff < 1.0  # 使用 500ms 覆盖
