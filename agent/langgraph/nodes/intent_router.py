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
"""意图路由节点（三层递进架构）。

实现三层递进路由：
- 第0层：前置过滤（安全拦截、显式指令、问候语、实体格式）
- 第1层：规则路由（关键词匹配 + 置信度打分）
- 第2层：LLM 语义路由（结构化输出 + 降级机制）
- 第3层：Planner 复杂任务规划（DAG 执行计划）

根据用户问题判断意图，路由到不同的工具（RAG / Database / Web / Chitchat）。
"""

import logging
import time
from typing import Any

from agent.langgraph.routers.config_loader import load_config
from agent.langgraph.routers.llm_router import get_llm_router
from agent.langgraph.routers.models import ExecutionPlan, PlanStep, RouteDecision
from agent.langgraph.routers.planner import get_planner
from agent.langgraph.routers.pre_filter import get_pre_filter
from agent.langgraph.routers.rule_router import get_rule_router
from agent.langgraph.skills import SkillRegistry, SkillResolver
from agent.langgraph.skills.evidence_adapter import SkillEvidenceAdapter
from agent.langgraph.skills.models import SkillResolveContext
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


async def intent_router_node(state: AgentState) -> dict[str, Any]:
    """意图路由节点（三层递进架构）。

    三层递进调用链：
    1. 第0层：前置过滤（< 1ms）
    2. 第1层：规则路由（< 10ms）
    3. 第2层：LLM 语义路由（< 300ms，仅当规则路由置信度 < 0.7）
    4. 第3层：Planner 复杂任务规划（< 5s，仅当 LLM 判断为复杂任务）

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段（route_target + route_decision）
    """
    start_time = time.time()
    
    user_question = state.get("user_question", "")

    if not user_question:
        logger.warning("[intent_router] 用户问题为空，路由到 chitchat")
        default_decision = RouteDecision(
            target="chitchat",
            confidence=0.5,
            source="rule",
            reason="用户问题为空",
            complexity="simple",
            metadata={},
        )
        return await _router_response(
            state, default_decision, start_time, react_enabled=False, db_tool_enabled=True
        )

    # 读取能力开关（每会话可独立控制）
    # - react.enabled: 是否启用受限 ReAct 子图（默认 False，向后兼容）
    # - db_tool.enabled: 是否启用数据库工具（默认 True，向后兼容）
    # 语义：开关为 True 时，路由逻辑决定是否使用；为 False 时该能力不可用
    agent_config = state.get("agent_config", {}) or {}
    react_cfg = agent_config.get("react", {}) or {}
    react_enabled = bool(react_cfg.get("enabled", False))

    db_tool_cfg = agent_config.get("db_tool", {}) or {}
    db_tool_enabled = bool(db_tool_cfg.get("enabled", True))

    logger.info(
        f"[intent_router] 能力开关: react_enabled={react_enabled}, db_tool_enabled={db_tool_enabled}"
    )
    
    # 加载配置
    config = load_config()
    
    # 从 state 中提取 tenant_id，注入到路由器配置中
    # 优先使用 state 中的 tenant_id，其次使用 agent_config 中的 tenant_id
    tenant_id = state.get("tenant_id", "")
    if not tenant_id:
        agent_config = state.get("agent_config", {})
        tenant_id = agent_config.get("tenant_id", "")
    
    # 将 tenant_id 注入到 LLM 路由器和 Planner 配置中
    if tenant_id:
        config.llm_router["tenant_id"] = tenant_id
        config.planner["tenant_id"] = tenant_id
        logger.info(f"[intent_router] 注入 tenant_id: {tenant_id}")
    
    # ========== 第0层：前置过滤 ==========
    pre_filter = get_pre_filter()
    pre_filter_decision = pre_filter.filter(user_question)
    
    if pre_filter_decision:
        logger.info(
            f"[intent_router] 第0层前置过滤命中: '{user_question}' -> "
            f"{pre_filter_decision.target} (confidence={pre_filter_decision.confidence:.2f}, "
            f"reason={pre_filter_decision.reason})"
        )
        return await _router_response(
            state, pre_filter_decision, start_time, react_enabled, db_tool_enabled
        )
    
    # ========== 第1层：规则路由 ==========
    rule_router = get_rule_router(config)
    rule_decision = rule_router.route(user_question)
    
    logger.info(
        f"[intent_router] 第1层规则路由: '{user_question}' -> "
        f"{rule_decision.target} (confidence={rule_decision.confidence:.2f}, "
        f"reason={rule_decision.reason})"
    )
    
    # 如果规则路由置信度 >= 0.7，直接返回
    rule_to_llm_threshold = config.thresholds.get("rule_to_llm", 0.7)
    if rule_decision.confidence >= rule_to_llm_threshold:
        logger.info(
            f"[intent_router] 规则路由置信度 >= {rule_to_llm_threshold}，直接返回"
        )
        return await _router_response(
            state, rule_decision, start_time, react_enabled, db_tool_enabled
        )
    
    # ========== 第2层：LLM 语义路由 ==========
    llm_router = get_llm_router(config)
    llm_decision = await llm_router.route(user_question, rule_decision)
    
    logger.info(
        f"[intent_router] 第2层LLM路由: '{user_question}' -> "
        f"{llm_decision.target} (confidence={llm_decision.confidence:.2f}, "
        f"reason={llm_decision.reason})"
    )
    
    # 检查是否需要用户澄清
    if llm_decision.metadata.get("needs_clarification"):
        logger.info("[intent_router] LLM 判断需要用户澄清")
        # 暂时返回 LLM 决策，后续可以在前端实现澄清交互
        return await _router_response(
            state, llm_decision, start_time, react_enabled, db_tool_enabled
        )

    # 如果复杂度不是 complex，直接返回
    if llm_decision.complexity != "complex":
        logger.info("[intent_router] LLM 判断为非复杂任务，直接返回")
        return await _router_response(
            state, llm_decision, start_time, react_enabled, db_tool_enabled
        )
    
    # ========== 第3层：Planner 复杂任务规划 ==========
    planner = get_planner(config)
    planner_decision = await planner.plan(user_question, llm_decision)
    
    logger.info(
        f"[intent_router] 第3层Planner: '{user_question}' -> "
        f"{planner_decision.target} (confidence={planner_decision.confidence:.2f}, "
        f"reason={planner_decision.reason})"
    )
    
    return await _router_response(
        state, planner_decision, start_time, react_enabled, db_tool_enabled
    )


