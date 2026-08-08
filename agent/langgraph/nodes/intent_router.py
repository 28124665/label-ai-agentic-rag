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
"""意图路由节点（三层递进架构 + Skill 路由主链路接线）。

实现三层递进路由：
- 第0层：前置过滤（安全拦截、精确指令、问候语、实体格式）
- 第0.5层：复杂度闸门（正则检测结构特征，复杂问题跳过规则路由）
- 第1层：规则路由（Tier1 强模式 0.80 + Tier2 弱关键词封顶 0.55）
- 第2层：LLM 语义路由（结构化输出 + 降级机制）
- 第3层：Planner 复杂任务规划（DAG 执行计划）

v1.1 §11.2 P0 主链路接线：
    所有返回路径经统一 ``_finalize_route()`` 执行 Skill 解析。
    Skill 命中（非 fallback）后调用 ``planner.plan_with_skills()``，
    生成受控 ExecutionPlan，写入 state.execution_plan。

与 intent_router 四层递进的协同（§10.9.3 优先级矩阵）：
    - 精确指令（@database/@rag/@web）优先于 Skill 路由（约束1）
    - chitchat 路径不做 Skill 路由（§10.8 no-skill 分支）
    - Tier2 弱关键词不作为 Skill 强信号（约束2）
    - Skill 路由结果不覆盖 route_target（约束6，Skill 与工具层路由正交）

根据用户问题判断意图，路由到不同的工具（RAG / Database / Web / Chitchat）。
"""

import asyncio
import logging
import time
from typing import Any

from agent.langgraph.routers.complexity_gate import get_complexity_gate
from agent.langgraph.routers.config_loader import IntentRouterConfig, load_config
from agent.langgraph.routers.llm_router import get_llm_router
from agent.langgraph.routers.models import ExecutionPlan, RouteDecision
from agent.langgraph.routers.planner import get_planner
from agent.langgraph.routers.pre_filter import get_pre_filter
from agent.langgraph.routers.rule_router import get_rule_router
from agent.langgraph.skills.evidence_adapter import SkillEvidenceAdapter
from agent.langgraph.skills.models import SkillResolveContext, SkillResolveResult
from agent.langgraph.skills.registry import SkillRegistry
from agent.langgraph.skills.resolver import SkillResolver
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)

# ========== SkillResolver 单例（避免每次请求重新加载 YAML）==========
# SkillRegistry.__init__ 会全量加载 YAML，需全局缓存（§15.2 性能与容量）
_skill_resolver_instance: SkillResolver | None = None


def get_skill_resolver() -> SkillResolver:
    """获取 SkillResolver 单例实例。

    类比 Java 中的 ``@Component`` 单例注入：
        ``SkillResolver`` ≈ ``@Service``，``SkillRegistry`` ≈ ``@Repository``，
        通过工厂方法实现单例，避免每次请求重新加载 19+ 个 YAML。

    P1 扩展（§18.3）：
        从 registry snapshot 获取 BM25 索引并注入 ``SkillResolver``，
        使 keyword 路径使用 BM25 混合召回替代纯关键词匹配。
    """
    global _skill_resolver_instance
    if _skill_resolver_instance is None:
        registry = SkillRegistry()
        # 从 registry snapshot 获取构建好的 BM25 索引
        bm25_retriever = None
        try:
            snapshot = registry.current_snapshot
            if snapshot.bm25_index is not None:
                from agent.langgraph.skills.retrievers import BM25Retriever

                retriever = BM25Retriever()
                retriever._index = snapshot.bm25_index
                bm25_retriever = retriever
        except Exception:
            logger.info(
                "[SkillResolver] BM25 索引不可用，降级为纯关键词匹配"
            )
        _skill_resolver_instance = SkillResolver(
            registry=registry,
            bm25_retriever=bm25_retriever,
        )
    return _skill_resolver_instance


def _resolve_skill(
    route_decision: RouteDecision,
    state: AgentState,
) -> SkillResolveResult | None:
    """执行 Skill 解析（§11.2 主链路接线）。

    将 RouteDecision 转换为 SkillResolveContext，调用 SkillResolver.resolve()。
    失败时返回 None，不阻塞主链路路由。

    Args:
        route_decision: 当前路由决策（含 metadata.report_type 等强信号）
        state: 当前 AgentState（提供 user_question/tenant_id/agent_config）

    Returns:
        SkillResolveResult 或 None（异常时）
    """
    try:
        resolver = get_skill_resolver()
        context = SkillResolveContext(
            user_question=state.get("user_question", ""),
            tenant_id=state.get("tenant_id", ""),
            route_decision=route_decision.model_dump() if route_decision else None,
            agent_config=state.get("agent_config"),
        )
        return resolver.resolve(context)
    except Exception as e:
        logger.warning(f"[intent_router] Skill 解析失败: {e}", exc_info=True)
        return None


