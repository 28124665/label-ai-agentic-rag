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
"""硬过滤链（设计文档 v1.1 §10.3）。

按固定顺序执行多层硬过滤，在候选召回前排除不可用 Skill：
    lifecycle → tenant → permission → route capability → language
    → dependency health → runtime capability gate → feature flag

每层独立过滤，过滤原因计入 ``filtered_reason_counts`` 供审计（§10.3.1 约束4）。

类比 Java 中的责任链模式（Chain of Responsibility）：
    每个 Filter ≈ ``@Component`` 实现 ``Filter`` 接口，
    ``HardFilterChain`` ≈ ``FilterChain`` 编排执行顺序。
    区别：责任链通常逐个传递，这里是逐层过滤（每层缩减候选集）。
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from agent.langgraph.skills.catalog import CatalogSnapshot
from agent.langgraph.skills.catalog_models import SkillCard, SkillManifest

logger = logging.getLogger(__name__)

# route_target → 需要的 capability 集合（route capability 层过滤用）
# Skill 的 capabilities 必须与 route_target 所需能力有交集，否则过滤
_ROUTE_CAPABILITY_MAP: dict[str, set[str]] = {
    "chitchat": set(),  # 闲聊不需要 Skill
    "database": {"database"},
    "rag": {"rag"},
    "hybrid": {"database", "rag"},  # hybrid 至少需要 database 或 rag
    "report": {"report", "database", "rag"},  # 报告可含任一执行能力
    "web": {"web"},
}


@dataclass
class HardFilterContext:
    """硬过滤上下文（请求级，每次请求重新构造）。

    类比 Java 中的 ``@Value`` 请求作用域 DTO：
        ``HardFilterContext`` ≈ ``RequestContext``，携带请求级租户/权限/配置。
    """

    tenant_id: str
    permissions: list[str] = field(default_factory=list)
    route_target: str = "chitchat"
    locale: str = "zh_CN"
    agent_config: dict = field(default_factory=dict)


@dataclass
class HardFilterResult:
    """硬过滤结果。"""

    cards: list[SkillCard] = field(default_factory=list)
    filtered_reason_counts: dict[str, int] = field(default_factory=dict)


class HardFilterChain:
    """硬过滤链编排器（设计文档 §10.3）。

    按固定顺序执行各层过滤，每层接收上一层的结果作为输入。
    过滤顺序严格按设计文档 §10.3：
        lifecycle → tenant → permission → route capability → language
        → dependency health → runtime capability gate → feature flag
    """

    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self._snapshot = snapshot

    def filter(self, context: HardFilterContext) -> HardFilterResult:
        """执行完整硬过滤链。"""
        cards = self._snapshot.list_cards()
        reason_counts: dict[str, int] = {}

        # 按设计文档 §10.3 顺序逐层过滤
        cards, rc = self._filter_lifecycle(cards)
        reason_counts.update(rc)

        cards, rc = self._filter_tenant(cards, context)
        reason_counts.update(rc)

        cards, rc = self._filter_permission(cards, context)
        reason_counts.update(rc)

        cards, rc = self._filter_route_capability(cards, context)
        reason_counts.update(rc)

        cards, rc = self._filter_language(cards, context)
        reason_counts.update(rc)

        cards, rc = self._filter_dependency_health(cards)
        reason_counts.update(rc)

        # runtime capability gate（§10.3.1，请求级 agent_config 开关）
        cards, rc = self._filter_runtime_capability(cards, context)
        reason_counts.update(rc)

        # feature flag / rollout（§10.3 最后一层，canary + rollout）
        cards, rc = self._filter_feature_flag(cards, context)
        reason_counts.update(rc)

        logger.info(
            f"[HardFilterChain] 硬过滤完成: "
            f"candidates={len(cards)}, filtered_reasons={reason_counts}"
        )
        return HardFilterResult(cards=cards, filtered_reason_counts=reason_counts)

    # ========== 各层过滤实现 ==========

    def _filter_lifecycle(self, cards: list[SkillCard]) -> tuple[list[SkillCard], dict[str, int]]:
        """第1层：生命周期过滤。"""
        kept = [c for c in cards if c.lifecycle_status == "active"]
        filtered_count = len(cards) - len(kept)
        return kept, {"lifecycle": filtered_count} if filtered_count else {}

    def _filter_tenant(self, cards: list[SkillCard], ctx: HardFilterContext) -> tuple[list[SkillCard], dict[str, int]]:
        """第2层：租户过滤 + 依赖级租户校验（§10.3.1 约束5）。"""
        kept: list[SkillCard] = []
        tenant_filtered = 0
        dep_tenant_filtered = 0

        for card in cards:
            # tenant_overrides=false → 显式禁用
            if card.tenant_overrides.get(ctx.tenant_id) is False:
                tenant_filtered += 1
                continue

            # allowed_tenants 非空且不含当前租户，且 tenant_overrides 未显式启用
            if (
                card.allowed_tenants
                and ctx.tenant_id not in card.allowed_tenants
                and card.tenant_overrides.get(ctx.tenant_id) is not True
            ):
                tenant_filtered += 1
                continue

            # 依赖级租户校验（§10.3.1 约束5）：
            # required_dependency 的 allowed_tenants 必须全部包含当前租户
            dep_blocked = False
            for dep_id in card.required_dependency_ids:
                dep_card = self._snapshot.get_card(dep_id)
                if dep_card is None:
                    continue  # 依赖不存在由 dependency_health 层处理
                if (
                    dep_card.allowed_tenants
                    and ctx.tenant_id not in dep_card.allowed_tenants
                    and dep_card.tenant_overrides.get(ctx.tenant_id) is not True
                ):
                    dep_blocked = True
                    break
            if dep_blocked:
                dep_tenant_filtered += 1
                continue

            kept.append(card)

        result: dict[str, int] = {}
        if tenant_filtered:
            result["tenant"] = tenant_filtered
        if dep_tenant_filtered:
            result["dependency_tenant"] = dep_tenant_filtered
        return kept, result

    def _filter_permission(self, cards: list[SkillCard], ctx: HardFilterContext) -> tuple[list[SkillCard], dict[str, int]]:
        """第3层：权限过滤。"""
        if not ctx.permissions:
            return cards, {}
        perm_set = set(ctx.permissions)
        kept = [c for c in cards if set(c.required_permissions).issubset(perm_set)]
        filtered_count = len(cards) - len(kept)
        return kept, {"permission": filtered_count} if filtered_count else {}

    def _filter_route_capability(self, cards: list[SkillCard], ctx: HardFilterContext) -> tuple[list[SkillCard], dict[str, int]]:
        """第4层：route capability 过滤。

        route_target 决定需要的 capability 集合，Skill 的 capabilities 必须有交集。
        chitchat 不需要任何 Skill → 全部过滤。
        """
        needed = _ROUTE_CAPABILITY_MAP.get(ctx.route_target)
        if needed is None:
            return cards, {}  # 未知 route_target 不过滤

        if not needed:
            # chitchat：不需要 Skill
            filtered_count = len(cards)
            return [], {"route_capability": filtered_count} if filtered_count else {}

        kept = [c for c in cards if set(c.capabilities) & needed]
        filtered_count = len(cards) - len(kept)
        return kept, {"route_capability": filtered_count} if filtered_count else {}

    def _filter_language(self, cards: list[SkillCard], ctx: HardFilterContext) -> tuple[list[SkillCard], dict[str, int]]:
        """第5层：语言过滤。"""
        kept = [c for c in cards if not c.languages or ctx.locale in c.languages]
        filtered_count = len(cards) - len(kept)
        return kept, {"language": filtered_count} if filtered_count else {}

    def _filter_dependency_health(self, cards: list[SkillCard]) -> tuple[list[SkillCard], dict[str, int]]:
        """第6层：依赖健康过滤。"""
        kept = [c for c in cards if c.dependencies_healthy]
        filtered_count = len(cards) - len(kept)
        return kept, {"dependency_health": filtered_count} if filtered_count else {}

    def _filter_runtime_capability(self, cards: list[SkillCard], ctx: HardFilterContext) -> tuple[list[SkillCard], dict[str, int]]:
        """第7层：runtime capability gate（§10.3.1，请求级 agent_config 工具开关）。

        请求级禁用优先于 Catalog 级启用（§10.3.1 约束1）。
        """
        gate = RuntimeCapabilityGate()
        kept, filtered = gate.filter(cards, ctx.agent_config)
        result: dict[str, int] = {}
        for reason, count in filtered.items():
            if count:
                result[reason] = count
        return kept, result

    def _filter_feature_flag(self, cards: list[SkillCard], ctx: HardFilterContext) -> tuple[list[SkillCard], dict[str, int]]:
        """第8层：feature flag / canary / rollout 过滤。

        P3 阶段未构建 manifests_by_id 时（get_manifest 返回 None），
        视为无 canary/rollout 限制，直接放行（降级行为）。
        """
        kept: list[SkillCard] = []
        canary_filtered = 0
        rollout_filtered = 0

        for card in cards:
            manifest = self._snapshot.get_manifest(card.skill_id)
            if manifest is None:
                # 无 Manifest → 无 governance 信息，放行（降级）
                kept.append(card)
                continue

            gov = manifest.governance

            # canary：canary_tenants 非空且当前租户不在 canary 列表 → 过滤
            if gov.canary_tenants and ctx.tenant_id not in gov.canary_tenants:
                canary_filtered += 1
                continue

            # rollout：rollout_percentage < 100 → 按概率过滤
            # 用 skill_id + tenant_id 做确定性 hash，保证同租户同 Skill 结果稳定
            if gov.rollout_percentage < 100.0:
                hash_input = f"{card.skill_id}:{ctx.tenant_id}"
                hash_val = int(hash(hash_input) % 100)
                if hash_val >= gov.rollout_percentage:
                    rollout_filtered += 1
                    continue

            kept.append(card)

        result: dict[str, int] = {}
        if canary_filtered:
            result["canary_excluded"] = canary_filtered
        if rollout_filtered:
            result["rollout_excluded"] = rollout_filtered
        return kept, result


class RuntimeCapabilityGate:
    """请求级工具开关过滤（设计文档 v1.1 §10.3.1）。

    读取 ``agent_config``，过滤依赖被禁用工具的 Skill。
    与 Catalog 级 feature_flag 区别：本 gate 按会话变化，每次请求重新评估。

    项目硬约束（project_memory）：
        - db_tool / ReAct 可按会话通过 agent_config 启用/禁用
        - db_tool 禁用时，database 模式路由到 rag_tool，hybrid 模式跳过 db_tool
        - ReAct 禁用时，react_planner 路由到 plan_executor
    """

    def filter(self, candidates: list[SkillCard], agent_config: dict) -> tuple[list[SkillCard], dict[str, int]]:
        """过滤依赖被禁用工具的 Skill。

        Args:
            candidates: 硬过滤前的候选 SkillCard
            agent_config: 请求级 agent_config

        Returns:
            (保留的 cards, 过滤原因计数)
        """
        db_config = agent_config.get("db_tool", {}) or {}
        react_config = agent_config.get("react", {}) or {}
        db_enabled = db_config.get("enabled", True) if isinstance(db_config, dict) else True
        react_enabled = react_config.get("enabled", False) if isinstance(react_config, dict) else False

        filtered: list[SkillCard] = []
        db_disabled_count = 0
        react_disabled_count = 0

        for card in candidates:
            # db_tool 禁用 → 依赖 database 能力的 Skill 不可用（§10.3.1 约束2）
            if not db_enabled and "database" in card.capabilities:
                db_disabled_count += 1
                continue
            # db_tool 禁用 → data 类 Skill 整体失效（data_spec 绑定的数据库目标无法访问）
            if not db_enabled and card.skill_type == "data":
                db_disabled_count += 1
                continue
            # ReAct 禁用 → react_only Skill 不可用（§10.3.1 约束3）
            if not react_enabled and card.execution_mode == "react_only":
                react_disabled_count += 1
                continue

            filtered.append(card)

        reason_counts: dict[str, int] = {}
        if db_disabled_count:
            reason_counts["runtime_db_disabled"] = db_disabled_count
        if react_disabled_count:
            reason_counts["runtime_react_disabled"] = react_disabled_count
        return filtered, reason_counts
