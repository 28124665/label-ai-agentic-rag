"""Stage 6 决策策略（P2，§10.8）。

设计文档 §10.8 决策策略：
    根据 Reranker 输出的 SkillRankDecision 做最终路由决策。
    决策类型：select / clarify / no-skill / fallback / composition

设计文档 §10.8.1 阈值初始值：
    absolute_threshold = 0.75（Top1 绝对置信度门槛）
    margin_threshold = 0.15（Top1 与 Top2 最小分差）
    composition_confidence = 0.70（组合 Skill 最低置信度）
    critical_absolute_threshold = 0.85（critical Skill 硬编码）
    no_skill_confidence = 0.40（低于此值判定 no-skill）

设计文档 §10.8 约束：
    - critical Skill 不得只凭低置信度语义匹配自动选择
    - no-skill 路径必须有 generic_analysis 兜底
    - 澄清问题围绕区分候选所需的最少信息

类比 Java：
    ``DecisionPolicy`` ≈ ``@Component`` 策略类，
    ``decide`` ≈ 策略方法，根据输入决策返回 RouteDecision。
    阈值配置 ≈ ``@Value`` 注入的配置参数。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent.langgraph.skills.card import SkillCard
from agent.langgraph.skills.reranker import SkillRankDecision

logger = logging.getLogger(__name__)


# ========== 阈值配置（§10.8.1） ==========


@dataclass(frozen=True)
class DecisionThresholds:
    """决策阈值配置（§10.8.1）。

    critical_absolute_threshold 硬编码不可配置（§10.8.1 约束1）。
    其他阈值可通过 agent_config.skill_router 覆盖。

    Attributes:
        absolute_threshold: Top1 绝对置信度门槛
        margin_threshold: Top1 与 Top2 的最小分差
        composition_confidence: 组合 Skill 最低置信度
        critical_absolute_threshold: critical Skill 绝对门槛（硬编码）
        no_skill_confidence: 低于此值判定 no-skill
    """

    absolute_threshold: float = 0.75
    margin_threshold: float = 0.15
    composition_confidence: float = 0.70
    critical_absolute_threshold: float = 0.85  # 硬编码（§10.8.1 约束1）
    no_skill_confidence: float = 0.40

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "DecisionThresholds":
        """从 agent_config.skill_router 构建阈值（覆盖默认值）。

        critical_absolute_threshold 不可覆盖（§10.8.1 约束1）。
        """
        if not config:
            return cls()
        skill_router = config.get("skill_router", {})
        if not isinstance(skill_router, dict):
            return cls()
        # 按 语言 分别校准（§10.8.1 约束3：absolute_threshold.zh_CN=0.75）
        # P2 阶段简化：只读取全局值，不按语言区分
        return cls(
            absolute_threshold=float(skill_router.get("absolute_threshold", 0.75)),
            margin_threshold=float(skill_router.get("margin_threshold", 0.15)),
            composition_confidence=float(skill_router.get("composition_confidence", 0.70)),
            # critical_absolute_threshold 不从 config 读取（硬编码）
            no_skill_confidence=float(skill_router.get("no_skill_confidence", 0.40)),
        )


# ========== 决策结果类型 ==========


DecisionType = str  # "select" | "clarify" | "no_skill" | "fallback" | "composition"


@dataclass
class DecisionResult:
    """决策结果。

    Attributes:
        decision: 决策类型（select/clarify/no_skill/fallback/composition）
        selected_skill_id: 选中的 skill_id（select/composition 时有值）
        selected_card: 选中的 SkillCard（select 时有值）
        clarify_candidates: 需要澄清的候选列表（clarify 时有值）
        reason: 决策原因
        thresholds: 使用的阈值（审计用）
    """

    decision: DecisionType
    selected_skill_id: str | None = None
    selected_card: SkillCard | None = None
    clarify_candidates: list[SkillCard] | None = None
    reason: str = ""
    thresholds: DecisionThresholds | None = None


# ========== 决策策略实现 ==========


class DecisionPolicy:
    """Stage 6 决策策略（§10.8）。

    根据 SkillRankDecision 和候选 SkillCard 做最终路由决策。

    决策逻辑（§10.8 伪代码）：
        1. Top1 分数 < no_skill_confidence → no_skill
        2. Top1 分数 < absolute_threshold → clarify（或 fallback）
        3. Top1 - Top2 < margin_threshold → clarify
        4. critical Skill 且 Top1 < critical_absolute_threshold → clarify
        5. 以上都不满足 → select Top1
    """

    def __init__(self, thresholds: DecisionThresholds | None = None) -> None:
        self._thresholds = thresholds or DecisionThresholds()

    def decide(
        self,
        rank_decision: SkillRankDecision,
        candidates_by_id: dict[str, SkillCard],
    ) -> DecisionResult:
        """执行决策策略（§10.8）。

        Args:
            rank_decision: Reranker 输出的重排决策
            candidates_by_id: skill_id → SkillCard 映射（用于查询候选详情）

        Returns:
            DecisionResult: 决策结果
        """
        # 无候选 → no_skill
        if not rank_decision.candidates:
            return DecisionResult(
                decision="no_skill",
                reason="NO_CANDIDATES",
                thresholds=self._thresholds,
            )

        top1 = rank_decision.candidates[0]
        top1_card = candidates_by_id.get(top1.skill_id)

        # Top1 分数 < no_skill_confidence → no_skill（§10.8.1）
        if top1.score < self._thresholds.no_skill_confidence:
            logger.debug(
                "[DecisionPolicy] no_skill: top1_score=%.3f < %.3f",
                top1.score,
                self._thresholds.no_skill_confidence,
            )
            return DecisionResult(
                decision="no_skill",
                reason=f"TOP1_SCORE_BELOW_THRESHOLD({top1.score:.3f})",
                thresholds=self._thresholds,
            )

        # critical Skill 特殊处理（§10.8 约束：critical 不得低置信度自动选择）
        if top1_card and top1_card.risk_level == "critical":
            if top1.score < self._thresholds.critical_absolute_threshold:
                logger.debug(
                    "[DecisionPolicy] critical clarify: score=%.3f < %.3f",
                    top1.score,
                    self._thresholds.critical_absolute_threshold,
                )
                return self._build_clarify_result(
                    rank_decision, candidates_by_id,
                    reason=f"CRITICAL_SKILL_LOW_CONFIDENCE({top1.score:.3f})",
                )

        # Top1 分数 < absolute_threshold → clarify（§10.8.1）
        if top1.score < self._thresholds.absolute_threshold:
            logger.debug(
                "[DecisionPolicy] clarify: top1_score=%.3f < %.3f",
                top1.score,
                self._thresholds.absolute_threshold,
            )
            return self._build_clarify_result(
                rank_decision, candidates_by_id,
                reason=f"TOP1_SCORE_BELOW_ABSOLUTE({top1.score:.3f})",
            )

        # Top1 - Top2 < margin_threshold → clarify（§10.8.1）
        if len(rank_decision.candidates) >= 2:
            top2 = rank_decision.candidates[1]
            margin = top1.score - top2.score
            if margin < self._thresholds.margin_threshold:
                logger.debug(
                    "[DecisionPolicy] clarify: margin=%.3f < %.3f",
                    margin,
                    self._thresholds.margin_threshold,
                )
                return self._build_clarify_result(
                    rank_decision, candidates_by_id,
                    reason=f"INSUFFICIENT_MARGIN({margin:.3f})",
                )

        # Reranker 标记歧义 → clarify（§10.7 SkillRankDecision.ambiguity_reason）
        if rank_decision.ambiguity_reason:
            logger.debug(
                "[DecisionPolicy] clarify: ambiguity_reason=%s",
                rank_decision.ambiguity_reason,
            )
            return self._build_clarify_result(
                rank_decision, candidates_by_id,
                reason=f"AMBIGUITY({rank_decision.ambiguity_reason})",
            )

        # 多意图 → composition（§10.8）
        if rank_decision.multi_intent and len(rank_decision.candidates) >= 2:
            logger.debug("[DecisionPolicy] composition: multi_intent=True")
            return DecisionResult(
                decision="composition",
                selected_skill_id=top1.skill_id,
                selected_card=top1_card,
                reason="MULTI_INTENT_COMPOSITION",
                thresholds=self._thresholds,
            )

        # 全部检查通过 → select Top1
        logger.debug("[DecisionPolicy] select: %s (score=%.3f)", top1.skill_id, top1.score)
        return DecisionResult(
            decision="select",
            selected_skill_id=top1.skill_id,
            selected_card=top1_card,
            reason=f"SELECTED(score={top1.score:.3f})",
            thresholds=self._thresholds,
        )

    def _build_clarify_result(
        self,
        rank_decision: SkillRankDecision,
        candidates_by_id: dict[str, SkillCard],
        reason: str,
    ) -> DecisionResult:
        """构建澄清决策结果。

        澄清候选取 Top-2（§10.8 约束：围绕区分候选所需的最少信息）。
        """
        clarify_cards: list[SkillCard] = []
        for ranked in rank_decision.candidates[:2]:
            card = candidates_by_id.get(ranked.skill_id)
            if card:
                clarify_cards.append(card)
        return DecisionResult(
            decision="clarify",
            clarify_candidates=clarify_cards,
            reason=reason,
            thresholds=self._thresholds,
        )
