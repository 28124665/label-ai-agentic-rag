"""Skill 生命周期治理与权限交集（按 docs §7 / §8）。

Skill 治理解决三个问题：
1. **生命周期状态机** — draft → pending_review → approved → active → deprecated
   - 只有 active 或租户明确灰度放开的 approved 才能被 Resolver 选择
2. **权限交集** — user_permissions ∩ skill.required_permissions
   - 交集必须等于 skill.required_permissions（用户必须拥有 Skill 要求的全部权限）
3. **变更审计** — change_log 记录所有变更

设计原则：
- Skill 是企业级受治理资产，不应随意上线 / 下线
- deprecated 状态必须可追溯原因
- 任何状态变更必须经过显式审批
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional, TypedDict

logger = logging.getLogger(__name__)


# ========== 状态定义 ==========
# Skill 生命周期状态机（按 docs §7.2）
SKILL_STATUS_DRAFT = "draft"  # 草稿，未提交
SKILL_STATUS_PENDING_REVIEW = "pending_review"  # 审核中
SKILL_STATUS_APPROVED = "approved"  # 已通过审核，未正式启用
SKILL_STATUS_ACTIVE = "active"  # 已启用（生产可用）
SKILL_STATUS_DEPRECATED = "deprecated"  # 已废弃（保留配置不再使用）

# 状态机迁移表（按 docs §7.2 / §7.3）
SKILL_STATUS_TRANSITIONS: dict[str, set[str]] = {
    SKILL_STATUS_DRAFT: {SKILL_STATUS_PENDING_REVIEW, SKILL_STATUS_DEPRECATED},
    SKILL_STATUS_PENDING_REVIEW: {
        SKILL_STATUS_APPROVED,
        SKILL_STATUS_DRAFT,  # 被打回
        SKILL_STATUS_DEPRECATED,
    },
    SKILL_STATUS_APPROVED: {
        SKILL_STATUS_ACTIVE,
        SKILL_STATUS_DEPRECATED,
    },
    SKILL_STATUS_ACTIVE: {
        SKILL_STATUS_DEPRECATED,  # 任何时候都可下线
    },
    SKILL_STATUS_DEPRECATED: set(),  # 终态，不可再变更
}

# 哪些状态可被 Resolver 选择（按 docs §7.3）
SELECTABLE_SKILL_STATUSES = {SKILL_STATUS_ACTIVE, SKILL_STATUS_APPROVED}


class ChangeLogEntry(TypedDict, total=False):
    """Skill 变更审计日志条目（按 docs §7.4）。"""

    timestamp: str  # ISO8601
    actor: str  # 操作人
    action: str  # 动作：create / submit / approve / activate / deprecate / edit
    from_status: str
    to_status: str
    reason: str
    diff: dict[str, Any]  # 字段级 diff


class SkillGovernance(TypedDict, total=False):
    """Skill 治理元数据（按 docs §7）。

    与 ReportSkill / DataSkill / RetrievalSkill 配合使用，
    治理元数据存储在 Skill 配置的 governance 字段。
    """

    skill_id: str
    version: str
    status: str  # 当前状态
    created_by: str
    approved_by: str
    created_at: str  # ISO8601
    approved_at: str
    activated_at: str
    deprecated_at: str
    deprecation_reason: str
    change_log: list[ChangeLogEntry]  # 完整审计链
    # 灰度开关：哪些 tenant 可以使用 approved 但未 active 的 Skill
    tenant_overrides: dict[str, bool]


# ========== 状态机校验 ==========
def is_valid_skill_status_transition(
    from_status: str,
    to_status: str,
) -> bool:
    """判断状态迁移是否合法（按 docs §7.2）。

    Args:
        from_status: 起始状态
        to_status: 目标状态

    Returns:
        bool: 是否允许迁移
    """
    if from_status == to_status:
        # 状态不变允许（同状态 update）
        return True
    allowed = SKILL_STATUS_TRANSITIONS.get(from_status, set())
    return to_status in allowed


def transition_skill_status(
    skill_id: str,
    from_status: str,
    to_status: str,
    actor: str,
    reason: str = "",
    diff: dict[str, Any] | None = None,
) -> Optional[ChangeLogEntry]:
    """状态机迁移并记录日志。

    Args:
        skill_id: Skill ID
        from_status: 起始状态
        to_status: 目标状态
        actor: 操作人
        reason: 变更原因
        diff: 字段级 diff

    Returns:
        ChangeLogEntry 或 None（不允许迁移）
    """
    if not is_valid_skill_status_transition(from_status, to_status):
        logger.warning(
            f"[SkillGovernance] 非法状态迁移: {skill_id} {from_status} -> {to_status}"
        )
        return None
    from datetime import datetime

    entry: ChangeLogEntry = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "actor": actor,
        "action": _status_to_action(to_status),
        "from_status": from_status,
        "to_status": to_status,
        "reason": reason,
        "diff": diff or {},
    }
    return entry


def _status_to_action(status: str) -> str:
    """状态 → 动作名。"""
    return {
        SKILL_STATUS_PENDING_REVIEW: "submit",
        SKILL_STATUS_APPROVED: "approve",
        SKILL_STATUS_ACTIVE: "activate",
        SKILL_STATUS_DEPRECATED: "deprecate",
    }.get(status, "edit")


# ========== 生命周期过滤器 ==========
class SkillResolverLifecycleFilter:
    """Skill 生命周期过滤器（按 docs §7.3）。

    依据 Skill.status 与租户开关决定 Skill 是否可被 Resolver 选择。
    """

    def __init__(self, default_allow_approved: bool = False):
        """初始化。

        Args:
            default_allow_approved: 是否默认放开 approved 状态
                - True: 所有 approved Skill 都被视作可被 Resolver 选择（灰度）
                - False: 仅当 tenant_overrides[tenant_id] == True 时才放开
        """
        self._default_allow_approved = default_allow_approved

    def is_selectable(
        self,
        skill_status: str,
        tenant_id: str = "",
        tenant_overrides: dict[str, bool] | None = None,
    ) -> bool:
        """判断 Skill 是否可被当前租户选择。

        Args:
            skill_status: Skill 当前状态
            tenant_id: 租户 ID
            tenant_overrides: 灰度配置（仅 approved 起作用）

        Returns:
            bool: 是否可选择
        """
        if skill_status in SELECTABLE_SKILL_STATUSES:
            if skill_status == SKILL_STATUS_ACTIVE:
                return True
            # approved 状态：检查租户开关
            if self._default_allow_approved:
                return True
            if tenant_id and tenant_overrides:
                return bool(tenant_overrides.get(tenant_id, False))
            return False
        return False


# ========== 权限交集 ==========
class PermissionIntersection:
    """权限交集计算（按 docs §7.5）。

    user_permissions ∩ skill.required_permissions 必须等于 skill.required_permissions
    也就是说：用户必须拥有 Skill 要求的全部权限，Skill 才可用。

    注意：这里"权限交集"指的是"用户权限覆盖 Skill 所需权限"，
    与字面意义的"集合交"略有不同——它实质是 superset 检查。
    """

    @staticmethod
    def compute(
        user_permissions: Iterable[str],
        skill_required_permissions: Iterable[str],
    ) -> tuple[set[str], bool]:
        """计算权限交集与是否满足。

        Args:
            user_permissions: 用户拥有的权限列表
            skill_required_permissions: Skill 要求的权限列表

        Returns:
            tuple: (effective_permissions, is_sufficient)
            - effective_permissions: 实际可用于此 Skill 的权限
            - is_sufficient: True 表示用户权限完全覆盖 Skill 要求
        """
        user_set = set(user_permissions or [])
        required_set = set(skill_required_permissions or [])
        effective = user_set & required_set
        is_sufficient = required_set.issubset(user_set)
        return effective, is_sufficient

    @staticmethod
    def is_skill_available(
        user_permissions: Iterable[str],
        skill_required_permissions: Iterable[str],
    ) -> bool:
        """判断用户是否可使用此 Skill。

        Args:
            user_permissions: 用户权限列表
            skill_required_permissions: Skill 要求的权限列表

        Returns:
            bool: True 表示用户可使用此 Skill
        """
        _, is_sufficient = PermissionIntersection.compute(
            user_permissions, skill_required_permissions
        )
        return is_sufficient


# ========== Skill 治理综合判定 ==========
def is_skill_usable(
    skill_status: str,
    skill_required_permissions: Iterable[str],
    user_permissions: Iterable[str],
    tenant_id: str = "",
    tenant_overrides: dict[str, bool] | None = None,
    lifecycle_filter: SkillResolverLifecycleFilter | None = None,
) -> tuple[bool, str]:
    """综合判定 Skill 是否对当前用户 / 租户可用（按 docs §7.3 / §7.5）。

    Args:
        skill_status: Skill 状态
        skill_required_permissions: Skill 所需权限
        user_permissions: 用户权限
        tenant_id: 租户 ID
        tenant_overrides: 灰度配置
        lifecycle_filter: 生命周期过滤器

    Returns:
        tuple: (is_usable, reason)
    """
    filter_ = lifecycle_filter or SkillResolverLifecycleFilter()

    # 1. 生命周期
    if not filter_.is_selectable(
        skill_status=skill_status,
        tenant_id=tenant_id,
        tenant_overrides=tenant_overrides,
    ):
        return False, f"Skill 状态 {skill_status} 不可被选择"

    # 2. 权限
    if not PermissionIntersection.is_skill_available(
        user_permissions, skill_required_permissions
    ):
        return False, "用户权限不满足 Skill 要求"

    return True, ""


def summarize_governance(governance: SkillGovernance) -> dict[str, Any]:
    """生成治理摘要（便于 UI 展示）。"""
    return {
        "skill_id": governance.get("skill_id", ""),
        "version": governance.get("version", ""),
        "status": governance.get("status", ""),
        "created_by": governance.get("created_by", ""),
        "approved_by": governance.get("approved_by", ""),
        "created_at": governance.get("created_at", ""),
        "approved_at": governance.get("approved_at", ""),
        "activated_at": governance.get("activated_at", ""),
        "deprecated_at": governance.get("deprecated_at", ""),
        "deprecation_reason": governance.get("deprecation_reason", ""),
        "change_log_count": len(governance.get("change_log", []) or []),
        "tenant_overrides": governance.get("tenant_overrides", {}) or {},
    }