async def _finalize_route(
    route_decision: RouteDecision,
    state: AgentState,
    config: IntentRouterConfig,
    start_time: float,
) -> dict[str, Any]:
    """统一返回路径：执行 Skill 解析 + plan_with_skills（§11.2）。

    所有非空、非澄清返回路径必须经此函数，避免短路绕过 Skill Resolver。
    流程：
        1. 构建 base result（route_target/route_decision/node_timings）
        2. chitchat / 精确指令 → 跳过 Skill 路由（§10.8 + §10.9.3 约束1）
        3. Skill 解析 → 写入 skill_resolution（始终写入，便于审计）
        4. Skill 命中（非 fallback）→ plan_with_skills + 写入 execution_plan/skill_set/skill_evidence_requirements
        5. plan_with_skills 成功 → 用 planned_decision 替换 route_decision

    约束：
        - Skill fallback 不触发 plan_with_skills（§10.8：fallback 不等于命中）
        - plan_with_skills 失败时保留原 route_decision，不阻塞主链路
        - route_target 由 planned_decision.target 决定（plan_with_skills 可能改为 hybrid）

    Args:
        route_decision: 当前路由决策
        state: 当前 AgentState
        config: 路由配置（供 get_planner 使用）
        start_time: 节点起始时间戳

    Returns:
        dict: 完整的状态更新字段
    """
    result: dict[str, Any] = {
        "route_target": route_decision.target,
        "route_decision": route_decision,
        "node_timings": {"intent_router": int((time.time() - start_time) * 1000)},
    }

    # ===== 跳过 Skill 路由的场景 =====

    # chitchat 不需要 Skill（§10.8 no-skill 分支）
    if route_decision.target == "chitchat":
        return result

    # 精确指令（@database/@rag/@web）优先于 Skill 路由（§10.9.3 约束1）
    # DataSkill/RetrievalSkill 仍可在工具节点内复用，但不走 plan_with_skills
    if route_decision.metadata and route_decision.metadata.get("directive"):
        return result

    # ===== Skill 解析（始终执行，写入 skill_resolution 供审计）=====
    skill_result = _resolve_skill(route_decision, state)
    if skill_result is not None:
        result["skill_resolution"] = skill_result.model_dump()

    # ===== Skill 命中（非 fallback）→ plan_with_skills =====
    # 约束：fallback_used=True 时不触发 plan_with_skills（§10.8 fallback ≠ 命中）
    if not (
        skill_result
        and skill_result.resolved
        and not skill_result.fallback_used
        and skill_result.skill_set is not None
    ):
        return result

    skill_set = skill_result.skill_set
    result["skill_set"] = skill_set.model_dump()

    # 收集 Evidence 要求（供 evidence_fusion 按 source_quota 截断 + answerability 检查）
    try:
        evidence_reqs = SkillEvidenceAdapter().collect_requirements(skill_set)
        result["skill_evidence_requirements"] = [
            req.model_dump() for req in evidence_reqs
        ]
    except Exception as e:
        logger.warning(f"[intent_router] Skill Evidence 要求收集失败: {e}")
        result["skill_evidence_requirements"] = []

    # 调用 plan_with_skills 生成受控 ExecutionPlan
    try:
        planner = get_planner(config)
        planned_decision = await planner.plan_with_skills(
            query=state.get("user_question", ""),
            llm_decision=route_decision,
            skill_set=skill_set,
            tenant_id=state.get("tenant_id", ""),
            query_lang=state.get("query_lang", "zh_CN"),
            agent_config=state.get("agent_config"),
        )

        # 从 metadata 提取 ExecutionPlan 对象
        # plan_with_skills 成功时 metadata["plan"] = ExecutionPlan.model_dump()
        # 失败时 metadata["skill_plan_error"] 存在，metadata["plan"] 不存在
        plan_dict = planned_decision.metadata.get("plan") if planned_decision.metadata else None
        if plan_dict and isinstance(plan_dict, dict):
            result["execution_plan"] = ExecutionPlan.model_validate(plan_dict)

        # 用 planned_decision 替换 route_decision
        # plan_with_skills 可能将 target 改为 hybrid（含 data+rag 步骤）
        result["route_decision"] = planned_decision
        result["route_target"] = planned_decision.target

        logger.info(
            f"[intent_router] Skill plan_with_skills 完成: "
            f"skill_id={skill_set.report_skill.skill_id}, "
            f"target={planned_decision.target}, "
            f"has_plan={'execution_plan' in result}"
        )
    except Exception as e:
        # plan_with_skills 失败不阻塞主链路，保留原 route_decision
        logger.warning(
            f"[intent_router] plan_with_skills 失败，保留原路由决策: {e}",
            exc_info=True,
        )

    return result


