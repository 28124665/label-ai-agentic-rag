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
"""计划执行器与并行调度单元测试。

覆盖范围：
- DAG 调度器：拓扑分层、并行执行、循环依赖检测
- 工具分发器：多 DB 并行、步骤参数优先、异常处理
- 结果聚合器：多源合并、失败步骤隔离
- 计划执行器节点：正常执行、降级处理
- 数据结构：StepArgs、PlanStep.from_dict
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.langgraph.executor.dag_scheduler import DAGScheduler
from agent.langgraph.executor.result_aggregator import ResultAggregator
from agent.langgraph.executor.tool_dispatcher import ToolDispatcher
from agent.langgraph.routers.models import ExecutionPlan, PlanStep, StepArgs


# ============================================================================
# 数据结构测试
# ============================================================================


class TestStepArgs:
    """测试 StepArgs 数据模型。"""

    def test_from_dict_full(self):
        """测试从完整 dict 构造 StepArgs。"""
        data = {
            "query": "一分厂人力成本",
            "db_id": "hr_system",
            "kb_ids": ["kb1", "kb2"],
            "mcp_server_name": "hr_mcp",
        }
        args = StepArgs.from_dict(data)
        assert args.query == "一分厂人力成本"
        assert args.db_id == "hr_system"
        assert args.kb_ids == ["kb1", "kb2"]
        assert args.mcp_server_name == "hr_mcp"

    def test_from_dict_empty(self):
        """测试从空 dict 构造 StepArgs。"""
        args = StepArgs.from_dict({})
        assert args.query == ""
        assert args.db_id == ""
        assert args.kb_ids == []

    def test_from_dict_none(self):
        """测试从 None 构造 StepArgs。"""
        args = StepArgs.from_dict(None)
        assert args.query == ""
        assert args.db_id == ""

    def test_from_dict_filters_unknown_fields(self):
        """测试过滤未知字段。"""
        data = {"query": "test", "unknown_field": "value"}
        args = StepArgs.from_dict(data)
        assert args.query == "test"
        assert not hasattr(args, "unknown_field")


class TestPlanStepFromDict:
    """测试 PlanStep.from_dict 方法。"""

    def test_from_dict_with_structured_args(self):
        """测试从 dict 构造 PlanStep，自动转换 args 为 StepArgs。"""
        data = {
            "step_id": "step1",
            "tool": "database",
            "args": {"query": "人力成本", "db_id": "hr_system"},
            "depends_on": [],
            "can_parallel": True,
            "description": "查询人力成本",
        }
        step = PlanStep.from_dict(data)
        assert step.step_id == "step1"
        assert step.tool == "database"
        assert isinstance(step.args, StepArgs)
        assert step.args.query == "人力成本"
        assert step.args.db_id == "hr_system"
        assert step.can_parallel is True
        assert step.description == "查询人力成本"

    def test_from_dict_with_empty_args(self):
        """测试 args 为空时的构造。"""
        data = {"step_id": "step1", "tool": "rag"}
        step = PlanStep.from_dict(data)
        assert isinstance(step.args, StepArgs)
        assert step.args.query == ""


# ============================================================================
# DAG 调度器测试
# ============================================================================


class TestDAGScheduler:
    """测试 DAG 调度器。"""

    def setup_method(self):
        """初始化测试环境。"""
        self.scheduler = DAGScheduler()

    def test_topological_layers_all_parallel(self):
        """测试全并行场景：所有步骤无依赖，都在同一层。"""
        steps = [
            PlanStep(step_id="s1", tool="database", depends_on=[]),
            PlanStep(step_id="s2", tool="database", depends_on=[]),
            PlanStep(step_id="s3", tool="rag", depends_on=[]),
        ]

        layers = self.scheduler._topological_layers(steps)

        assert len(layers) == 1
        assert len(layers[0]) == 3
        step_ids = {s.step_id for s in layers[0]}
        assert step_ids == {"s1", "s2", "s3"}

    def test_topological_layers_sequential(self):
        """测试串行依赖场景：step2 依赖 step1。"""
        steps = [
            PlanStep(step_id="s1", tool="database", depends_on=[]),
            PlanStep(step_id="s2", tool="rag", depends_on=["s1"]),
            PlanStep(step_id="s3", tool="web", depends_on=["s2"]),
        ]

        layers = self.scheduler._topological_layers(steps)

        assert len(layers) == 3
        assert layers[0][0].step_id == "s1"
        assert layers[1][0].step_id == "s2"
        assert layers[2][0].step_id == "s3"

    def test_topological_layers_mixed(self):
        """测试混合场景：部分并行，部分串行。"""
        steps = [
            PlanStep(step_id="s1", tool="database", depends_on=[]),
            PlanStep(step_id="s2", tool="database", depends_on=[]),
            PlanStep(step_id="s3", tool="rag", depends_on=["s1", "s2"]),
        ]

        layers = self.scheduler._topological_layers(steps)

        assert len(layers) == 2
        assert len(layers[0]) == 2  # s1, s2 并行
        assert len(layers[1]) == 1  # s3 等待 s1, s2
        assert layers[1][0].step_id == "s3"

    def test_topological_layers_circular_dependency(self):
        """测试循环依赖检测。"""
        steps = [
            PlanStep(step_id="s1", tool="database", depends_on=["s2"]),
            PlanStep(step_id="s2", tool="database", depends_on=["s1"]),
        ]

        with pytest.raises(ValueError, match="循环依赖"):
            self.scheduler._topological_layers(steps)

    @pytest.mark.asyncio
    async def test_execute_all_parallel(self):
        """测试全并行执行。"""
        plan = ExecutionPlan(
            plan_id="test_plan_001",
            steps=[
                PlanStep(step_id="s1", tool="database", args=StepArgs(db_id="hr")),
                PlanStep(step_id="s2", tool="database", args=StepArgs(db_id="sc")),
                PlanStep(step_id="s3", tool="rag"),
            ],
        )

        # Mock 工具分发器
        mock_results = [
            {"step_id": "s1", "tool": "database", "success": True, "db_result": {"rows": [{"cost": 100}], "row_count": 1}, "db_id": "hr"},
            {"step_id": "s2", "tool": "database", "success": True, "db_result": {"rows": [{"cost": 200}], "row_count": 1}, "db_id": "sc"},
            {"step_id": "s3", "tool": "rag", "success": True, "rag_docs": [{"content": "报告"}], "rag_quality_score": 0.8},
        ]

        with patch.object(
            DAGScheduler, "_execute_step", new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.side_effect = mock_results
            results = await self.scheduler.execute(plan, {})

        assert len(results) == 3
        success_count = sum(1 for r in results if r.get("success"))
        assert success_count == 3

    @pytest.mark.asyncio
    async def test_execute_with_failure(self):
        """测试部分步骤失败时不影响其他步骤。"""
        plan = ExecutionPlan(
            plan_id="test_plan_002",
            steps=[
                PlanStep(step_id="s1", tool="database"),
                PlanStep(step_id="s2", tool="rag"),
            ],
        )

        async def mock_execute(step, state, prev_results):
            if step.step_id == "s1":
                return {"step_id": "s1", "tool": "database", "success": False, "error": "DB连接失败"}
            return {"step_id": "s2", "tool": "rag", "success": True, "rag_docs": []}

        with patch.object(
            DAGScheduler, "_execute_step", new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.side_effect = mock_execute_side_effect
            results = await self.scheduler.execute(plan, {})

        assert len(results) == 2
        # 确保失败的步骤被记录，成功的步骤不受影响
        failed = [r for r in results if not r.get("success")]
        success = [r for r in results if r.get("success")]
        assert len(failed) == 1
        assert len(success) == 1


async def mock_execute_side_effect(step, state, prev_results):
    """Mock 执行函数：s1 失败，s2 成功。"""
    if step.step_id == "s1":
        return {"step_id": "s1", "tool": "database", "success": False, "error": "DB连接失败"}
    return {"step_id": "s2", "tool": "rag", "success": True, "rag_docs": []}


# ============================================================================
# 结果聚合器测试
# ============================================================================


class TestResultAggregator:
    """测试结果聚合器。"""

    def test_aggregate_multi_db_results(self):
        """测试多 DB 结果合并。"""
        tool_results = [
            {
                "step_id": "s1",
                "tool": "database",
                "success": True,
                "db_result": {"rows": [{"hr_cost": 500000}], "row_count": 1},
                "db_quality_score": 0.9,
                "db_id": "hr_system",
            },
            {
                "step_id": "s2",
                "tool": "database",
                "success": True,
                "db_result": {"rows": [{"material_cost": 1200000}], "row_count": 1},
                "db_quality_score": 0.85,
                "db_id": "supply_chain",
            },
        ]

        aggregated = ResultAggregator.aggregate(tool_results)

        assert aggregated["db_result"]["row_count"] == 2
        assert aggregated["db_result"]["is_aggregated"] is True
        assert "hr_system" in aggregated["db_result"]["source_dbs"]
        assert "supply_chain" in aggregated["db_result"]["source_dbs"]
        # 每行应标注来源数据库
        assert aggregated["db_result"]["rows"][0]["_source_db"] == "hr_system"
        assert aggregated["db_result"]["rows"][1]["_source_db"] == "supply_chain"
        # 质量分取最高
        assert aggregated["db_quality_score"] == 0.9

    def test_aggregate_rag_results_sorted_by_score(self):
        """测试 RAG 结果按 score 降序排列。"""
        tool_results = [
            {
                "step_id": "s1",
                "tool": "rag",
                "success": True,
                "rag_docs": [
                    {"content": "doc1", "score": 0.5},
                    {"content": "doc2", "score": 0.8},
                ],
                "rag_quality_score": 0.8,
            },
            {
                "step_id": "s2",
                "tool": "rag",
                "success": True,
                "rag_docs": [{"content": "doc3", "score": 0.9}],
                "rag_quality_score": 0.9,
            },
        ]

        aggregated = ResultAggregator.aggregate(tool_results)

        assert len(aggregated["rag_docs"]) == 3
        # 验证降序排列
        scores = [doc["score"] for doc in aggregated["rag_docs"]]
        assert scores == [0.9, 0.8, 0.5]
        assert aggregated["rag_quality_score"] == 0.9
        assert aggregated["rag_top_score"] == 0.9

    def test_aggregate_skips_failed_steps(self):
        """测试跳过失败步骤。"""
        tool_results = [
            {
                "step_id": "s1",
                "tool": "database",
                "success": False,
                "error": "连接失败",
                "db_result": {},
            },
            {
                "step_id": "s2",
                "tool": "rag",
                "success": True,
                "rag_docs": [{"content": "doc1", "score": 0.7}],
                "rag_quality_score": 0.7,
            },
        ]

        aggregated = ResultAggregator.aggregate(tool_results)

        # 失败步骤的 DB 结果不应出现
        assert aggregated["db_result"]["row_count"] == 0
        # 成功步骤的 RAG 结果应保留
        assert len(aggregated["rag_docs"]) == 1
        assert aggregated["rag_docs"][0]["content"] == "doc1"

    def test_aggregate_empty_results(self):
        """测试空结果列表。"""
        aggregated = ResultAggregator.aggregate([])

        assert aggregated["rag_docs"] == []
        assert aggregated["rag_quality_score"] == 0.0
        assert aggregated["db_result"]["row_count"] == 0
        assert aggregated["web_docs"] == []

    def test_aggregate_web_results(self):
        """测试 Web 结果合并。"""
        tool_results = [
            {
                "step_id": "s1",
                "tool": "web",
                "success": True,
                "web_docs": [{"content": "web1", "url": "http://1"}],
            },
            {
                "step_id": "s2",
                "tool": "web",
                "success": True,
                "web_docs": [{"content": "web2", "url": "http://2"}],
            },
        ]

        aggregated = ResultAggregator.aggregate(tool_results)

        assert len(aggregated["web_docs"]) == 2


# ============================================================================
# 工具分发器测试
# ============================================================================


class TestToolDispatcher:
    """测试工具分发器。

    注意：由于 database_tool/rag_tool 模块导入时会加载重型依赖（MCP、DB 连接等），
    测试中直接 mock ToolDispatcher 的私有方法 _execute_database/_execute_rag/_execute_web，
    验证 dispatch 方法的路由逻辑、参数传递和异常处理。
    """

    @pytest.mark.asyncio
    async def test_dispatch_database_routes_correctly(self):
        """测试 database 类型步骤路由到 _execute_database。"""
        step = PlanStep(
            step_id="s1",
            tool="database",
            args=StepArgs(query="人力成本", db_id="hr_system"),
        )
        state = {"user_question": "原始问题", "db_id": "default_db"}

        dispatcher = ToolDispatcher()
        expected_result = {
            "db_result": {"rows": [{"cost": 100}], "row_count": 1},
            "db_quality_score": 0.9,
            "db_id": "hr_system",
        }
        with patch.object(
            dispatcher, "_execute_database", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = expected_result
            result = await dispatcher.dispatch(step, state, {})

        assert result["success"] is True
        assert result["step_id"] == "s1"
        assert result["tool"] == "database"
        assert result["db_id"] == "hr_system"
        mock_exec.assert_called_once_with(step, state)

    @pytest.mark.asyncio
    async def test_dispatch_rag_routes_correctly(self):
        """测试 rag 类型步骤路由到 _execute_rag。"""
        step = PlanStep(
            step_id="s1",
            tool="rag",
            args=StepArgs(query="成本报告", kb_ids=["report_kb"]),
        )
        state = {"user_question": "原始问题"}

        dispatcher = ToolDispatcher()
        expected_result = {
            "rag_docs": [{"content": "报告", "score": 0.8}],
            "rag_quality_score": 0.8,
            "rag_has_relevant": True,
            "rag_relevant_count": 1,
            "rag_top_score": 0.8,
            "kb_ids": ["report_kb"],
        }
        with patch.object(
            dispatcher, "_execute_rag", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = expected_result
            result = await dispatcher.dispatch(step, state, {})

        assert result["success"] is True
        assert result["rag_docs"][0]["content"] == "报告"
        mock_exec.assert_called_once_with(step, state)

    @pytest.mark.asyncio
    async def test_dispatch_web_routes_correctly(self):
        """测试 web 类型步骤路由到 _execute_web。"""
        step = PlanStep(
            step_id="s1",
            tool="web",
            args=StepArgs(query="搜索内容"),
        )
        state = {"user_question": "原始问题"}

        dispatcher = ToolDispatcher()
        expected_result = {"web_docs": [{"content": "网页", "url": "http://1"}]}
        with patch.object(
            dispatcher, "_execute_web", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = expected_result
            result = await dispatcher.dispatch(step, state, {})

        assert result["success"] is True
        assert result["web_docs"][0]["content"] == "网页"
        mock_exec.assert_called_once_with(step, state)

    @pytest.mark.asyncio
    async def test_dispatch_handles_exception(self):
        """测试异常处理：工具调用失败时返回 success=False。"""
        step = PlanStep(
            step_id="s1",
            tool="database",
            args=StepArgs(query="查询", db_id="hr_system"),
        )
        state = {"user_question": "查询"}

        dispatcher = ToolDispatcher()
        with patch.object(
            dispatcher, "_execute_database", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.side_effect = RuntimeError("DB 连接失败")
            result = await dispatcher.dispatch(step, state, {})

        assert result["success"] is False
        assert "DB 连接失败" in result["error"]
        assert result["step_id"] == "s1"
        assert result["tool"] == "database"

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool_raises(self):
        """测试未知工具类型触发异常。"""
        # 使用 mock 绕过 Literal 类型检查
        step = MagicMock()
        step.step_id = "s1"
        step.tool = "unknown_tool"
        step.args = StepArgs()
        step.description = ""

        dispatcher = ToolDispatcher()
        result = await dispatcher.dispatch(step, {}, {})

        assert result["success"] is False
        assert "未知工具类型" in result["error"]


# ============================================================================
# 计划执行器节点测试
# ============================================================================


class TestPlanExecutorNode:
    """测试计划执行器节点。"""

    @pytest.mark.asyncio
    async def test_normal_execution(self):
        """测试正常执行流程。"""
        from agent.langgraph.nodes.plan_executor import plan_executor_node
        from agent.langgraph.routers.models import RouteDecision

        plan_data = {
            "plan_id": "test_plan_001",
            "steps": [
                {
                    "step_id": "s1",
                    "tool": "database",
                    "args": {"query": "人力成本", "db_id": "hr_system"},
                    "depends_on": [],
                    "can_parallel": True,
                },
                {
                    "step_id": "s2",
                    "tool": "rag",
                    "args": {"query": "成本报告", "kb_ids": ["report_kb"]},
                    "depends_on": [],
                    "can_parallel": True,
                },
            ],
            "estimated_tokens": 1000,
            "fallback_strategy": "sequential",
        }

        state = {
            "user_question": "一分厂成本",
            "route_decision": RouteDecision(
                target="hybrid",
                confidence=0.85,
                source="planner",
                reason="复杂任务",
                complexity="complex",
                metadata={"plan": plan_data},
            ),
        }

        # Mock DAGScheduler
        mock_tool_results = [
            {
                "step_id": "s1",
                "tool": "database",
                "success": True,
                "db_result": {"rows": [{"cost": 500}], "row_count": 1},
                "db_quality_score": 0.9,
                "db_id": "hr_system",
            },
            {
                "step_id": "s2",
                "tool": "rag",
                "success": True,
                "rag_docs": [{"content": "报告", "score": 0.8}],
                "rag_quality_score": 0.8,
                "rag_has_relevant": True,
                "rag_relevant_count": 1,
                "rag_top_score": 0.8,
            },
        ]

        with patch.object(
            DAGScheduler, "execute", new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.return_value = mock_tool_results
            result = await plan_executor_node(state)

        assert result["plan_execution_status"] == "completed"
        assert len(result["tool_results"]) == 2
        # 验证兼容字段已写入
        assert result["db_result"]["row_count"] == 1
        assert len(result["rag_docs"]) == 1
        assert result["rag_quality_score"] == 0.8
        assert result["db_quality_score"] == 0.9

    @pytest.mark.asyncio
    async def test_fallback_no_plan(self):
        """测试降级处理：route_decision 中无 plan。"""
        from agent.langgraph.nodes.plan_executor import plan_executor_node
        from agent.langgraph.routers.models import RouteDecision

        state = {
            "user_question": "测试",
            "route_decision": RouteDecision(
                target="hybrid",
                confidence=0.85,
                source="planner",
                reason="复杂任务",
                complexity="complex",
                metadata={},  # 无 plan
            ),
        }

        result = await plan_executor_node(state)

        assert result["plan_execution_status"] == "failed"
        assert result["tool_results"] == []
        assert result["rag_docs"] == []
        assert result["db_result"] == {}

    @pytest.mark.asyncio
    async def test_fallback_no_route_decision(self):
        """测试降级处理：state 中无 route_decision。"""
        from agent.langgraph.nodes.plan_executor import plan_executor_node

        state = {"user_question": "测试"}

        result = await plan_executor_node(state)

        assert result["plan_execution_status"] == "failed"
        assert result["tool_results"] == []

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        """测试部分步骤失败时标记为 partial。"""
        from agent.langgraph.nodes.plan_executor import plan_executor_node
        from agent.langgraph.routers.models import RouteDecision

        plan_data = {
            "plan_id": "test_plan_partial",
            "steps": [
                {"step_id": "s1", "tool": "database", "args": {}, "depends_on": []},
                {"step_id": "s2", "tool": "rag", "args": {}, "depends_on": []},
            ],
        }

        state = {
            "user_question": "测试",
            "route_decision": RouteDecision(
                target="hybrid",
                confidence=0.85,
                source="planner",
                reason="复杂任务",
                complexity="complex",
                metadata={"plan": plan_data},
            ),
        }

        mock_tool_results = [
            {"step_id": "s1", "tool": "database", "success": False, "error": "失败"},
            {
                "step_id": "s2",
                "tool": "rag",
                "success": True,
                "rag_docs": [],
                "rag_quality_score": 0.0,
            },
        ]

        with patch.object(
            DAGScheduler, "execute", new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.return_value = mock_tool_results
            result = await plan_executor_node(state)

        # 有失败步骤，状态应为 partial
        assert result["plan_execution_status"] == "partial"
        assert len(result["tool_results"]) == 2


# ============================================================================
# 路由分发测试
# ============================================================================


class TestRouteDecision:
    """测试路由分发函数。"""

    def test_route_to_plan_executor_for_complex_planner(self):
        """测试 Planner 复杂任务路由到 plan_executor。"""
        from agent.langgraph.nodes.intent_router import route_decision
        from agent.langgraph.routers.models import RouteDecision

        state = {
            "route_target": "hybrid",
            "route_decision": RouteDecision(
                target="hybrid",
                confidence=0.85,
                source="planner",
                reason="复杂任务",
                complexity="complex",
                metadata={},
            ),
        }

        assert route_decision(state) == "plan_executor"

    def test_route_to_rag_for_non_planner(self):
        """测试非 Planner 决策的 hybrid 仍走 rag_tool。"""
        from agent.langgraph.nodes.intent_router import route_decision
        from agent.langgraph.routers.models import RouteDecision

        state = {
            "route_target": "hybrid",
            "route_decision": RouteDecision(
                target="hybrid",
                confidence=0.85,
                source="llm",  # 非 planner
                reason="",
                complexity="moderate",  # 非 complex
                metadata={},
            ),
        }

        assert route_decision(state) == "rag_tool"

    def test_route_to_rag_for_simple_planner(self):
        """测试 Planner 但非 complex 的决策不走 plan_executor。"""
        from agent.langgraph.nodes.intent_router import route_decision
        from agent.langgraph.routers.models import RouteDecision

        state = {
            "route_target": "rag",
            "route_decision": RouteDecision(
                target="rag",
                confidence=0.85,
                source="planner",
                reason="",
                complexity="moderate",  # 非 complex
                metadata={},
            ),
        }

        assert route_decision(state) == "rag_tool"
