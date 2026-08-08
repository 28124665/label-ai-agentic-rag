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
"""CitationMetrics — 基于 (claim_id, evidence_id) 对的引用指标体系（v2.1 §4.7 P0-C 修复）。

设计背景：
    v1 按全局去重 evidence_id 计算 precision，同一 Evidence 对 A 有效对 B 无效仍得 1.0，
    无法检测虚引。本模块改为基于 (claim_id, evidence_id) 对聚合，四个指标分别度量
    引用有效性、正确性、完整性与答案忠实度。

指标定义（v2.1 §4.7.2）：
    - citation_validity:      verified_support_ids / resolved_declared_ids
    - citation_correctness:   有 verified_support 的 claim 数 / 总 claim 数
    - citation_completeness:  有 declared_citation 的 required claim 数 / 总 required claim 数
    - faithfulness:           supported claim 数 / 总 claim 数

零除保护：
    所有分母为 0 时统一返回 0.0（含 citation_completeness 无 required claim 的场景）。
    注：设计文档 §4.7.2 中 completeness 无关键 claim 时建议返回 1.0，此处按
    「分母为 0 返回 0.0」的统一保护口径处理，便于上游聚合统计口径一致。
"""
from __future__ import annotations

from agent.langgraph.evidence.claim import Claim, ClaimFinalStatus


class CitationMetrics:
    """引用与忠实度指标计算器（v2.1 §4.7 P0-C 修复）。

    基于 (claim_id, evidence_id) 对聚合，避免 v1 全局去重导致的虚引漏检。
    所有指标均在 [0.0, 1.0] 区间内，分母为 0 时返回 0.0。
    """

    def compute(self, claims: list[Claim]) -> dict[str, float]:
        """计算全部引用指标。

        Args:
            claims: Claim dataclass 列表（含验证结果与引用绑定）

        Returns:
            dict: {
                "citation_validity": float,
                "citation_correctness": float,
                "citation_completeness": float,
                "faithfulness": float,
            }
        """
        return {
            "citation_validity": self._citation_validity(claims),
            "citation_correctness": self._citation_correctness(claims),
            "citation_completeness": self._citation_completeness(claims),
            "faithfulness": self._faithfulness(claims),
        }

    @staticmethod
    def _citation_validity(claims: list[Claim]) -> float:
        """引用有效性：声明的合法引用中真正验证支持的比例。

        分子：所有 claim 的 verified_support_ids 总数
        分母：所有 claim 的 resolved_declared_ids 总数
        含义：引用的资料是否真的支持答案（检测虚引）。

        注：verified_support_ids 可能含反证扫描发现的支持 ID（非模型声明），
        导致分子 > 分母，此处 clamp 到 1.0 保证指标域为 [0, 1]。
        """
        total_resolved = sum(len(c.resolved_declared_ids) for c in claims)
        if total_resolved == 0:
            return 0.0
        total_verified = sum(len(c.verified_support_ids) for c in claims)
        return min(1.0, total_verified / total_resolved)

    @staticmethod
    def _citation_correctness(claims: list[Claim]) -> float:
        """引用正确性：有验证支持的 claim 占比。

        分子：has_verified_support 为 True 的 claim 数
        分母：总 claim 数
        含义：多少比例的 claim 引用了真正支持它的资料。
        """
        if not claims:
            return 0.0
        supported_claims = sum(1 for c in claims if c.has_verified_support)
        return supported_claims / len(claims)

    @staticmethod
    def _citation_completeness(claims: list[Claim]) -> float:
        """引用完整性：关键 claim 中有引用的比例。

        分子：is_required 且有 declared_citation（resolved_declared_ids 非空）的 claim 数
        分母：is_required 的 claim 总数
        含义：答案中所有关键 claim 是否都有引用支撑。

        「有 declared_citation」采用 resolved_declared_ids（合法映射后的引用 ID），
        与 citation_validity 分母保持一致；仅含非法编号（如 [999]）的 claim 不计入「有引用」。
        """
        required_claims = [c for c in claims if c.is_required]
        if not required_claims:
            return 0.0
        cited = sum(1 for c in required_claims if c.resolved_declared_ids)
        return cited / len(required_claims)

    @staticmethod
    def _faithfulness(claims: list[Claim]) -> float:
        """答案忠实度：supported claim 的占比。

        分子：final_status == supported 的 claim 数
        分母：总 claim 数
        含义：最终答案有多少比例的声明被资料证实。
        """
        if not claims:
            return 0.0
        supported = sum(
            1 for c in claims if c.final_status == ClaimFinalStatus.SUPPORTED.value
        )
        return supported / len(claims)