async def intent_router_node(state: AgentState) -> dict[str, Any]:
    """意图路由节点（三层递进架构）。

    四层递进调用链：
    1. 第0层：前置过滤（< 1ms）—— 安全拦截、精确指令、问候语、实体格式
    2. 第0.5层：复杂度闸门（< 1ms）—— 正则检测结构特征，复杂问题跳过规则路由
    3. 第1层：规则路由（< 10ms）—— Tier1 强模式 0.80 / Tier2 弱关键词封顶 0.55
    4. 第2层：LLM 语义路由（< 300ms）—— 规则置信度 < 0.7 或复杂度闸门判定复杂
    5. 第3层：Planner 复杂任务规划（< 5s）—— LLM 判断为复杂任务

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
        return {
            "route_target": "chitchat",
            "route_decision": default_decision,
            "node_timings": {"intent_router": int((time.time() - start_time) * 1000)},
        }

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
    # 安全拦截、精确指令（@database/@rag/@web）、问候语、实体格式
    # 精确指令在复杂度闸门之前执行，尊重用户显式意图
    pre_filter = get_pre_filter()
    pre_filter_decision = pre_filter.filter(user_question)

    if pre_filter_decision:
        logger.info(
            f"[intent_router] 第0层前置过滤命中: '{user_question}' -> "
            f"{pre_filter_decision.target} (confidence={pre_filter_decision.confidence:.2f}, "
            f"reason={pre_filter_decision.reason})"
        )
        # ★ v1.1 §11.2 P0：经统一返回路径执行 Skill 解析
        # 精确指令（directive=True）在 _finalize_route 内跳过 Skill 路由（§10.9.3 约束1）
        return await _finalize_route(pre_filter_decision, state, config, start_time)

    # ========== 第0.5层：复杂度闸门 ==========
    # 正则检测问题结构特征（长查询、多子句、多问号、推理标记、多工具、递进结构）
    # 复杂问题跳过规则路由，直接进入 LLM 语义路由，避免规则路由误拦截
    complexity_gate = get_complexity_gate(config)
    complexity_result = complexity_gate.check(user_question)

    if complexity_result.is_complex:
        logger.info(
            f"[intent_router] 第0.5层复杂度闸门判定复杂: "
            f"'{user_question[:50]}' -> signals={complexity_result.triggered_signals}, "
            f"跳过规则路由直接进入 LLM 路由"
        )
        # 构造规则路由占位决策，标记被复杂度闸门跳过
        # confidence=0.0 确保后续逻辑进入 LLM 路由
        rule_decision = RouteDecision(
            target="chitchat",
            confidence=0.0,
            source="rule",
            reason=f"复杂度闸门跳过规则路由: {complexity_result.triggered_signals}",
            complexity="complex",
            metadata={
                "complexity_gate_skipped": True,
                "complexity_signals": complexity_result.triggered_signals,
                "complexity_confidence": complexity_result.confidence,
            },
        )
    else:
        # ========== 第1层：规则路由 ==========
        # 简单问题走规则路由：Tier1 强模式 0.80 / Tier2 弱关键词封顶 0.55
        rule_router = get_rule_router(config)
        rule_decision = rule_router.route(user_question)

        logger.info(
            f"[intent_router] 第1层规则路由: '{user_question}' -> "
            f"{rule_decision.target} (confidence={rule_decision.confidence:.2f}, "
            f"reason={rule_decision.reason})"
        )

        # 如果规则路由置信度 >= 0.7（仅 Tier1 强模式可达），直接返回
        rule_to_llm_threshold = config.thresholds.get("rule_to_llm", 0.7)
        if rule_decision.confidence >= rule_to_llm_threshold:
            logger.info(
                f"[intent_router] 规则路由置信度 >= {rule_to_llm_threshold}，直接返回"
            )
            # ★ v1.1 §11.2 P0：经统一返回路径执行 Skill 解析
            # Tier1 强模式（≥0.80）允许 Skill alias 作为强信号（§10.9.3 约束2）
            # 若 Skill 命中（非 fallback），plan_with_skills 替换为受控执行计划
            return await _finalize_route(rule_decision, state, config, start_time)

    # ========== 第2层：LLM 语义路由 ==========
    # 触发条件：
    #   - 复杂度闸门判定复杂（rule_decision.confidence=0.0）
    #   - 规则路由 Tier2 弱关键词匹配（confidence < 0.7）
    llm_router = get_llm_router(config)
    llm_decision = await llm_router.route(user_question, rule_decision)

    # 将复杂度闸门的信号注入到 LLM 决策的 metadata 中，供后续 Planner 使用
    if complexity_result.is_complex and llm_decision.metadata is not None:
        llm_decision.metadata["complexity_signals"] = (
            complexity_result.triggered_signals
        )

    logger.info(
        f"[intent_router] 第2层LLM路由: '{user_question}' -> "
        f"{llm_decision.target} (confidence={llm_decision.confidence:.2f}, "
        f"reason={llm_decision.reason})"
    )

    # 检查是否需要用户澄清
    if llm_decision.metadata.get("needs_clarification"):
        logger.info("[intent_router] LLM 判断需要用户澄清")
        # 暂时返回 LLM 决策，后续可以在前端实现澄清交互
        return {
            "route_target": llm_decision.target,
            "route_decision": llm_decision,
            "node_timings": {"intent_router": int((time.time() - start_time) * 1000)},
        }

    # 如果复杂度不是 complex，直接返回
    if llm_decision.complexity != "complex":
        logger.info("[intent_router] LLM 判断为非复杂任务，直接返回")
        # ★ v1.1 §11.2 P0：经统一返回路径执行 Skill 解析
        return await _finalize_route(llm_decision, state, config, start_time)

    # ========== 第3层：Planner 复杂任务规划 ==========
    # §10.9.2：复杂任务也需 Skill 语义召回 + Reranker → plan_with_skills
    # 先执行 Skill 解析，命中（非 fallback）则用 plan_with_skills 替代自由 plan()
    pre_skill_result = _resolve_skill(llm_decision, state)
    if (
        pre_skill_result
        and pre_skill_result.resolved
        and not pre_skill_result.fallback_used
        and pre_skill_result.skill_set is not None
    ):
        # Skill 命中 → _finalize_route 内调用 plan_with_skills
        logger.info(
            f"[intent_router] 第3层 Skill 命中: "
            f"skill_id={pre_skill_result.skill_set.report_skill.skill_id}, "
            f"使用 plan_with_skills 替代自由 plan()"
        )
        return await _finalize_route(llm_decision, state, config, start_time)

    # 无 Skill → 自由 plan()
    planner = get_planner(config)
    planner_decision = await planner.plan(user_question, llm_decision)

    logger.info(
        f"[intent_router] 第3层Planner: '{user_question}' -> "
        f"{planner_decision.target} (confidence={planner_decision.confidence:.2f}, "
        f"reason={planner_decision.reason})"
    )

    # ★ v1.1 §11.2 P0：经统一返回路径（写入 skill_resolution 供审计，不再触发 plan_with_skills）
    result = await _finalize_route(planner_decision, state, config, start_time)
    # 覆盖 skill_resolution：使用 Planner 前已解析的结果（避免 _finalize_route 重复解析）
    if pre_skill_result is not None:
        result["skill_resolution"] = pre_skill_result.model_dump()
    return result


def route_decision(state: AgentState) -> str:
    """路由决策函数，用于 LangGraph 条件边。

    路由优先级：
    1. needs_clarification → clarification（主动澄清交互）
    2. Planner 复杂任务 → plan_executor（并行执行 DAG 计划）
    3. RAG/Hybrid → rag_tool
    4. Database → db_tool
    5. Chitchat/Web → prompt_assembly

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

    # ★ Planner 生成的复杂任务走计划执行器（支持多工具并行）
    if (
        route_decision_data
        and route_decision_data.source == "planner"
        and route_decision_data.complexity == "complex"
    ):
        return "plan_executor"

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
    其他模式直接进入 evidence_fusion（v2.1 P0-1 修复：所有工具产出必须经
    Evidence Fusion 构建 snapshot，不再跳过融合直连 reflection）。

    Returns:
        str: ``"db_tool"``（hybrid 串行）或 ``"evidence_fusion"``（其他模式）
    """
    if state.get("route_target") == "hybrid":
        return "db_tool"
    return "evidence_fusion"
