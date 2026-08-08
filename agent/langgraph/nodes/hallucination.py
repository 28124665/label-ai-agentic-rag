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
"""幻觉检测节点。

验证生成的答案是否被检索文档支持，通过多层架构检测幻觉内容，
并根据忠实度分数执行分级处置。

参考原有实现：
- agent/component/hallucination_detector.py: HallucinationDetector 组件
"""

import logging
import time
from typing import Any

from agent.langgraph.config import (
    EnforcementMode,
    RunMode,
    is_feature_enabled,
    resolve_enforcement,
    resolve_run_mode,
)
from agent.langgraph.gateways.factory import get_gateway_resolver
from agent.langgraph.state import AgentState
from agent.langgraph.evidence.claim import Claim
from agent.langgraph.evidence.snapshot import EvidenceSnapshot
from agent.langgraph.evidence.verdict_matrix import VerdictMatrix
from agent.langgraph.evidence.pair_verifier import PairVerifier, PairVerifierConfig
from agent.langgraph.policy.policy_engine import PolicyEngine

logger = logging.getLogger(__name__)

# 分级处置阈值（参考 HallucinationDetector 组件配置）
PASS_THRESHOLD = 0.85
FILTER_THRESHOLD = 0.6
REGENERATE_THRESHOLD = 0.3

# 防止无限重新生成的上限
MAX_REGENERATES = 2


