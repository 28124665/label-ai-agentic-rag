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
"""Claim 与 PairVerdict 数据模型（v2.1 §4.2.1，P0-4/P0-5 修复）。

设计目标：
    将答案拆分为原子事实声明（Claim），每个 Claim 绑定引用编号，
    验证后产出 PairVerdict 列表，按 pair 聚合为 Claim 级状态。

核心约束（评审硬约束 #2）：
    - declared_citation_ids / verified_support_ids / contradicting_ids 必须分开保存
    - 每个 pair 有独立状态，Claim 状态按 pair 聚合
    - 禁止覆盖原始声明（raw_declared_indices 保留所有编号，含非法编号）

类比 Java 中的领域模型：
    ``Claim`` 类似 ``@Entity``，``PairVerdict`` 类似 ``@Embeddable`` 值对象，
    ``ClaimType`` / ``ClaimFinalStatus`` 类似 ``enum``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClaimType(str, Enum):
    """Claim 类型（服务端重新计算，任务二建议 2）。

    用于 ClaimClassifier 判定 is_required：金额/日期/身份/合规类必为 required。
    """

    AMOUNT = "amount"           # 金额（如"营收15.3亿"）
    DATE = "date"               # 日期（如"2024年Q3"）
    RATIO = "ratio"             # 比率（如"增长15.3%"）
    IDENTITY = "identity"       # 身份（如"适用于X"）
    COMPLIANCE = "compliance"   # 合规（如"符合Y标准"）
    CONCLUSION = "conclusion"   # 最终结论
    STATEMENT = "statement"     # 一般陈述（非关键）


class ClaimFinalStatus(str, Enum):
    """Claim 最终状态（v2.1 §4.4，P0-6 修复）。

    优先级（从高到低）：
    - verifier_error：验证器异常，不能映射成 supported
    - contradicted：资料冲突
    - insufficient：资料不足
    - supported：资料明确支持
    - pending：待验证
    - truncated：被 max_claims 截断
    """

    PENDING = "pending"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"
    VERIFIER_ERROR = "verifier_error"
    TRUNCATED = "truncated"


class PairStatus(str, Enum):
    """单个 Claim-Evidence pair 的验证状态。"""

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"
    VERIFIER_ERROR = "verifier_error"


class VerifierStatus(str, Enum):
    """验证器状态（v2.1 P0-5 修复：按 pair 聚合）。"""

    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"
    MIXED = "mixed"         # 部分 pair ok 部分 error
    UNKNOWN = "unknown"
    BUDGET_EXHAUSTED = "budget_exhausted"  # 预算耗尽


@dataclass
class PairVerdict:
    """单个 Claim-Evidence pair 的验证裁决（v2.1 §4.2.1，P0-5 修复）。

    每个 pair 有独立状态，Claim 状态按 pair 聚合。
    v2.1 补充：强类型 schema + 显式版本号（任务二建议 1）。

    类比 Java 中的值对象（Value Object）：
        ``@Value public class PairVerdict { String evidenceId; PairStatus pairStatus; ... }``
    """

    evidence_id: str

    schema_version: str = "1.0"  # 强类型 schema 版本号
    pair_status: str = PairStatus.INSUFFICIENT.value        # supported / contradicted / insufficient / verifier_error
    verifier_status: str = VerifierStatus.UNKNOWN.value     # ok / timeout / error / unknown
    rule_result: str = "unknown"        # rule 层验证结果
    nli_result: str = "unknown"         # NLI 层验证结果
    llm_result: str = "unknown"         # LLM 层验证结果
    attempt_id: str = ""                # 幂等 attempt ID（任务二建议 4）
    is_counter_evidence: bool = False   # ★ 是否为反证扫描发现（非模型声明）

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 state/日志/审计）。"""
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "pair_status": self.pair_status,
            "verifier_status": self.verifier_status,
            "rule_result": self.rule_result,
            "nli_result": self.nli_result,
            "llm_result": self.llm_result,
            "attempt_id": self.attempt_id,
            "is_counter_evidence": self.is_counter_evidence,
        }