async def _router_response(
    state: AgentState,
    decision: RouteDecision,
    start_time: float,
    react_enabled: bool,
    db_tool_enabled: bool,
) -> dict[str, Any]:
    """Attach skill runtime state without changing ordinary routing flows."""
    updates: dict[str, Any] = {
        "route_target": decision.target,
        "route_decision": decision,
        "react_enabled": react_enabled,
        "db_tool_enabled": db_tool_enabled,
    }
    if _should_resolve_skill(state, decision):
        registry = SkillRegistry()
        resolution = SkillResolver(registry).resolve(
            SkillResolveContext(
                user_question=state.get("user_question", ""),
                tenant_id=state.get("tenant_id", ""),
                skill_id=_skill_hint(state, decision, "skill_id"),
                report_type=_skill_hint(state, decision, "report_type"),
                route_decision=decision.model_dump(),
                agent_config=state.get("agent_config", {}) or {},
            )
        )
        updates["skill_resolution"] = resolution.model_dump()
        if resolution.resolved and resolution.skill_set is not None:
            skill_set = resolution.skill_set
            updates["skill_set"] = skill_set.model_dump()
            updates["skill_evidence_requirements"] = [
                requirement.model_dump()
                for requirement in SkillEvidenceAdapter().collect_requirements(skill_set)
            ]
            if _needs_skill_plan(decision):
                planned = await get_planner().plan_with_skills(
                    state.get("user_question", ""),
                    decision,
                    skill_set,
                    state.get("tenant_id", ""),
                    time_range=state.get("time_range"),
                    report_date=state.get("report_date"),
                    query_lang=state.get("query_lang", "zh_CN"),
                    agent_config=state.get("agent_config", {}) or {},
                )
                decision = planned
                updates["route_target"] = planned.target
                updates["route_decision"] = planned
                plan = planned.metadata.get("plan")
                if isinstance(plan, dict):
                    updates["execution_plan"] = ExecutionPlan(
                        plan_id=plan.get("plan_id", ""),
                        steps=[PlanStep.from_dict(item) for item in plan.get("steps", [])],
                        estimated_tokens=plan.get("estimated_tokens", 0),
                        fallback_strategy=plan.get("fallback_strategy", "default"),
                    )
    updates["node_timings"] = {
        "intent_router": int((time.time() - start_time) * 1000)
    }
    return updates


