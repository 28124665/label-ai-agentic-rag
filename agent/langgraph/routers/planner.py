"""第3层：Planner 复杂任务规划器。

当第2层 LLM 语义路由判断为复杂任务（complexity="complex"）时，调用 Planner 进行任务拆解：
- 使用 Qwen3.6-27B（或同等能力模型）
- 生成结构化执行计划（DAG）
- 识别可并行执行的子任务
- 支持超时降级

设计原则：
- 高成本、高延迟（< 5s）
- 覆盖 5%-10% 的请求
- 处理多步骤、多工具的复杂任务
"""

import asyncio
import logging
import time
from typing import Any, Optional

from agent.langgraph.routers.config_loader import IntentRouterConfig, load_config
from agent.langgraph.routers.models import ExecutionPlan, PlanStep, RouteDecision
from agent.langgraph.skills.models import ResolvedSkillSet

logger = logging.getLogger(__name__)


class Planner:
    """第3层 Planner 复杂任务规划器。"""

    def __init__(self, config: Optional[IntentRouterConfig] = None):
        """初始化 Planner。

        Args:
            config: 路由配置，如果为 None 则从配置文件加载
        """
        self.component_name = "Planner"
        self._config = config or load_config()
        planner_config = self._config.planner
        self._timeout_ms = planner_config.get("timeout_ms", 5000)
        self._max_steps = planner_config.get("max_steps", 10)

    async def plan(self, query: str, llm_decision: RouteDecision) -> RouteDecision:
        """执行任务规划。

        Args:
            query: 用户查询
            llm_decision: 第2层 LLM 路由的决策结果

        Returns:
            RouteDecision: 路由决策（包含执行计划）
        """
        # 调用 Planner
        start_time = time.time()
        try:
            plan = await asyncio.wait_for(
                self._call_planner(query, llm_decision),
                timeout=self._timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[Planner] Planner 调用超时: {self._timeout_ms}ms")
            # 超时降级：返回 LLM 路由结果
            llm_decision.metadata["planner_timeout"] = True
            return llm_decision
        except Exception as e:
            logger.error(f"[Planner] Planner 调用失败: {e}")
            # 失败降级：返回 LLM 路由结果
            llm_decision.metadata["planner_failed"] = True
            return llm_decision

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[Planner] Planner 调用完成: {elapsed_ms}ms, 步骤数: {len(plan.steps)}")

        # 构建路由决策
        target = self._determine_target(plan)
        decision = RouteDecision(
            target=target,
            confidence=llm_decision.confidence,
            source="planner",
            reason=f"Planner 生成执行计划: {len(plan.steps)} 个步骤",
            complexity="complex",
            metadata={
                "plan": plan.model_dump(),
                "sub_intents": llm_decision.metadata.get("sub_intents", []),
                "entities": llm_decision.metadata.get("entities", {}),
            },
        )

        return decision

    async def plan_with_skills(
        self,
        query: str,
        llm_decision: RouteDecision,
        skill_set: ResolvedSkillSet,
        tenant_id: str,
        time_range: dict[str, Any] | None = None,
        report_date: str | None = None,
        query_lang: str = "zh-CN",
        agent_config: dict[str, Any] | None = None,
    ) -> RouteDecision:
        """Build a deterministic plan from a resolved skill set.

        This additive path preserves the existing LLM-backed ``plan`` method for
        requests that have no resolved domain skill.
        """
        # Lazy import avoids circular import:
        # routers.__init__ -> planner -> planner_adapter -> routers.models
        from agent.langgraph.skills.planner_adapter import (
            SkillPlanContext,
            SkillPlannerAdapter,
        )

        result = SkillPlannerAdapter().build_plan(
            SkillPlanContext(
                user_question=query,
                tenant_id=tenant_id,
                time_range=time_range,
                report_date=report_date,
                query_lang=query_lang,
                skill_set=skill_set,
                agent_config=agent_config or {},
            )
        )
        metadata = {
            "sub_intents": llm_decision.metadata.get("sub_intents", []),
            "entities": llm_decision.metadata.get("entities", {}),
            "skill_id": skill_set.report_skill.skill_id,
        }
        if not result.success:
            metadata["skill_plan_error"] = {
                "reason": result.reason,
                "missing_context": result.missing_context,
            }
            return RouteDecision(
                target=llm_decision.target,
                confidence=llm_decision.confidence,
                source=llm_decision.source,
                reason=f"Skill plan unavailable: {result.reason}",
                complexity=llm_decision.complexity,
                metadata=metadata,
            )

        assert result.plan is not None
        metadata["plan"] = result.plan.model_dump()
        logger.info(
            "[Planner] Skill plan generated: %s, steps: %d",
            result.plan.plan_id,
            len(result.plan.steps),
        )
        return RouteDecision(
            target="hybrid",
            confidence=llm_decision.confidence,
            source="planner",
            reason=f"Skill planner generated execution plan: {len(result.plan.steps)} steps",
            complexity="complex",
            metadata=metadata,
        )

    async def _call_planner(
        self, query: str, llm_decision: RouteDecision
    ) -> ExecutionPlan:
        """调用 Planner 生成执行计划。

        通过 ModelGateway（§5.2）获取 LLM 调用结果，使用项目的统一 LLM 调用链路。
        ModelGateway 替代直接使用 LLMBundle，为后续 model-client 化预留升级路径。

        Args:
            query: 用户查询
            llm_decision: LLM 路由决策

        Returns:
            ExecutionPlan: 执行计划
        """
        from agent.langgraph.gateways.factory import get_gateway_resolver
        from agent.langgraph.routers.prompt_manager import get_prompt_manager
        import json_repair

        # 加载 Prompt 模板
        prompt_manager = get_prompt_manager()
        planner_config = self._config.planner
        tenant_id = planner_config.get("tenant_id", "")
        model_name = planner_config.get("model_id", "qwen3.6-27b")
        prompt_version = planner_config.get("prompt_version", "v1")

        # 构建上下文信息
        context = {
            "sub_intents": llm_decision.metadata.get("sub_intents", []),
            "entities": llm_decision.metadata.get("entities", {}),
            "primary_intent": llm_decision.target,
        }

        # 渲染 Prompt
        prompt = prompt_manager.render_prompt(
            "planner",
            prompt_version,
            query=query,
            context=str(context)
        )

        # 通过 ModelGateway 获取 LLM 调用结果（§5.2 调用点 2）
        # ModelGateway.async_chat 返回 ChatResult，替代 (content, token_count) 元组
        resolver = get_gateway_resolver()
        gateway = await resolver.model_for(tenant_id)

        system_prompt = "你是一个任务规划专家。请严格按照 JSON 格式输出执行计划。"
        messages = [{"role": "user", "content": prompt}]
        gen_conf = {"temperature": 0.1, "max_tokens": 1000}

        result = await gateway.async_chat(
            tenant_id=tenant_id,
            llm_id=model_name,
            system=system_prompt,
            history=messages,
            gen_conf=gen_conf,
            prefer_bundle=False,
        )

        # 检查是否返回错误（§5.2 错误语义对齐：**ERROR** 判断逻辑禁止改动）
        content = result.content
        if content.startswith("**ERROR**"):
            raise RuntimeError(f"LLM 调用失败: {content}")

        # 解析 JSON 响应（json_repair 能容忍格式不严格的 JSON）
        parsed = json_repair.loads(content)

        # 转换为 ExecutionPlan 对象（使用 from_dict 自动将 args dict 转为 StepArgs）
        plan = ExecutionPlan(
            plan_id=parsed.get("plan_id", f"plan_{int(time.time())}"),
            steps=[
                PlanStep.from_dict(step)
                for i, step in enumerate(parsed.get("steps", []))
            ],
            estimated_tokens=parsed.get("estimated_tokens", 1000),
            fallback_strategy=parsed.get("fallback_strategy", "sequential"),
        )

        return plan

    def _determine_target(self, plan: ExecutionPlan) -> str:
        """根据执行计划确定路由目标。

        Args:
            plan: 执行计划

        Returns:
            str: 路由目标
        """
        # 统计各工具的使用情况
        tool_counts = {}
        for step in plan.steps:
            tool_counts[step.tool] = tool_counts.get(step.tool, 0) + 1

        # 如果使用了多个不同工具，判断为 hybrid
        if len(tool_counts) > 1:
            return "hybrid"

        # 如果只使用了一个工具，返回该工具对应的目标
        if len(tool_counts) == 1:
            tool = list(tool_counts.keys())[0]
            return tool

        # 默认返回 chitchat
        return "chitchat"


# 全局实例
_planner_instance: Optional[Planner] = None


def get_planner(config: Optional[IntentRouterConfig] = None) -> Planner:
    """获取 Planner 单例实例。

    Args:
        config: 路由配置

    Returns:
        Planner: Planner 实例
    """
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = Planner(config)
    return _planner_instance