@dataclass
class Claim:
    """原子事实声明（v2.1 §4.2.1，P0-4 修复）。

    v2.1 核心修复（P0-4 + 任务二建议 1/2）：
    - 禁止覆盖原始声明：raw_declared_indices 保留所有编号（含 [999] 等非法编号）
    - 分类保存：invalid_indices（不存在的编号）/ unauthorized_ids（越权 ID）
    - 分离保存：verified_support_ids / contradicting_ids
    - 强类型 schema + 显式版本号
    - is_required 由服务端重新计算（server_is_required），不信任模型输出

    类比 Java 中的 ``@Entity``：
        ``@Entity class Claim { @Id String claimId; @ElementCollection List<Integer> rawDeclaredIndices; ... }``
    """

    # 基础信息
    claim_id: str
    text: str

    schema_version: str = "1.0"  # 强类型 schema 版本号
    span: tuple[int, int] = (0, 0)  # 在原文中的 (start, end) 位置

    # ★ P0-4 修复：分类保存引用编号，禁止覆盖原始声明
    raw_declared_indices: list[int] = field(default_factory=list)       # 模型原始声明（含 [999] 等非法编号）
    resolved_declared_ids: list[str] = field(default_factory=list)      # 合法映射的 evidence_id
    invalid_indices: list[int] = field(default_factory=list)            # 不存在的编号（如 [999]）
    unauthorized_ids: list[str] = field(default_factory=list)           # 越权 evidence_id
    truncated_citations: list[int] = field(default_factory=list)        # 被预算截断的引用编号

    # 验证结果（分离保存，评审硬约束 #2）
    verified_support_ids: list[str] = field(default_factory=list)       # 验证通过的支持 ID
    contradicting_ids: list[str] = field(default_factory=list)          # 反证 ID（含模型声明 + 反证扫描）
    pair_verdicts: list[PairVerdict] = field(default_factory=list)       # 每个 pair 的独立裁决

    # 状态
    verifier_status: str = VerifierStatus.UNKNOWN.value     # 按 pair 聚合的验证器状态
    final_status: str = ClaimFinalStatus.PENDING.value      # 最终状态（由 VerdictMatrix 判定）

    # Claim 分类（服务端重新计算，任务二建议 2）
    model_claim_type: str = ClaimType.STATEMENT.value       # 模型声明的类型（仅供审计）
    model_is_required: bool = False                         # 模型声明是否关键（仅供审计）
    server_claim_type: str = ClaimType.STATEMENT.value      # ★ 服务端重新计算的类型
    server_is_required: bool = False                        # ★ 服务端重新计算的关键性

    # 风险等级（服务端重新计算）
    risk_level: str = "low"                                  # low / medium / high

    def add_pair_verdict(self, verdict: PairVerdict) -> None:
        """添加 pair 验证裁决并更新相关 ID 列表。

        Args:
            verdict: pair 验证裁决
        """
        self.pair_verdicts.append(verdict)

        if verdict.pair_status == PairStatus.SUPPORTED.value:
            if verdict.evidence_id not in self.verified_support_ids:
                self.verified_support_ids.append(verdict.evidence_id)
        elif verdict.pair_status == PairStatus.CONTRADICTED.value:
            if verdict.evidence_id not in self.contradicting_ids:
                self.contradicting_ids.append(verdict.evidence_id)

    def aggregate_verifier_status(self) -> str:
        """按 pair 聚合 verifier_status（v2.1 P0-5 修复）。

        聚合规则：
        - 所有 pair 都是 ok → ok
        - 任意 pair 是 error → error（优先级高于 timeout）
        - 任意 pair 是 timeout → timeout
        - 混合状态 → mixed
        - 无 pair → unknown

        Returns:
            str: 聚合后的 verifier_status
        """
        if not self.pair_verdicts:
            self.verifier_status = VerifierStatus.UNKNOWN.value
            return self.verifier_status

        statuses = {v.verifier_status for v in self.pair_verdicts}
        if statuses == {VerifierStatus.OK.value}:
            self.verifier_status = VerifierStatus.OK.value
        elif VerifierStatus.ERROR.value in statuses:
            self.verifier_status = VerifierStatus.ERROR.value
        elif VerifierStatus.TIMEOUT.value in statuses:
            self.verifier_status = VerifierStatus.TIMEOUT.value
        elif VerifierStatus.BUDGET_EXHAUSTED.value in statuses:
            self.verifier_status = VerifierStatus.BUDGET_EXHAUSTED.value
        else:
            self.verifier_status = VerifierStatus.MIXED.value

        return self.verifier_status

    @property
    def is_required(self) -> bool:
        """是否为关键 Claim（优先使用服务端计算结果）。

        Returns:
            bool: server_is_required 优先，fallback 到 model_is_required
        """
        return self.server_is_required or self.model_is_required

    @property
    def has_verified_support(self) -> bool:
        """是否有验证通过的支持证据。

        Returns:
            bool: verified_support_ids 非空
        """
        return len(self.verified_support_ids) > 0

    @property
    def has_contradiction(self) -> bool:
        """是否有反证。

        Returns:
            bool: contradicting_ids 非空
        """
        return len(self.contradicting_ids) > 0

    @property
    def has_invalid_citations(self) -> bool:
        """是否有非法引用编号（P0-4 验收项 15）。

        Returns:
            bool: invalid_indices 非空
        """
        return len(self.invalid_indices) > 0

    @property
    def has_unauthorized_citations(self) -> bool:
        """是否有越权引用。

        Returns:
            bool: unauthorized_ids 非空
        """
        return len(self.unauthorized_ids) > 0

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 state/日志/审计/SSE 传输）。"""
        return {
            "schema_version": self.schema_version,
            "claim_id": self.claim_id,
            "text": self.text,
            "span": list(self.span),
            "raw_declared_indices": self.raw_declared_indices,
            "resolved_declared_ids": self.resolved_declared_ids,
            "invalid_indices": self.invalid_indices,
            "unauthorized_ids": self.unauthorized_ids,
            "truncated_citations": self.truncated_citations,
            "verified_support_ids": self.verified_support_ids,
            "contradicting_ids": self.contradicting_ids,
            "pair_verdicts": [v.to_dict() for v in self.pair_verdicts],
            "verifier_status": self.verifier_status,
            "final_status": self.final_status,
            "model_claim_type": self.model_claim_type,
            "model_is_required": self.model_is_required,
            "server_claim_type": self.server_claim_type,
            "server_is_required": self.server_is_required,
            "risk_level": self.risk_level,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        """从字典反序列化（用于 state 恢复）。

        Args:
            data: 字典数据

        Returns:
            Claim 实例
        """
        pair_verdicts = [
            PairVerdict(**pv) if isinstance(pv, dict) else pv
            for pv in data.get("pair_verdicts", [])
        ]
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            claim_id=data["claim_id"],
            text=data["text"],
            span=tuple(data.get("span", [0, 0])),
            raw_declared_indices=data.get("raw_declared_indices", []),
            resolved_declared_ids=data.get("resolved_declared_ids", []),
            invalid_indices=data.get("invalid_indices", []),
            unauthorized_ids=data.get("unauthorized_ids", []),
            truncated_citations=data.get("truncated_citations", []),
            verified_support_ids=data.get("verified_support_ids", []),
            contradicting_ids=data.get("contradicting_ids", []),
            pair_verdicts=pair_verdicts,
            verifier_status=data.get("verifier_status", VerifierStatus.UNKNOWN.value),
            final_status=data.get("final_status", ClaimFinalStatus.PENDING.value),
            model_claim_type=data.get("model_claim_type", ClaimType.STATEMENT.value),
            model_is_required=data.get("model_is_required", False),
            server_claim_type=data.get("server_claim_type", ClaimType.STATEMENT.value),
            server_is_required=data.get("server_is_required", False),
            risk_level=data.get("risk_level", "low"),
        )
