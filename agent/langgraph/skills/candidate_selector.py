#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""候选 Skill 依赖选择器（设计文档 v1.1 §9.7）。

当一个 Skill 声明了依赖（``depends_on``）且存在多个候选实现时，
按 5 级策略选择最合适的候选 SkillManifest。

选择策略（§9.7，优先级从高到低，每级仅在能缩小范围时生效）：
    1. 租户偏好 — 命中 ``preferred_skill_id`` 直接选定
    2. 成本优先 — 取成本等级最低（low < medium < high）
    3. 延迟优先 — 取延迟等级最低（interactive < batch）
    4. 版本优先 — 取满足版本约束的最高版本
    5. 默认     — 取候选集首个

类比 Java：
    ``CandidateDependencySelector`` ≈ ``@Service`` 选择策略编排类，
    ``CandidateSelectionContext`` ≈ ``@Value`` 查询入参（DTO）。
    各级策略可用策略模式拆分，此处合并实现以保持简洁。
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel

from agent.langgraph.skills.catalog_models import SkillManifest
from agent.langgraph.skills.dependency_graph import resolve_version_constraint

logger = logging.getLogger(__name__)

# packaging 降级处理（用于版本排序键）
try:
    from packaging.version import InvalidVersion, Version

    _HAS_PACKAGING = True
except ImportError:  # pragma: no cover - 降级路径
    _HAS_PACKAGING = False
    Version = None  # type: ignore[assignment]
    InvalidVersion = ValueError  # type: ignore[assignment]


class CandidateSelectionContext(BaseModel):
    """候选选择上下文（§9.7）。

    Attributes:
        tenant_id: 租户 ID
        preferred_skill_id: 租户偏好的 Skill ID（命中即选定）
        preferred_cost_class: 偏好成本等级（None 表示按最低成本自动选择）
        preferred_latency_class: 偏好延迟等级（None 表示按最低延迟自动选择）
        preferred_version: 偏好版本（如 "1.4.0"；None 表示取最高版本）
    """

    tenant_id: str = ""
    preferred_skill_id: str | None = None
    preferred_cost_class: Literal["low", "medium", "high"] | None = None
    preferred_latency_class: Literal["interactive", "batch"] | None = None
    preferred_version: str | None = None


class CandidateDependencySelector:
    """候选依赖选择器（设计文档 §9.7）。

    类比 Java 中的 ``@Service``：5 级策略级联，每级仅在能缩小候选集时生效，
    保证最终总能选出候选（除非候选集为空）。
    """

    def select(
        self,
        candidates: list[SkillManifest],
        context: CandidateSelectionContext,
        constraint: str | None = None,
    ) -> SkillManifest | None:
        """从候选列表中选择最合适的 SkillManifest。

        Args:
            candidates: 候选 SkillManifest 列表
            context: 选择上下文（租户偏好 / 成本 / 延迟 / 版本偏好）
            constraint: 版本约束字符串（如 "^1.4"），先过滤候选集

        Returns:
            选中的 SkillManifest；候选集为空或全部不满足约束时返回 None
        """
        if not candidates:
            return None

        pool = list(candidates)

        # 前置：版本约束过滤（仅保留满足约束的候选；全部不满足则保留原集合）
        if constraint:
            filtered = [
                c for c in pool
                if self._version_satisfies(c.card.version, constraint)
            ]
            if filtered:
                pool = filtered
            else:
                logger.warning(
                    "[CandidateSelector] 无候选满足版本约束 %s，保留全部候选",
                    constraint,
                )

        # 策略1：租户偏好（命中即选定，直接返回）
        if context.preferred_skill_id:
            for c in pool:
                if c.card.skill_id == context.preferred_skill_id:
                    logger.debug(
                        "[CandidateSelector] 租户偏好命中: %s",
                        context.preferred_skill_id,
                    )
                    return c

        # 策略2：成本优先
        pool = self._filter_by_cost(pool, context.preferred_cost_class)
        if len(pool) == 1:
            return pool[0]

        # 策略3：延迟优先
        pool = self._filter_by_latency(pool, context.preferred_latency_class)
        if len(pool) == 1:
            return pool[0]

        # 策略4：版本优先（取最高版本，或偏好版本）
        chosen = self._pick_by_version(pool, context.preferred_version)
        if chosen is not None:
            return chosen

        # 策略5：默认（首个）
        return pool[0]

    # ========== 策略实现 ==========
    @staticmethod
    def _version_satisfies(version: str, constraint: str) -> bool:
        """判断版本是否满足约束（复用 ``resolve_version_constraint``）。"""
        matched = resolve_version_constraint([version], constraint)
        return matched == version

    @staticmethod
    def _filter_by_cost(
        pool: list[SkillManifest],
        preferred: Literal["low", "medium", "high"] | None,
    ) -> list[SkillManifest]:
        """成本优先过滤。"""
        if not pool:
            return pool
        if preferred:
            filtered = [c for c in pool if c.card.cost_class == preferred]
            if filtered:
                return filtered
        # 自动取最低成本等级
        lowest_rank = min(_cost_rank(c.card.cost_class) for c in pool)
        filtered = [c for c in pool if _cost_rank(c.card.cost_class) == lowest_rank]
        return filtered or pool

    @staticmethod
    def _filter_by_latency(
        pool: list[SkillManifest],
        preferred: Literal["interactive", "batch"] | None,
    ) -> list[SkillManifest]:
        """延迟优先过滤。"""
        if not pool:
            return pool
        if preferred:
            filtered = [c for c in pool if c.card.latency_class == preferred]
            if filtered:
                return filtered
        lowest_rank = min(_latency_rank(c.card.latency_class) for c in pool)
        filtered = [c for c in pool if _latency_rank(c.card.latency_class) == lowest_rank]
        return filtered or pool

    @staticmethod
    def _pick_by_version(
        pool: list[SkillManifest],
        preferred_version: str | None,
    ) -> SkillManifest | None:
        """版本优先选择：偏好版本命中则取，否则取最高版本。"""
        if not pool:
            return None
        if preferred_version:
            for c in pool:
                if c.card.version == preferred_version:
                    return c
        # 取最高版本
        return max(pool, key=lambda c: _version_key(c.card.version))


# ========== 排序辅助 ==========
def _cost_rank(cost_class: str) -> int:
    """成本等级排序键：low(0) < medium(1) < high(2)。"""
    return {"low": 0, "medium": 1, "high": 2}.get(cost_class, 1)


def _latency_rank(latency_class: str) -> int:
    """延迟等级排序键：interactive(0) < batch(1)。"""
    return {"interactive": 0, "batch": 1}.get(latency_class, 0)


def _version_key(version: str) -> Any:
    """版本排序键：优先 packaging.Version，降级到 semver 元组。"""
    if _HAS_PACKAGING:
        try:
            return Version(version)  # type: ignore[no-any-return]
        except InvalidVersion:
            pass
    # 降级：简易 semver 元组
    parts = version.strip().split(".")
    nums: list[int] = []
    for part in parts[:3]:
        dig = ""
        for ch in part:
            if ch.isdigit():
                dig += ch
            else:
                break
        nums.append(int(dig) if dig else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)