async def hallucination_node(state: AgentState) -> dict[str, Any]:
    """幻觉检测节点（v2.1 §4.0.2 P0-3 修复版）。

    对 LLM 生成的答案进行幻觉检测，根据忠实度分数执行分级处置：
    - ≥ 0.85（pass）：直接通过，进入 answer_renderer 渲染
    - 0.6 ~ 0.85（filter）：过滤不支持的论断，进入 answer_renderer 渲染
    - 0.3 ~ 0.6（regenerate）：回到 prompt_assembly 重新生成
    - < 0.3（reject）：严重幻觉，直接到 observability 拒答

    regenerate 动作最多允许 MAX_REGENERATES 次，超过后降级为 exhausted
    （走 observability 返回保守答案），避免图循环无限重试。

    P0-3 修复要点（v2.1 §4.0.2）：
    1. 空答案/无 Evidence：ENFORCED 模式 fail-closed 拒答（不再 pass 逃逸）
    2. feature flag 关闭：返回值标注 enforcement_mode（不再无标注 pass）
    3. AST 解析失败：ENFORCED 模式重试/拒答（★禁止降级纯文本逃逸口），
       DISABLED/SHADOW 模式才允许降级纯文本验证

    检测流程：
    1. 解析 run_mode / enforcement_mode（优先用 state 中已注入值，否则从配置解析）
    2. 空答案 → ENFORCED reject("无法生成答案") / DISABLED·SHADOW pass
    3. 无 Evidence → ENFORCED reject("当前资料不足以确认") / DISABLED·SHADOW pass
    4. AST 解析失败（answer_ast 缺失）：
       - ENFORCED + 重试耗尽 → reject("答案结构解析失败")
       - ENFORCED + 未耗尽 → regenerate("AST 解析失败，触发重试")
       - DISABLED/SHADOW → 降级纯文本（继续走 VerifierGateway）
    5. feature flag 关闭 → pass（标注 enforcement_mode）
    6. VerifierGateway 三层验证；异常时 ENFORCED reject / DISABLED·SHADOW 降级字符重叠度

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，含 hallucination_score、hallucination_action、
              enforcement_mode（v2.1 P0-3 修复：统一标注强制级别）
    """
    start_time = time.time()

    # === 1. 解析 run_mode 和 enforcement_mode ===
    # 优先使用 state 中已注入的值（由上游 intent_router 等节点写入），
    # 缺失时从 agent_config + route_target 现场解析，保证本节点独立可用
    agent_config = state.get("agent_config", {}) or {}
    route_target = state.get("route_target", "")
    regenerate_count = state.get("regenerate_count", 0)
    user_question = state.get("user_question", "")
    # 租户 ID（可能为空字符串，由 Gateway 内部处理默认值）
    tenant_id = state.get("tenant_id", "")
    generated_answer = state.get("generated_answer", "")

    run_mode_str = state.get("run_mode", "")
    if run_mode_str:
        try:
            run_mode = RunMode(run_mode_str)
        except ValueError:
            run_mode = resolve_run_mode(route_target, agent_config)
    else:
        run_mode = resolve_run_mode(route_target, agent_config)

    enforcement_mode_str = state.get("enforcement_mode", "")
    if enforcement_mode_str:
        try:
            enforcement_mode = EnforcementMode(enforcement_mode_str)
        except ValueError:
            enforcement_mode = resolve_enforcement(run_mode, agent_config)
    else:
        enforcement_mode = resolve_enforcement(run_mode, agent_config)

    def _base_return(score: float, action: str, **extra: Any) -> dict[str, Any]:
        """构造统一含 enforcement_mode 字段的基础返回值。

        v2.1 P0-3 修复问题 2：所有返回路径均标注 enforcement_mode，
        便于下游节点（answer_renderer / observability）按强制级别分支处理。
        """
        result: dict[str, Any] = {
            "hallucination_score": score,
            "hallucination_action": action,
            "enforcement_mode": enforcement_mode.value,
            "node_timings": {"hallucination": int((time.time() - start_time) * 1000)},
        }
        result.update(extra)
        return result

    # === 2. 空答案处理（P0-3 修复问题 1：fail-closed）===
    if not generated_answer:
        if enforcement_mode == EnforcementMode.ENFORCED:
            # ENFORCED 模式 fail-closed：空答案不可放行给用户
            logger.warning("[hallucination] ENFORCED 模式生成答案为空，fail-closed 拒答")
            return _base_return(0.0, "reject", reject_reason="无法生成答案")
        # DISABLED/SHADOW：legacy 行为，跳过检测直接 pass
        logger.info(f"[hallucination] 生成答案为空，enforcement_mode={enforcement_mode.value}，跳过检测")
        return _base_return(1.0, "pass")

    # === 3. 读取 Evidence（替代 merged_context，v2.1 §4.0.2）===
    # Evidence 是结构化证据列表（含 content/source_type/evidence_id 等），
    # 替代旧的 merged_context 字符串，支持 Claim 级可追溯引用
    evidence = state.get("evidence", []) or []

    if not evidence:
        if enforcement_mode == EnforcementMode.ENFORCED:
            # ENFORCED 模式 fail-closed：无证据不可放行
            logger.warning("[hallucination] ENFORCED 模式无 Evidence，fail-closed 拒答")
            return _base_return(0.0, "reject", reject_reason="当前资料不足以确认")
        # DISABLED/SHADOW：legacy 行为，跳过检测直接 pass
        logger.info(f"[hallucination] 无 Evidence，enforcement_mode={enforcement_mode.value}，跳过检测")
        return _base_return(1.0, "pass")

    # Evidence → context 字符串（VerifierGateway 接口仍需字符串入参，防腐层适配）
    merged_context = "\n\n".join(ev.get("content", "") for ev in evidence if ev.get("content"))

    # === 4. AST 解析失败处理（★ P0-3 关键修复：堵住 ENFORCED 逃逸口）===
    # ENFORCED 模式要求 Claim 级可追溯，answer_ast 缺失表示结构化解析失败。
    # 旧实现直接降级为纯文本验证，是 ENFORCED 模式的逃逸口——
    # 未验证的纯文本会展示给用户，破坏 fail-closed 语义。
    # 修复：ENFORCED 模式下 AST 缺失必须重试或拒答，★禁止 fall through 到降级纯文本
    answer_ast = state.get("answer_ast")
    if enforcement_mode == EnforcementMode.ENFORCED and not answer_ast:
        if regenerate_count >= MAX_REGENERATES:
            # 重试次数耗尽，fail-closed 拒答
            logger.error(f"[hallucination] ENFORCED 模式 AST 解析失败且重试已耗尽 ({regenerate_count}/{MAX_REGENERATES})，fail-closed 拒答")
            return _base_return(0.0, "reject", reject_reason="答案结构解析失败")
        # 未耗尽 → 触发重新生成（回到 prompt_assembly 重新生成结构化答案）
        logger.warning(f"[hallucination] ENFORCED 模式 AST 解析失败，触发重试 ({regenerate_count + 1}/{MAX_REGENERATES})")
        return _base_return(0.0, "regenerate", regenerate_count=regenerate_count + 1)
    # DISABLED/SHADOW：AST 缺失视为正常（legacy 路径无结构化 AST），
    # 降级为纯文本验证（fall through 到下方 VerifierGateway 调用）

    # === 4.5. ENFORCED + AST 可用：使用 PairVerifier 进行 Claim 级三层验证（v3.0）===
    # 当 enforcement_mode 为 ENFORCED 且 answer_ast 可用时，使用 CitationBinder +
    # PairVerifier 进行 batch 三层验证（Rule + NLI + LLM），替代全局 VerifierGateway。
    # 这为每个 Claim 产生独立的 pair_verdicts，支持 VerdictMatrix 细粒度裁决。
    if enforcement_mode == EnforcementMode.ENFORCED and answer_ast:
        try:
            # 从 state 读取 Evidence 列表
            raw_evidence = state.get("evidence", []) or []
            if not raw_evidence:
                # 无证据时 ENFORCED 模式已在上一步 fail-closed，此处不会到达
                logger.warning("[hallucination] ENFORCED+AST 模式无 Evidence，降级走 VerifierGateway")
                return _base_return(0.0, "reject", reject_reason="当前资料不足以确认")

            # 构建 EvidenceSnapshot
            evidence_snapshot = EvidenceSnapshot.build(raw_evidence)

            # 从 state 反序列化 Claim 列表
            raw_claims = state.get("claims", []) or []
            claims = [Claim.from_dict(c) if isinstance(c, dict) else c for c in raw_claims]

            if not claims:
                # 无 Claim 时无法进行 pair 级验证，走原有 VerifierGateway 流程
                logger.info("[hallucination] ENFORCED+AST 模式无 Claim，降级走 VerifierGateway")
                # fall through to step 6
                pass  # 这里的 pass 让流程继续到 VerifierGateway

            else:
                # 构建首次 PairVerifier（batch 三层验证，短路策略）
                llm_id = state.get("llm_id", "")
                if not llm_id:
                    # 尝试从 gateway 解析
                    resolver = get_gateway_resolver()
                    verifier = await resolver.verifier_for(tenant_id)
                    if hasattr(verifier, '_resolve_tenant_llm_id'):
                        llm_id = verifier._resolve_tenant_llm_id(tenant_id)

                if llm_id:
                    pair_verifier = PairVerifier(
                        llm_id=llm_id,
                        tenant_id=tenant_id,
                        config=PairVerifierConfig(batch_size=5),
                    )
                    from agent.langgraph.config import VerificationBudget
                    budget = VerificationBudget()
                    pair_verifier.set_max_llm_calls(budget.max_llm_calls)
                else:
                    pair_verifier = None

                # 构建 CitationBinder（注入 PairVerifier 或 None 使用纯 Rule 层）
                from agent.langgraph.config import VerificationBudget
                from agent.langgraph.evidence.citation_binder import CitationBinder

                budget = VerificationBudget()
                binder = CitationBinder(
                    snapshot=evidence_snapshot,
                    tenant_id=tenant_id,
                    budget=budget,
                    pair_verifier=pair_verifier,
                )

                # 执行异步绑定闭环
                claims = await binder.bind_async(claims, state.get("answer_with_citations", ""))

                # 使用 VerdictMatrix 评估 Claim 级 final_status
                matrix = VerdictMatrix()
                claim_verdicts = matrix.evaluate(claims)

                # 使用 PolicyEngine 决定最终 action
                policy = PolicyEngine()
                policy_result = policy.decide(
                    claims=claims,
                    claim_verdicts=claim_verdicts,
                    enforcement_mode=enforcement_mode.value,
                    regenerate_count=regenerate_count,
                )

                faithfulness_score = policy_result.get("faithfulness_score", 0.0)
                action = policy_result.get("action", "reject")

                # 更新 state 中的 claim_verdicts
                claim_verdict_dicts = [cv.to_dict() if hasattr(cv, 'to_dict') else cv for cv in claim_verdicts]

                logger.info(
                    "[hallucination] ENFORCED+AST 三层验证完成: score=%.4f, "
                    "action=%s, claims=%d, llm_calls=%d",
                    faithfulness_score,
                    action,
                    len(claims),
                    pair_verifier.llm_calls_used if pair_verifier else 0,
                )

                updates = _base_return(faithfulness_score, action, claim_verdicts=claim_verdict_dicts)
                if action == "regenerate":
                    updates["regenerate_count"] = regenerate_count + 1
                return updates

        except Exception as e:
            # ENFORCED 模式：PairVerifier 异常走 fail-closed
            logger.error("[hallucination] ENFORCED+AST 模式 PairVerifier 异常: %s，fail-closed 拒答", e)
            return _base_return(0.0, "reject", reject_reason="验证服务异常")

    # === 5. 配置开关：未启用幻觉检测时直接 pass（标注 enforcement_mode）===
    # P0-3 修复问题 2：返回值统一标注 enforcement_mode，下游可感知强制级别
    if not is_feature_enabled("hallucination"):
        logger.info(f"[hallucination] 幻觉检测未启用（feature flag 关闭），enforcement_mode={enforcement_mode.value}，跳过检测")
        return _base_return(1.0, "pass")

    # === 6. 调用 VerifierGateway 三层验证（规则层 + NLI 层 + LLM 层）===
    try:
        resolver = get_gateway_resolver()
        verifier = await resolver.verifier_for(tenant_id)
        result = await verifier.verify_faithfulness(
            answer=generated_answer,
            context=merged_context,
            query=user_question,
            tenant_id=tenant_id,
        )

        # 从 Gateway 返回值提取忠实度分数
        # Gateway 返回的 action 不直接采用——由本地 _dispose 重新计算，
        # 原因：本地 _dispose 包含 regenerate_count 上限检查，确保不无限重试
        faithfulness_score = float(result.get("faithfulness_score", 0.0))
        gateway_action = result.get("action", "")
        claims_count = len(result.get("claims", []) or [])

        action = _dispose(faithfulness_score, regenerate_count)

        logger.info(
            f"[hallucination] 三层验证完成: score={faithfulness_score:.4f}, "
            f"action={action} (gateway_action={gateway_action}), "
            f"claims={claims_count}, regenerate_count={regenerate_count}, "
            f"enforcement_mode={enforcement_mode.value}"
        )

        updates = _base_return(faithfulness_score, action)
        if action == "regenerate":
            updates["regenerate_count"] = regenerate_count + 1
        return updates

    except Exception as e:
        # VerifierGateway 调用失败（组件构造/LLM 解析/三层验证任意环节异常），
        # 按 enforcement_mode 分级处置（v2.1 P0-3：ENFORCED 不可降级）
        logger.warning(f"[hallucination] VerifierGateway 调用失败: {e}, enforcement_mode={enforcement_mode.value}")

        if enforcement_mode == EnforcementMode.ENFORCED:
            # ENFORCED 模式 fail-closed：验证器异常不可降级为纯文本，
            # 否则未验证内容会展示给用户，破坏 Claim 级可追溯保证
            logger.error("[hallucination] ENFORCED 模式验证器异常，fail-closed 拒答")
            return _base_return(0.0, "reject", reject_reason="验证服务异常")

        # DISABLED/SHADOW：降级为字符重叠度评估，保证主流程不中断
        try:
            faithfulness_score = await _verify_faithfulness(
                answer=generated_answer,
                context=merged_context,
                query=user_question,
            )

            action = _dispose(faithfulness_score, regenerate_count)

            logger.info(f"[hallucination] 降级检测完成: score={faithfulness_score:.4f}, action={action}, regenerate_count={regenerate_count}")

            updates = _base_return(faithfulness_score, action)
            if action == "regenerate":
                updates["regenerate_count"] = regenerate_count + 1
            return updates

        except Exception as fallback_err:
            # 降级路径也失败时直接放行（ENFORCED 已在上面拦截，此处仅 DISABLED/SHADOW）
            logger.error(f"[hallucination] 降级检测异常: {fallback_err}")
            return _base_return(1.0, "pass")


