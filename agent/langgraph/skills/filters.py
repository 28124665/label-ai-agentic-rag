"""Stage 0-1 硬过滤实现（P2，§10.3 / §18.3）。

设计文档 §18.3 filters.py 职责：
    1. Stage 0 规范化：query 文本清洗、locale 提取
    2. Stage 1 硬过滤：lifecycle / tenant / permission / runtime capability / latency_class
    3. exclusion_rules 执行
    4. conflicts_with(CANDIDATE) 排除
    5. 输出过滤后的 list[SkillCard]，供 Stage 2-4 使用

与 P0 resolver._prefilter_skill 的关系（§18.3 约束1）：
    P2 阶段 resolver 降级为 HardFilter 的薄包装，避免逻辑重复。
    P0 的 _prefilter_skill 作用于 ReportSkill（SkillBase），
    P2 的 HardFilter 作用于 SkillCard（检索视图），但校验内核一致。

类比 Java：
    ``HardFilter`` ≈ ``@Component`` 校验器，
    ``apply`` ≈ ``filter(Predicate)``,  返回通过过滤的候选列表。
    被排除的候选和排除原因记录在 ``FilterResult`` 中，供 Routing Trace 使用。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.langgraph.skills.card import SkillCard
from agent.langgraph.skills.governance import (
    PermissionIntersection,
    SkillResolverLifecycleFilter,
    SKILL_STATUS_ACTIVE,
)

logger = logging.getLogger(__name__)


@dataclass
class FilteredCandidate:
    """被过滤的候选记录（供 Routing Trace 审计）。"""

    card: SkillCard
    excluded: bool
    reason: str = ""


@dataclass
class FilterResult:
    """硬过滤结果。

    Attributes:
        passed: 通过过滤的 SkillCard 列表
        excluded: 被排除的候选记录（含排除原因，供 Routing Trace）
    """

    passed: list[SkillCard] = field(default_factory=list)
    excluded: list[FilteredCandidate] = field(default_factory=list)

    @property
    def has_candidates(self) -> bool:
        """是否还有可用候选。"""
        return len(self.passed) > 0


class HardFilter:
    """Stage 0-1 硬过滤器（§10.3 / §18.3）。

    过滤顺序（§10.3）：
        lifecycle → tenant → permission → runtime capability → latency_class

    设计原则（§5.3 合法候选优先）：
        只有合法候选可以进入语义召回。
        显式 skill_id / report_type 路径也必须经过此过滤（§10.3 约束）。

    与 P0 SkillResolver._prefilter_skill 的区别：
        - P0 作用于 ReportSkill（SkillBase），返回单一 reason
        - P2 作用于 SkillCard，批量过滤并记录全部排除原因
        - 校验内核一致（lifecycle/tenant/permission/capability）
    """

    def __init__(
        self,
        lifecycle_filter: SkillResolverLifecycleFilter | None = None,
    ) -> None:
        self._lifecycle_filter = lifecycle_filter or SkillResolverLifecycleFilter()

    def apply(
        self,
        cards: list[SkillCard],
        context: "FilterContext",
    ) -> FilterResult:
        """批量硬过滤（§10.3）。

        Args:
            cards: 待过滤的 SkillCard 列表
            context: 过滤上下文（tenant_id / user_permissions / agent_config / ...）

        Returns:
            FilterResult: 通过过滤的候选 + 被排除的记录
        """
        result = FilterResult()
        for card in cards:
            reason = self._check(card, context)
            if reason is None:
                result.passed.append(card)
            else:
                result.excluded.append(
                    FilteredCandidate(card=card, excluded=True, reason=reason)
                )
                logger.debug(
                    "[HardFilter] 候选 '%s' 被排除: %s", card.skill_id, reason
                )
        return result

    def check_single(self, card: SkillCard, context: "FilterContext") -> str | None:
        """单个 SkillCard 硬过滤（供显式 skill_id/report_type 路径使用）。

        Returns:
            None 表示通过，字符串表示拒绝原因
        """
        return self._check(card, context)

    def _check(self, card: SkillCard, context: "FilterContext") -> str | None:
        """执行全部硬过滤检查（§10.3 顺序）。"""
        # 1. lifecycle（从 card.lifecycle_status 读取）
        if not self._is_lifecycle_selectable(card, context):
            return "LIFECYCLE_NOT_SELECTABLE"

        # 2. tenant（allowed_tenants 白名单）
        if not self._tenant_allowed(card.allowed_tenants, context.tenant_id):
            return "TENANT_NOT_ALLOWED"

        # 3. permission（required_permissions ⊆ user_permissions）
        if not self._permission_ok(card, context):
            return "PERMISSION_DENIED"

        # 4. runtime capability gate（agent_config.db_tool.enabled / react.enabled）
        if not self._runtime_capability_ok(card, context):
            return "CAPABILITY_DISABLED"

        # 5. latency_class 过滤（batch 报告不用于交互式查询）
        if not self._latency_ok(card, context):
            return "LATENCY_MISMATCH"

        # 6. exclusion_rules 执行
        if self._excluded_by_rules(card, context):
            return "EXCLUSION_RULE_MATCHED"

        return None

    # ========== 过滤器实现 ==========

    def _is_lifecycle_selectable(
        self, card: SkillCard, context: "FilterContext"
    ) -> bool:
        """生命周期过滤（§12.1）。

        从 card.lifecycle_status 读取（P2 阶段从 enabled 推导）。
        active 直接放行；approved 检查 tenant_overrides；其余拒绝。
        """
        status = card.lifecycle_status
        if status == "active":
            return True
        if status == "approved":
            return self._lifecycle_filter.is_selectable(
                skill_status=SKILL_STATUS_ACTIVE,
                tenant_id=context.tenant_id,
                tenant_overrides=card.tenant_overrides or None,
            )
        # draft / pending_review / deprecated / archived 不可选
        return False

    @staticmethod
    def _tenant_allowed(allowed_tenants: list[str], tenant_id: str) -> bool:
        """租户白名单校验（§10.3 tenant 过滤层）。

        空列表 = 允许所有租户；非空 = 白名单。
        """
        if not allowed_tenants:
            return True
        return tenant_id in allowed_tenants

    @staticmethod
    def _permission_ok(card: SkillCard, context: "FilterContext") -> bool:
        """权限校验（§10.3 permission 过滤层，§12.2 权限约束）。

        required_permissions ⊆ user_permissions。
        未配置权限时默认放行（向后兼容）。
        """
        if context.user_permissions is None:
            return True
        return PermissionIntersection.is_skill_available(
            user_permissions=context.user_permissions,
            skill_required_permissions=card.required_permissions,
        )

    @staticmethod
    def _runtime_capability_ok(card: SkillCard, context: "FilterContext") -> bool:
        """runtime capability gate（§10.3.1 请求级工具开关过滤）。

        检查 agent_config 中的工具开关：
            - db_tool.enabled=False → 依赖 database 能力的 Skill 不可用
            - react.enabled=False → 依赖 react 能力的 Skill 不可用
        """
        config = context.agent_config or {}

        # db_tool 禁用检查
        db_tool_config = config.get("db_tool", {})
        db_enabled = (
            db_tool_config.get("enabled", True)
            if isinstance(db_tool_config, dict)
            else True
        )
        if not db_enabled and "database" in card.capabilities:
            return False

        # react 禁用检查（P2 阶段简化：capabilities 不含 react，跳过）
        # P3 阶段引入 execution_mode 后补充

        return True

    @staticmethod
    def _latency_ok(card: SkillCard, context: "FilterContext") -> bool:
        """latency_class 过滤（§18.3）。

        batch 报告不用于交互式查询（route_target != "report"）。
        interactive 报告始终通过。
        """
        if card.latency_class == "interactive":
            return True
        # batch 报告仅在 route_target="report" 时可用
        return context.route_target == "report"

    @staticmethod
    def _excluded_by_rules(card: SkillCard, context: "FilterContext") -> bool:
        """exclusion_rules 执行（§18.3）。

        检查 card.exclusion_rules 中的条件是否匹配当前上下文。
        阶段 1 exclusion_rules 为空，始终返回 False。
        """
        for rule in card.exclusion_rules:
            if not isinstance(rule, dict):
                continue
            # when_entity 条件匹配
            entity_field = rule.get("when_entity")
            equals_value = rule.get("equals")
            if entity_field and equals_value is not None:
                # 从 context.entities 获取实体值
                entities = context.entities or {}
                if entities.get(entity_field) == equals_value:
                    return True
        return False


@dataclass
class FilterContext:
    """硬过滤上下文（封装 SkillResolveContext 的相关字段）。

    避免直接依赖 SkillResolveContext（P2 阶段 SkillResolveContext 可能演进），
    只提取过滤所需的字段。

    Attributes:
        tenant_id: 租户 ID
        user_permissions: 用户权限列表（None=未配置，默认放行）
        agent_config: Agent 配置（含 db_tool/react 等开关）
        route_target: 路由目标（rag/database/hybrid/web/report/chitchat）
        entities: 实体提取结果（供 exclusion_rules 匹配）
    """

    tenant_id: str = ""
    user_permissions: list[str] | None = None
    agent_config: dict | None = None
    route_target: str = ""
    entities: dict | None = None

    @classmethod
    def from_resolve_context(cls, context) -> "FilterContext":
        """从 SkillResolveContext 构造 FilterContext。

        Args:
            context: SkillResolveContext 实例

        Returns:
            FilterContext
        """
        config = context.agent_config or {}
        permissions = config.get("permissions")
        route_decision = context.route_decision or {}
        metadata = route_decision.get("metadata", {}) if isinstance(route_decision, dict) else {}
        return cls(
            tenant_id=context.tenant_id,
            user_permissions=permissions if isinstance(permissions, list) else None,
            agent_config=config,
            route_target=route_decision.get("target", "") if isinstance(route_decision, dict) else "",
            entities=metadata.get("entities") if isinstance(metadata, dict) else None,
        )
