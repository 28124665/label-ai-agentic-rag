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
"""计划执行器节点。

当意图路由的第3层 Planner 判断为复杂任务（complexity="complex"）时，
路由到此节点。节点读取 Planner 生成的 DAG 执行计划，按依赖关系
并行执行所有步骤，并将结果聚合后写入 AgentState。

核心流程：
1. 从 route_decision.metadata["plan"] 读取执行计划
2. 调用 DAGScheduler 按拓扑分层并行执行
3. 调用 ResultAggregator 聚合多工具结果
4. 同步写入兼容字段（rag_docs, db_result, web_docs），确保后续节点兼容
"""

import logging
import time
from typing import Any

from agent.langgraph.executor.dag_scheduler import DAGScheduler
from agent.langgraph.executor.result_aggregator import ResultAggregator
from agent.langgraph.routers.models import ExecutionPlan, PlanStep
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


async def plan_executor_node(state: AgentState) -> dict[str, Any]:
    """计划执行器节点。

    读取 state["route_decision"].metadata["plan"]，
    按 DAG 依赖关系并行执行所有步骤，
    将结果聚合到 state["tool_results"]，
    并同步写入兼容字段（rag_docs, db_result, web_docs 等）。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()

    # 1. 获取执行计划
    route_decision = state.get("route_decision")
    if not route_decision:
        logger.error("[plan_executor] route_decision 为空，降级处理")
        return _fallback_empty_result(start_time)

    plan_data = route_decision.metadata.get("plan")
    if not plan_data:
        logger.error("[plan_executor] metadata 中未找到 plan，降级处理")
        return _fallback_empty_result(start_time)

    # 2. 解析执行计划
    try:
        plan = _parse_plan(plan_data)
    except Exception as e:
        logger.error(f"[plan_executor] 解析执行计划失败: {e}，降级处理")
        return _fallback_empty_result(start_time)

    if not plan.steps:
        logger.warning("[plan_executor] 执行计划为空，降级处理")
        return _fallback_empty_result(start_time)

    logger.info(
        f"[plan_executor] 开始执行计划 {plan.plan_id}: "
        f"{len(plan.steps)} 个步骤"
    )

    # 3. DAG 调度执行
    try:
        scheduler = DAGScheduler()
        tool_results = await scheduler.execute(plan, state)
    except ValueError as e:
        # 循环依赖等 DAG 解析错误
        logger.error(f"[plan_executor] DAG 调度失败: {e}，降级处理")
        return _fallback_empty_result(start_time)
    except Exception as e:
        logger.error(f"[plan_executor] 计划执行异常: {e}，降级处理")
        return _fallback_empty_result(start_time)

    # 4. 聚合结果到兼容字段
    aggregated = ResultAggregator.aggregate(tool_results)

    elapsed_ms = int((time.time() - start_time) * 1000)

    # 统计执行情况
    success_count = sum(1 for r in tool_results if r.get("success", False))
    fail_count = len(tool_results) - success_count

    logger.info(
        f"[plan_executor] 计划 {plan.plan_id} 执行完成: "
        f"total={len(tool_results)}, success={success_count}, "
        f"failed={fail_count}, {elapsed_ms}ms"
    )

    return {
        "tool_results": tool_results,
        "execution_plan": plan,
        "plan_execution_status": "completed" if fail_count == 0 else "partial",
        # 同步写入兼容字段（确保 quality_check 和 prompt_assembly 兼容）
        "rag_docs": aggregated.get("rag_docs", []),
        "rag_quality_score": aggregated.get("rag_quality_score", 0.0),
        "rag_has_relevant": aggregated.get("rag_has_relevant", False),
        "rag_relevant_count": aggregated.get("rag_relevant_count", 0),
        "rag_top_score": aggregated.get("rag_top_score", 0.0),
        "db_result": aggregated.get("db_result", {}),
        "db_quality_score": aggregated.get("db_quality_score", 0.0),
        "web_docs": aggregated.get("web_docs", []),
        "node_timings": {"plan_executor": elapsed_ms},
    }


def _parse_plan(plan_data: dict[str, Any]) -> ExecutionPlan:
    """从 dict 解析执行计划。

    使用 PlanStep.from_dict 自动将 args dict 转为 StepArgs。

    Args:
        plan_data: 执行计划的 dict 表示

    Returns:
        ExecutionPlan: 解析后的执行计划对象
    """
    steps_data = plan_data.get("steps", [])
    steps = [PlanStep.from_dict(s) for s in steps_data]

    return ExecutionPlan(
        plan_id=plan_data.get("plan_id", f"plan_{int(time.time())}"),
        steps=steps,
        estimated_tokens=plan_data.get("estimated_tokens", 1000),
        fallback_strategy=plan_data.get("fallback_strategy", "sequential"),
    )


def _fallback_empty_result(start_time: float) -> dict[str, Any]:
    """降级处理：返回空结果，不阻塞后续流程。

    当计划解析失败或执行异常时，返回空的工具结果，
    让流程继续走到 quality_check → prompt_assembly。

    Args:
        start_time: 节点开始时间

    Returns:
        dict: 空结果的状态更新
    """
    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.warning(f"[plan_executor] 降级处理: 返回空结果 ({elapsed_ms}ms)")

    return {
        "tool_results": [],
        "plan_execution_status": "failed",
        "rag_docs": [],
        "rag_quality_score": 0.0,
        "rag_has_relevant": False,
        "rag_relevant_count": 0,
        "rag_top_score": 0.0,
        "db_result": {},
        "db_quality_score": 0.0,
        "web_docs": [],
        "node_timings": {"plan_executor": elapsed_ms},
    }
