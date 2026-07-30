"""DAG 调度器。

基于拓扑排序执行 DAG 执行计划，同一层级（无依赖关系）的步骤并行执行，
不同层串行执行。

核心算法：
- 拓扑分层（Kahn's algorithm 变体）：将步骤按依赖关系分为多个层级
- 同层并行：同一层级的步骤通过 asyncio.gather 并行执行
- 层间串行：上一层全部完成后，下一层才开始执行

示例：
    输入 DAG:
        step1 (无依赖)     → Layer 0
        step2 (无依赖)     → Layer 0
        step3 (依赖 step1, step2) → Layer 1
        step4 (依赖 step3)  → Layer 2

    执行顺序:
        Layer 0: [step1, step2] 并行执行
        Layer 1: [step3] 串行执行（等待 Layer 0 完成）
        Layer 2: [step4] 串行执行（等待 Layer 1 完成）
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from agent.langgraph.routers.models import ExecutionPlan, PlanStep

if TYPE_CHECKING:
    from agent.langgraph.state import AgentState, ToolResult

logger = logging.getLogger(__name__)


class DAGScheduler:
    """DAG 调度器。

    将 ExecutionPlan 按 DAG 依赖关系分层执行，
    同层步骤并行，不同层串行。
    """

    async def execute(
        self, plan: ExecutionPlan, state: "AgentState"
    ) -> list["ToolResult"]:
        """执行 DAG 计划。

        执行策略：
        1. 将步骤按依赖关系分层（拓扑排序）
        2. 同层步骤用 asyncio.gather 并行执行
        3. 不同层串行执行
        4. 每层完成后，将结果传递给依赖该层的下一层

        Args:
            plan: 执行计划
            state: 当前状态

        Returns:
            list[ToolResult]: 所有步骤的执行结果
        """
        from agent.langgraph.state import ToolResult

        results: dict[str, ToolResult] = {}

        # 1. 拓扑分层
        layers = self._topological_layers(plan.steps)

        logger.info(
            f"[DAGScheduler] 计划 {plan.plan_id} 分为 {len(layers)} 层, "
            f"共 {len(plan.steps)} 个步骤"
        )

        # 2. 逐层执行
        for layer_idx, layer in enumerate(layers):
            layer_step_ids = [s.step_id for s in layer]
            logger.info(
                f"[DAGScheduler] 执行第 {layer_idx + 1} 层: {layer_step_ids}"
            )

            # 同层并行执行
            layer_results = await asyncio.gather(
                *[self._execute_step(step, state, results) for step in layer],
                return_exceptions=True,
            )

            # 收集结果
            for step, result in zip(layer, layer_results):
                if isinstance(result, Exception):
                    logger.error(
                        f"[DAGScheduler] 步骤 {step.step_id} ({step.tool}) "
                        f"执行失败: {result}"
                    )
                    results[step.step_id] = ToolResult(
                        step_id=step.step_id,
                        tool=step.tool,
                        success=False,
                        error=str(result),
                    )
                else:
                    results[step.step_id] = result
                    logger.info(
                        f"[DAGScheduler] 步骤 {step.step_id} ({step.tool}) "
                        f"完成: success={result.get('success', False)}, "
                        f"latency={result.get('latency_ms', 0)}ms"
                    )

        return list(results.values())

    def _topological_layers(self, steps: list[PlanStep]) -> list[list[PlanStep]]:
        """拓扑排序分层。

        将 DAG 分为多个层级，同一层级的步骤无依赖关系，可并行执行。

        算法（Kahn's algorithm 变体）：
        1. 第0层：无依赖的步骤
        2. 第N层：所有依赖都在前 N-1 层的步骤

        Args:
            steps: 步骤列表

        Returns:
            list[list[PlanStep]]: 分层后的步骤列表

        Raises:
            ValueError: 检测到循环依赖
        """
        completed: set[str] = set()
        layers: list[list[PlanStep]] = []

        remaining = list(steps)
        while remaining:
            # 找出当前可执行的步骤（所有依赖已完成）
            current_layer = [
                s
                for s in remaining
                if all(dep in completed for dep in s.depends_on)
            ]

            if not current_layer:
                # 循环依赖检测
                remaining_ids = [s.step_id for s in remaining]
                raise ValueError(
                    f"检测到循环依赖，未完成的步骤: {remaining_ids}。"
                    f"请检查 PlanStep.depends_on 是否存在循环引用。"
                )

            layers.append(current_layer)
            for s in current_layer:
                completed.add(s.step_id)
                remaining.remove(s)

        return layers

    async def _execute_step(
        self,
        step: PlanStep,
        state: "AgentState",
        previous_results: dict[str, "ToolResult"],
    ) -> "ToolResult":
        """执行单个步骤。

        将步骤分发到对应的工具执行器，并收集结果。

        Args:
            step: 执行步骤
            state: 全局状态
            previous_results: 前置步骤结果（可用于步骤间数据传递）

        Returns:
            ToolResult: 工具执行结果
        """
        from agent.langgraph.executor.tool_dispatcher import get_tool_dispatcher

        dispatcher = get_tool_dispatcher()
        return await dispatcher.dispatch(step, state, previous_results)