def _should_resolve_skill(state: AgentState, decision: RouteDecision) -> bool:
    """Return whether route context explicitly indicates a skill-guided report."""
    metadata = decision.metadata or {}
    if any(
        _skill_hint(state, decision, key)
        for key in ("report_type", "skill_id")
    ):
        return True
    route_text = " ".join(
        str(value)
        for value in (
            state.get("user_question", ""),
            decision.target,
            decision.reason,
            metadata.get("intent", ""),
        )
    ).casefold()
    return (
        decision.complexity == "complex"
        or any(keyword in route_text for keyword in ("report", "报告", "skill", "技能"))
    )


def _needs_skill_plan(decision: RouteDecision) -> bool:
    """Plan resolved report skills and complex routed requests deterministically."""
    return decision.complexity == "complex" or "report" in (
        f"{decision.target} {decision.reason}".casefold()
    ) or "报告" in decision.reason


def _skill_hint(state: AgentState, decision: RouteDecision, key: str) -> str | None:
    """Read explicit skill selection from state first, then route metadata."""
    value = state.get(key)
    if isinstance(value, str) and value:
        return value
    value = (decision.metadata or {}).get(key)
    return value if isinstance(value, str) and value else None


def route_decision(state: AgentState) -> str:
    """路由决策函数，用于 LangGraph 条件边。

    路由优先级：
    1. needs_clarification → clarification（主动澄清交互）
    2. ReAct 子图启用 + Planner 标记为 react → react_subgraph（受限多步推理）
    3. Planner 复杂任务 → plan_executor（并行执行 DAG 计划）
    4. RAG/Hybrid → rag_tool（hybrid 模式下 rag_tool 完成后串行 db_tool）
    5. Database → db_tool
    6. Chitchat/Web → prompt_assembly

    能力开关的影响：
    - db_tool_enabled=False:
        * route_target=database → 降级为 rag_tool（用知识库替代数据库）
        * route_target=hybrid → 降级为 rag_tool（仅做 RAG，不串行 db_tool）
    - react_enabled=False:
        * 永远不进 react_subgraph（即使 Planner 标记为 react_planner）

    Args:
        state: 当前 AgentState

    Returns:
        str: 下一个节点名称
    """
    route_target = state.get("route_target", "chitchat")
    route_decision_data = state.get("route_decision")

    # ★ 优先检查是否需要用户澄清（confidence < 0.6）
    if (
        route_decision_data
        and route_decision_data.metadata
        and route_decision_data.metadata.get("needs_clarification")
    ):
        return "clarification"

    # ★ ReAct 子图启用 + Planner 标记为 react 时进入受限 ReAct 子图
    if (
        state.get("react_enabled", False)
        and route_decision_data
        and route_decision_data.source == "react_planner"
        and route_decision_data.complexity == "complex"
    ):
        logger.info("[route_decision] 复杂任务路由到 react_subgraph")
        return "react_subgraph"

    # ★ Planner 生成的复杂任务走计划执行器（支持多工具并行）
    if (
        route_decision_data
        and route_decision_data.source == "planner"
        and route_decision_data.complexity == "complex"
    ):
        return "plan_executor"

    # ★ db_tool 禁用时的降级路由
    if not state.get("db_tool_enabled", True):
        if route_target == "database":
            logger.info(
                "[route_decision] db_tool 已禁用，database 路由降级为 rag_tool"
            )
            return "rag_tool"
        if route_target == "hybrid":
            logger.info(
                "[route_decision] db_tool 已禁用，hybrid 路由降级为 rag_tool（仅 RAG）"
            )
            return "rag_tool"

    if route_target == "rag":
        return "rag_tool"
    elif route_target == "database":
        return "db_tool"
    elif route_target == "hybrid":
        # hybrid：先 RAG，再由 after_rag_tool 串行进入 db_tool
        return "rag_tool"
    else:
        # chitchat 或其他 -> 直接到 prompt_assembly
        return "prompt_assembly"


def after_rag_tool(state: AgentState) -> str:
    """rag_tool 完成后的条件路由。

    hybrid 模式下串行进入 db_tool，确保 Database 分支不被丢弃；
    但当 db_tool 被禁用时，hybrid 退化为单 RAG，直接进入 reflection。
    其他模式直接进入 reflection。
    """
    if state.get("route_target") == "hybrid" and state.get("db_tool_enabled", True):
        return "db_tool"
    if state.get("route_target") == "hybrid" and not state.get("db_tool_enabled", True):
        logger.info("[after_rag_tool] db_tool 已禁用，hybrid 跳过 db_tool，直接进入 reflection")
    return "reflection"