async def _verify_faithfulness(answer: str, context: str, query: str) -> float:
    """验证答案的忠实度。

    参考 HallucinationDetector 的多层验证架构：
    1. 规则层：精确校验数值/日期/专有名词
    2. NLI 层：自然语言推理判断
    3. LLM 层：语义支持度判断

    当前简化实现：基于关键词重叠度计算忠实度分数。

    Args:
        answer: 生成的答案
        context: 检索上下文
        query: 用户问题

    Returns:
        float: 忠实度分数 0.0 ~ 1.0
    """
    if not answer or not context:
        return 0.0

    answer_chars = set(answer)
    context_chars = set(context)

    if not answer_chars:
        return 0.0

    overlap = answer_chars & context_chars
    overlap_ratio = len(overlap) / len(answer_chars) if answer_chars else 0.0

    answer_len = len(answer)
    context_len = len(context)

    length_penalty = 1.0
    if context_len > 0 and answer_len > context_len * 2:
        length_penalty = 0.8

    return min(1.0, overlap_ratio * length_penalty)


def _dispose(faithfulness_score: float, regenerate_count: int) -> str:
    """根据忠实度分数执行分级处置。

    Args:
        faithfulness_score: 忠实度分数
        regenerate_count: 已重新生成次数

    Returns:
        str: 处置动作（pass / filter / regenerate / exhausted / reject）
    """
    if faithfulness_score >= PASS_THRESHOLD:
        return "pass"
    elif faithfulness_score >= FILTER_THRESHOLD:
        return "filter"
    elif faithfulness_score >= REGENERATE_THRESHOLD:
        if regenerate_count >= MAX_REGENERATES:
            logger.warning(f"[hallucination] 重新生成次数已达上限 {MAX_REGENERATES}，降级为 exhausted")
            return "exhausted"
        return "regenerate"
    else:
        return "reject"


