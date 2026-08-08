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
"""Policy Engine 单一决策者（v2.1 §4.6，P1-C 修复 + 验收项 17）。

设计目标：
    作为 Claim 级幻觉检测的**唯一决策出口**，根据 Claim 验证结果、运行模式、
    重试次数和证据快照，按优先级输出最终策略决策（pass/filter/regenerate/
    exhausted/reject）。

核心约束：
    - **单一决策者**（P1-C 修复）：Gateway 不再返回 action，所有策略判定集中在
      ``PolicyEngine.decide()``，避免节点忽略 Gateway 建议后按全局分数重算的
      "两个 owner" 问题。
    - **关键 Claim 一票否决**（P1-D 修复）：``is_required=True`` 且
      ``final_status=contradicted`` → 直接 reject，防止琐碎 Claim 淹没关键矛盾。
    - **可追溯 regeneration**：regenerate 时携带 ``forbidden_claim_texts`` +
      ``allowed_evidence_ids`` + ``regenerate_instruction``，修复"regenerate 回到
      同一上下文只是重复抽样"的问题。

决策优先级（从高到低）：
    a. 关键 Claim 矛盾 → reject（P1-D 修复，一票否决）
    b. 关键 Claim 不足 → reject（v2.1 验收项 17，FACTUAL 模式下 fail-closed）
    c. verifier_error 比例过高 → reject（验证器大面积故障，结果不可信）
    d. regenerate_count 超限 → exhausted（无法继续改善）
    e. 不足 Claim 比例高 → regenerate（携带失败上下文重新生成）
    f. 全部 supported → pass
    g. 部分 supported → filter

类比 Java 中的策略服务（参考 design-patterns rule）：
    ``@Service class PolicyEngine`` 承担单一决策职责，对外暴露
    ``decide()`` 方法，内部按优先级链式判定，符合"单一职责"原则。

参考文档：docs/Claim级可追溯幻觉检测设计.md §4.6
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.langgraph.config import RunMode
from agent.langgraph.evidence.claim import Claim, ClaimFinalStatus, VerifierStatus
from agent.langgraph.evidence.snapshot import EvidenceSnapshot

logger = logging.getLogger(__name__)


class PolicyAction(str, Enum):
    """策略动作枚举（v2.1 §4.6）。

    类比 Java 中的 ``enum PolicyAction``，确保动作值类型安全且全局唯一。

    动作含义：
        - PASS: 全部 Claim 验证通过（或忠实度 ≥ pass_threshold），原样输出
        - FILTER: 部分 Claim 验证通过（忠实度处于 filter 区间），过滤后输出
        - REGENERATE: 不足 Claim 比例高（忠实度低于 filter_threshold），
          携带禁忌清单 + 允许证据重新生成
        - EXHAUSTED: 重试次数耗尽且答案仍不达标，无法继续改善
        - REJECT: 关键 Claim 矛盾/不足或验证器大面积故障，直接拒答（fail-closed）
    """

    PASS = "pass"
    FILTER = "filter"
    REGENERATE = "regenerate"
    EXHAUSTED = "exhausted"
    REJECT = "reject"


@dataclass
class PolicyDecision:
    """策略决策结果（v2.1 §4.6，P1-C 修复，任务二建议 1 强类型 schema）。

    Attributes:
        schema_version: 强类型 schema 版本号（便于后续演进兼容）
        action: 策略动作（pass/filter/regenerate/exhausted/reject）
        reason: 决策原因（人类可读，用于日志/审计/SSE 透传）
        faithfulness_score: 忠实度得分 [0.0, 1.0]，
            按 Claim 关键性加权计算（supported_weight / total_weight）
        failed_claims: 失败 Claim 列表（final_status != supported 的 Claim）
        forbidden_claim_texts: 禁忌 Claim 文本列表（regenerate 时传给模型，
            禁止再次生成这些声明）
        allowed_evidence_ids: 允许使用的证据 ID 列表（验证通过的证据，
            regenerate 时引导模型复用有效证据）
        regenerate_instruction: 重新生成指令（regenerate 时携带，含差异化反馈）
        verifier_status: 验证器整体状态（按 Claim 聚合）

    类比 Java 中的 DTO（Data Transfer Object）：
        ``@Data @Builder class PolicyDecision``，
        作为 PolicyEngine 与调用方之间的契约对象。
    """

    schema_version: str = "1.0"  # 强类型 schema 版本号（任务二建议 1）

    action: str = PolicyAction.PASS.value
    reason: str = ""
    faithfulness_score: float = 0.0
    failed_claims: list[Claim] = field(default_factory=list)
    forbidden_claim_texts: list[str] = field(default_factory=list)
    allowed_evidence_ids: list[str] = field(default_factory=list)
    regenerate_instruction: str = ""
    verifier_status: str = VerifierStatus.OK.value

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 state/日志/审计/SSE 传输）。

        failed_claims 中的 Claim 对象会递归调用其 ``to_dict()``，
        确保序列化结果为纯 JSON 结构。
        """
        return {
            "schema_version": self.schema_version,
            "action": self.action,
            "reason": self.reason,
            "faithfulness_score": round(self.faithfulness_score, 4),
            "failed_claims": [
                c.to_dict() if hasattr(c, "to_dict") else c
                for c in self.failed_claims
            ],
            "forbidden_claim_texts": self.forbidden_claim_texts,
            "allowed_evidence_ids": self.allowed_evidence_ids,
            "regenerate_instruction": self.regenerate_instruction,
            "verifier_status": self.verifier_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyDecision":
        """从字典反序列化（用于 state 恢复）。

        Args:
            data: 字典数据

        Returns:
            PolicyDecision 实例（failed_claims 会还原为 Claim 对象）
        """
        failed = data.get("failed_claims", [])
        failed_claims = [
            Claim.from_dict(c) if isinstance(c, dict) else c for c in failed
        ]
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            action=data.get("action", PolicyAction.PASS.value),
            reason=data.get("reason", ""),
            faithfulness_score=data.get("faithfulness_score", 0.0),
            failed_claims=failed_claims,
            forbidden_claim_texts=data.get("forbidden_claim_texts", []),
            allowed_evidence_ids=data.get("allowed_evidence_ids", []),
            regenerate_instruction=data.get("regenerate_instruction", ""),
            verifier_status=data.get("verifier_status", VerifierStatus.OK.value),
        )


class PolicyEngine:
    """策略引擎：Claim 级幻觉检测的唯一决策者（v2.1 §4.6，P1-C 修复）。

    职责：
        根据 Claim 验证结果、运行模式、重试次数和证据快照，按优先级输出
        最终策略决策。Gateway 只返回 claim_verdicts，不再返回 action，
        所有策略判定集中在此类。

    设计决策：
        - **单一决策者模式**：避免决策逻辑散落到多个节点（P1-C 修复核心），
          防止"Gateway 返回 action → 节点忽略 → 按全局分数重算"的不一致问题。
        - **硬性约束优先**：关键 Claim 矛盾/不足、验证器大面积故障等不可恢复
          的失败优先判定，避免低质量答案通过后续阈值检查。
        - **加权评分**（P1-D 修复）：关键 Claim 权重高于普通 Claim，防止
          琐碎 Claim 淹没关键矛盾（如用 100 个 supported 琐碎 Claim 稀释
          1 个 contradicted 关键 Claim）。
        - **可追溯 regeneration**：regenerate 时携带 forbidden_claim_texts
          和 allowed_evidence_ids，确保模型重新生成时避开失败声明、复用
          已验证有效的证据，而非简单重复抽样。

    类比 Java 中的策略服务：
        ``@Service class PolicyEngine`` 承担单一决策职责，
        对外暴露 ``decide()`` 方法，内部按优先级链式判定。
    """

    # ===== 阈值常量（可由构造函数覆盖）=====

    # verifier_error 占比 ≥ 此阈值 → reject（验证器大面积故障，结果不可信）
    VERIFIER_ERROR_REJECT_RATIO = 0.3

    # 忠实度 ≥ 此阈值 → pass（全部/绝大多数 Claim 验证通过）
    PASS_THRESHOLD = 0.85

    # 忠实度 ≥ 此阈值（但 < PASS_THRESHOLD）→ filter（部分通过，过滤后输出）
    # 忠实度 < 此阈值 → regenerate（不足 Claim 比例高，需重新生成）
    FILTER_THRESHOLD = 0.6

    # 最大重试次数（默认 2，与 rag_enhancement 配置 max_regenerates 对齐）
    DEFAULT_MAX_REGENERATES = 2

    # ===== 关键性权重（P1-D 修复：加权评分，防止琐碎 Claim 淹没关键矛盾）=====

    # is_required=True 的关键 Claim 权重（类比 importance_weights["critical"]=3.0）
    _REQUIRED_WEIGHT = 3.0
    # risk_level="high" 的高风险 Claim 权重（类比 importance_weights["important"]=2.0）
    _HIGH_RISK_WEIGHT = 2.0
    # 普通 Claim 权重（类比 importance_weights["normal"]=1.0）
    _NORMAL_WEIGHT = 1.0

    def __init__(
        self,
        max_regenerates: int | None = None,
        verifier_error_reject_ratio: float | None = None,
        pass_threshold: float | None = None,
        filter_threshold: float | None = None,
    ) -> None:
        """初始化策略引擎。

        所有参数可选，None 时使用类常量默认值。调用方可从
        ``rag_enhancement.yaml`` 的 ``hallucination`` 配置段注入值。

        Args:
            max_regenerates: 最大重试次数（None → DEFAULT_MAX_REGENERATES）
            verifier_error_reject_ratio: verifier_error 拒答比例阈值
            pass_threshold: pass 忠实度阈值
            filter_threshold: filter/regenerate 分界忠实度阈值
        """
        self.max_regenerates = max_regenerates or self.DEFAULT_MAX_REGENERATES
        self.verifier_error_reject_ratio = (
            verifier_error_reject_ratio
            if verifier_error_reject_ratio is not None
            else self.VERIFIER_ERROR_REJECT_RATIO
        )
        self.pass_threshold = (
            pass_threshold if pass_threshold is not None else self.PASS_THRESHOLD
        )
        self.filter_threshold = (
            filter_threshold
            if filter_threshold is not None
            else self.FILTER_THRESHOLD
        )

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def decide(
        self,
        claims: list[Claim],
        run_mode: RunMode,
        regenerate_count: int,
        evidence_snapshot: EvidenceSnapshot,
    ) -> PolicyDecision:
        """执行策略决策（v2.1 §4.6，P1-C 修复 + 验收项 17）。

        按优先级链式判定，命中任一条件立即返回，不继续后续检查。

        Args:
            claims: Claim 列表（已完成验证，final_status 已由 VerdictMatrix 填充）
            run_mode: 业务运行模式（CHITCHAT/FACTUAL/REPORT）
            regenerate_count: 当前已重试次数（首次生成 = 0）
            evidence_snapshot: 不可变证据快照（用于提取 allowed_evidence_ids）

        Returns:
            PolicyDecision: 策略决策结果
        """
        # 空答案处理：无 Claim 需要验证，视为通过（如闲聊模式短答案）
        if not claims:
            return PolicyDecision(
                action=PolicyAction.PASS.value,
                reason="无 Claim 需要验证",
                faithfulness_score=1.0,
                verifier_status=VerifierStatus.UNKNOWN.value,
            )

        total = len(claims)

        # 预计算统计量与加权忠实度
        weighted = self._compute_weighted_score(claims)
        faithfulness_score = weighted["overall"]
        verifier_status = self._aggregate_verifier_status(claims)

        # 失败 Claim = final_status != supported（含 contradicted/insufficient/
        # verifier_error/pending/truncated），用于 reject/regenerate/filter 携带
        failed_claims = [
            c for c in claims
            if c.final_status != ClaimFinalStatus.SUPPORTED.value
        ]
        # 禁忌文本 = 所有失败 Claim 的文本（regenerate 时禁止模型再次生成）
        forbidden_claim_texts = [c.text for c in failed_claims]

        # ===== a. 关键 Claim 矛盾 → reject（P1-D 修复，一票否决）=====
        # is_required=True 且 final_status=contradicted → 直接 reject
        # 设计意图：关键 Claim（金额/日期/身份/合规）出现矛盾时不可妥协，
        # 防止用大量 supported 琐碎 Claim 稀释关键矛盾（P1-D 根因）
        critical_contradicted = [
            c for c in claims
            if c.is_required
            and c.final_status == ClaimFinalStatus.CONTRADICTED.value
        ]
        if critical_contradicted:
            return PolicyDecision(
                action=PolicyAction.REJECT.value,
                reason=(
                    f"关键 Claim 存在矛盾（{len(critical_contradicted)} 个），"
                    f"一票否决: {[c.claim_id for c in critical_contradicted]}"
                ),
                faithfulness_score=faithfulness_score,
                failed_claims=failed_claims,
                forbidden_claim_texts=forbidden_claim_texts,
                verifier_status=verifier_status,
            )

        # ===== b. ★关键 Claim 不足 → reject（v2.1 验收项 17，FACTUAL 模式）=====
        # FACTUAL 模式下 is_required=True 且 final_status=insufficient → reject
        # 设计意图：事实问答模式 fail-closed，关键 Claim 证据不足时不能"尽量回答"，
        # 必须拒答而非输出未验证内容（验收项 17 硬性要求）
        if run_mode == RunMode.FACTUAL:
            critical_insufficient = [
                c for c in claims
                if c.is_required
                and c.final_status == ClaimFinalStatus.INSUFFICIENT.value
            ]
            if critical_insufficient:
                return PolicyDecision(
                    action=PolicyAction.REJECT.value,
                    reason=(
                        f"FACTUAL 模式下关键 Claim 证据不足"
                        f"（{len(critical_insufficient)} 个），拒答: "
                        f"{[c.claim_id for c in critical_insufficient]}"
                    ),
                    faithfulness_score=faithfulness_score,
                    failed_claims=failed_claims,
                    forbidden_claim_texts=forbidden_claim_texts,
                    verifier_status=verifier_status,
                )

        # ===== c. verifier_error 比例过高 → reject =====
        # 验证器大面积故障时结果不可信，直接拒答（fail-closed）
        # 阈值 0.3：超过 30% 的 Claim 验证器异常，判定为系统性故障
        verifier_errors = [
            c for c in claims
            if c.final_status == ClaimFinalStatus.VERIFIER_ERROR.value
        ]
        verifier_error_ratio = len(verifier_errors) / total if total > 0 else 0.0
        if verifier_error_ratio >= self.verifier_error_reject_ratio:
            return PolicyDecision(
                action=PolicyAction.REJECT.value,
                reason=(
                    f"验证器错误比例过高"
                    f"（{verifier_error_ratio:.1%} ≥ "
                    f"{self.verifier_error_reject_ratio:.1%}），结果不可信"
                ),
                faithfulness_score=faithfulness_score,
                failed_claims=failed_claims,
                verifier_status=VerifierStatus.ERROR.value,
            )

        # ===== f. 全部 supported（忠实度 ≥ pass_threshold）→ pass =====
        # 优先检查 pass 条件：即使 regenerate_count 已耗尽，高质量答案仍应通过
        # （pass 不受重试预算限制，只有需要改善时才检查预算）
        if faithfulness_score >= self.pass_threshold:
            return PolicyDecision(
                action=PolicyAction.PASS.value,
                reason=(
                    f"忠实度达标（{faithfulness_score:.2f} ≥ "
                    f"{self.pass_threshold}），原样输出"
                ),
                faithfulness_score=faithfulness_score,
                verifier_status=verifier_status,
            )

        # 以下为非 pass 场景：准备 regenerate 所需的失败上下文
        allowed_evidence_ids = self._collect_allowed_evidence_ids(
            claims, evidence_snapshot
        )

        # ===== g. 部分 supported（忠实度处于 filter 区间）→ filter =====
        # filter_threshold ≤ 忠实度 < pass_threshold：过滤掉失败 Claim 后输出
        if faithfulness_score >= self.filter_threshold:
            return PolicyDecision(
                action=PolicyAction.FILTER.value,
                reason=(
                    f"部分 Claim 验证通过（忠实度 {faithfulness_score:.2f}，"
                    f"处于 filter 区间），过滤失败 Claim 后输出"
                ),
                faithfulness_score=faithfulness_score,
                failed_claims=failed_claims,
                forbidden_claim_texts=forbidden_claim_texts,
                allowed_evidence_ids=allowed_evidence_ids,
                verifier_status=verifier_status,
            )

        # ===== d + e. 忠实度低于 filter_threshold（不足 Claim 比例高）=====
        # 需要重新生成，但先检查重试预算：
        #   d. regenerate_count 超限 → exhausted（无法继续改善）
        #   e. 仍可重试 → regenerate（携带失败上下文）
        # 设计意图：d 优先于 e，确保重试预算耗尽时不再无效重试
        if regenerate_count >= self.max_regenerates:
            return PolicyDecision(
                action=PolicyAction.EXHAUSTED.value,
                reason=(
                    f"重试次数已耗尽（{regenerate_count}/"
                    f"{self.max_regenerates}），忠实度仍为 "
                    f"{faithfulness_score:.2f}，无法继续改善"
                ),
                faithfulness_score=faithfulness_score,
                failed_claims=failed_claims,
                forbidden_claim_texts=forbidden_claim_texts,
                allowed_evidence_ids=allowed_evidence_ids,
                verifier_status=verifier_status,
            )

        # e. regenerate：携带 forbidden_claim_texts + allowed_evidence_ids +
        #    regenerate_instruction，修复"regenerate 回到同一上下文只是重复抽样"
        instruction = self._build_regenerate_instruction(
            failed_claims, allowed_evidence_ids
        )
        return PolicyDecision(
            action=PolicyAction.REGENERATE.value,
            reason=(
                f"忠实度过低（{faithfulness_score:.2f} < "
                f"{self.filter_threshold}），携带失败上下文重新生成"
                f"（第 {regenerate_count + 1}/{self.max_regenerates} 次）"
            ),
            faithfulness_score=faithfulness_score,
            failed_claims=failed_claims,
            forbidden_claim_texts=forbidden_claim_texts,
            allowed_evidence_ids=allowed_evidence_ids,
            regenerate_instruction=instruction,
            verifier_status=verifier_status,
        )

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _compute_weighted_score(self, claims: list[Claim]) -> dict[str, Any]:
        """按关键性加权计算忠实度（P1-D 修复）。

        关键 Claim（is_required=True）权重 3.0，高风险 Claim 权重 2.0，
        普通 Claim 权重 1.0。加权后 supported_weight / total_weight，
        防止琐碎 Claim 淹没关键矛盾。

        类比 Java 中的策略计算：
            等价于 ``importance_weights.get(claim.getImportance(), 1.0)``
            的加权平均，确保关键 Claim 的失败对分数影响更大。

        Args:
            claims: Claim 列表

        Returns:
            dict: 包含 overall（加权忠实度）、supported_count、
            contradicted_count、insufficient_count 的统计字典
        """
        total_weight = 0.0
        supported_weight = 0.0
        supported_count = 0
        contradicted_count = 0
        insufficient_count = 0

        for claim in claims:
            # 关键性 → 权重：required > high_risk > normal
            if claim.is_required:
                weight = self._REQUIRED_WEIGHT
            elif claim.risk_level == "high":
                weight = self._HIGH_RISK_WEIGHT
            else:
                weight = self._NORMAL_WEIGHT

            total_weight += weight
            if claim.final_status == ClaimFinalStatus.SUPPORTED.value:
                supported_weight += weight
                supported_count += 1
            elif claim.final_status == ClaimFinalStatus.CONTRADICTED.value:
                contradicted_count += 1
            elif claim.final_status == ClaimFinalStatus.INSUFFICIENT.value:
                insufficient_count += 1

        return {
            "overall": supported_weight / total_weight
            if total_weight > 0
            else 0.0,
            "supported_count": supported_count,
            "contradicted_count": contradicted_count,
            "insufficient_count": insufficient_count,
        }

    def _aggregate_verifier_status(self, claims: list[Claim]) -> str:
        """聚合所有 Claim 的 verifier_status（v2.1 P0-5 修复：按 Claim 聚合）。

        聚合规则（与 Claim.aggregate_verifier_status 对齐）：
        - 所有 Claim 都 ok → ok
        - 任意 Claim 是 error → error（优先级高于 timeout）
        - 任意 Claim 是 timeout → timeout
        - 任意 Claim 是 budget_exhausted → budget_exhausted
        - 混合状态 → mixed
        - 无 Claim → unknown

        Args:
            claims: Claim 列表

        Returns:
            str: 聚合后的 verifier_status
        """
        if not claims:
            return VerifierStatus.UNKNOWN.value

        statuses = {c.verifier_status for c in claims}
        if statuses == {VerifierStatus.OK.value}:
            return VerifierStatus.OK.value
        if VerifierStatus.ERROR.value in statuses:
            return VerifierStatus.ERROR.value
        if VerifierStatus.TIMEOUT.value in statuses:
            return VerifierStatus.TIMEOUT.value
        if VerifierStatus.BUDGET_EXHAUSTED.value in statuses:
            return VerifierStatus.BUDGET_EXHAUSTED.value
        if len(statuses) > 1:
            return VerifierStatus.MIXED.value
        # 单一非上述状态（如全 unknown）
        return next(iter(statuses)) if statuses else VerifierStatus.UNKNOWN.value

    def _collect_allowed_evidence_ids(
        self,
        claims: list[Claim],
        evidence_snapshot: EvidenceSnapshot,
    ) -> list[str]:
        """收集验证通过的证据 ID（regenerate 时作为 allowed_evidence_ids 传递）。

        从所有 Claim 的 ``verified_support_ids`` 中收集去重，
        若提供 evidence_snapshot 则进一步过滤确保 ID 在当前快照中存在
        （防止过期证据跨快照泄漏）。

        设计意图：regenerate 时引导模型复用已验证有效的证据，
        而非重新检索可能不一致的证据集。

        Args:
            claims: Claim 列表
            evidence_snapshot: 不可变证据快照（用于过滤有效证据 ID）

        Returns:
            list[str]: 去重后的 allowed_evidence_ids
        """
        allowed: list[str] = []
        seen: set[str] = set()
        for claim in claims:
            for eid in claim.verified_support_ids:
                if eid and eid not in seen:
                    seen.add(eid)
                    allowed.append(eid)

        # 若提供快照，过滤掉不在当前快照中的 ID（防止跨快照泄漏）
        if evidence_snapshot is not None and allowed:
            snapshot_ids = {
                ev.get("evidence_id")
                for ev in evidence_snapshot.evidences
                if ev.get("evidence_id")
            }
            allowed = [eid for eid in allowed if eid in snapshot_ids]

        return allowed

    def _build_regenerate_instruction(
        self,
        failed_claims: list[Claim],
        allowed_evidence_ids: list[str],
    ) -> str:
        """构建重新生成指令（携带差异化失败反馈）。

        对不同失败类型给出差异化指导：
        - contradicted: 与资料矛盾，不得再次输出
        - insufficient: 资料不足，需补充证据或删除
        - verifier_error: 验证异常，谨慎处理
        - 其他: 未通过验证，需修正

        同时列出允许使用的已验证证据 ID，引导模型复用有效证据。

        Args:
            failed_claims: 失败 Claim 列表
            allowed_evidence_ids: 允许使用的证据 ID 列表

        Returns:
            str: 重新生成指令文本
        """
        lines: list[str] = ["请根据以下反馈重新生成答案："]

        for claim in failed_claims:
            if claim.final_status == ClaimFinalStatus.CONTRADICTED.value:
                lines.append(
                    f"- 以下内容与资料矛盾，不得再次输出：{claim.text}"
                )
            elif claim.final_status == ClaimFinalStatus.INSUFFICIENT.value:
                lines.append(
                    f"- 以下内容资料不足，需补充证据或删除：{claim.text}"
                )
            elif claim.final_status == ClaimFinalStatus.VERIFIER_ERROR.value:
                lines.append(
                    f"- 以下内容验证异常，请谨慎处理或删除：{claim.text}"
                )
            else:
                lines.append(
                    f"- 以下内容未通过验证，需修正：{claim.text}"
                )

        if allowed_evidence_ids:
            lines.append(
                f"- 优先使用以下已验证通过的证据编号：{allowed_evidence_ids}"
            )

        return "\n".join(lines)