def hallucination_decision(state: AgentState) -> str:
    """幻觉检测条件路由函数，用于 LangGraph 条件边（v2.1 §4.0.2 路由映射更新）。

    路由映射（v2.1 P0-3 修复后）：
    - pass / filter → "answer_renderer"：进入结构化答案渲染节点，
      仅输出 final_status == "supported" 的 Claim（fail-closed）
    - reject / exhausted → "observability"：直接到可观测性节点，
      跳过渲染（reject 无可信内容，exhausted 已达重试上限）
    - regenerate → "prompt_assembly"：回到提示词组装重新生成

    设计决策：
    - pass/filter 不再直连 observability，而是先经 answer_renderer 渲染，
      确保 ENFORCED 模式下未验证 Claim 不泄露给用户（v2.1 §3.2）
    - reject/exhausted 直连 observability（跳过渲染），observability 再到
      answer_output 输出保守答案/拒答文案

    Args:
        state: 当前 AgentState

    Returns:
        str: 下一个节点名称（映射键：answer_renderer / observability / prompt_assembly）
    """
    action = state.get("hallucination_action", "pass")

    if action in ("pass", "filter"):
        # pass/filter → 渲染节点（v2.1 §3.2：结构化答案渲染，过滤未验证 Claim）
        return "answer_renderer"
    elif action == "regenerate":
        # regenerate → 回到提示词组装重新生成
        return "prompt_assembly"
    elif action in ("reject", "exhausted"):
        # reject/exhausted → 直接到可观测性（跳过渲染，输出拒答/保守答案）
        return "observability"
    else:
        # 兜底：未知 action 走 observability，避免阻塞主流程
        return "observability"
