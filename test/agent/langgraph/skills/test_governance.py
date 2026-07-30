"""Skill 治理模块单元测试（按 docs §7）。"""
from __future__ import annotations

import pytest

from agent.langgraph.skills.governance import (
    SELECTABLE_SKILL_STATUSES,
    SKILL_STATUS_ACTIVE,
    SKILL_STATUS_APPROVED,
    SKILL_STATUS_DEPRECATED,
    SKILL_STATUS_DRAFT,
    SKILL_STATUS_PENDING_REVIEW,
    SKILL_STATUS_TRANSITIONS,
    PermissionIntersection,
    SkillResolverLifecycleFilter,
    is_skill_usable,
    is_valid_skill_status_transition,
    summarize_governance,
    transition_skill_status,
)


class TestSkillStatusTransitions:
    """Skill 状态机迁移校验。"""

    def test_draft_to_pending_review(self):
        """draft → pending_review 合法。"""
        assert is_valid_skill_status_transition(
            SKILL_STATUS_DRAFT, SKILL_STATUS_PENDING_REVIEW
        )

    def test_pending_review_to_approved(self):
        """pending_review → approved 合法。"""
        assert is_valid_skill_status_transition(
            SKILL_STATUS_PENDING_REVIEW, SKILL_STATUS_APPROVED
        )

    def test_pending_review_to_draft_back(self):
        """pending_review → draft 合法（被打回）。"""
        assert is_valid_skill_status_transition(
            SKILL_STATUS_PENDING_REVIEW, SKILL_STATUS_DRAFT
        )

    def test_approved_to_active(self):
        """approved → active 合法。"""
        assert is_valid_skill_status_transition(
            SKILL_STATUS_APPROVED, SKILL_STATUS_ACTIVE
        )

    def test_active_to_deprecated(self):
        """active → deprecated 合法。"""
        assert is_valid_skill_status_transition(
            SKILL_STATUS_ACTIVE, SKILL_STATUS_DEPRECATED
        )

    def test_draft_to_active_invalid(self):
        """draft → active 非法（必须经过 pending_review / approved）。"""
        assert not is_valid_skill_status_transition(
            SKILL_STATUS_DRAFT, SKILL_STATUS_ACTIVE
        )

    def test_deprecated_is_terminal(self):
        """deprecated 是终态，不能再变更。"""
        for to_status in [
            SKILL_STATUS_DRAFT,
            SKILL_STATUS_PENDING_REVIEW,
            SKILL_STATUS_APPROVED,
            SKILL_STATUS_ACTIVE,
        ]:
            assert not is_valid_skill_status_transition(
                SKILL_STATUS_DEPRECATED, to_status
            )

    def test_same_status_allowed(self):
        """同状态允许（用于 update）。"""
        for status in [
            SKILL_STATUS_DRAFT,
            SKILL_STATUS_PENDING_REVIEW,
            SKILL_STATUS_APPROVED,
            SKILL_STATUS_ACTIVE,
        ]:
            assert is_valid_skill_status_transition(status, status)


class TestTransitionSkillStatus:
    """transition_skill_status 记录日志。"""

    def test_record_change_log(self):
        """合法迁移返回 ChangeLogEntry。"""
        entry = transition_skill_status(
            skill_id="skill_001",
            from_status=SKILL_STATUS_DRAFT,
            to_status=SKILL_STATUS_PENDING_REVIEW,
            actor="alice",
            reason="提审",
        )
        assert entry is not None
        assert entry["from_status"] == SKILL_STATUS_DRAFT
        assert entry["to_status"] == SKILL_STATUS_PENDING_REVIEW
        assert entry["actor"] == "alice"
        assert entry["reason"] == "提审"
        assert "timestamp" in entry

    def test_invalid_transition_returns_none(self):
        """非法迁移返回 None。"""
        entry = transition_skill_status(
            skill_id="skill_001",
            from_status=SKILL_STATUS_DRAFT,
            to_status=SKILL_STATUS_ACTIVE,
            actor="alice",
        )
        assert entry is None


class TestSkillResolverLifecycleFilter:
    """Skill 生命周期过滤器。"""

    def test_active_selectable(self):
        """active 状态可选择。"""
        f = SkillResolverLifecycleFilter()
        assert f.is_selectable(SKILL_STATUS_ACTIVE)

    def test_approved_not_selectable_by_default(self):
        """approved 状态默认不可选择（需租户灰度）。"""
        f = SkillResolverLifecycleFilter(default_allow_approved=False)
        assert not f.is_selectable(SKILL_STATUS_APPROVED, tenant_id="t1")

    def test_approved_selectable_with_default(self):
        """approved 状态在 default_allow_approved=True 时可选择。"""
        f = SkillResolverLifecycleFilter(default_allow_approved=True)
        assert f.is_selectable(SKILL_STATUS_APPROVED, tenant_id="t1")

    def test_approved_selectable_with_tenant_override(self):
        """approved 状态在租户 override=True 时可选择。"""
        f = SkillResolverLifecycleFilter(default_allow_approved=False)
        assert f.is_selectable(
            SKILL_STATUS_APPROVED,
            tenant_id="t1",
            tenant_overrides={"t1": True},
        )

    def test_draft_not_selectable(self):
        """draft 状态不可选择。"""
        f = SkillResolverLifecycleFilter()
        assert not f.is_selectable(SKILL_STATUS_DRAFT)

    def test_pending_review_not_selectable(self):
        """pending_review 状态不可选择。"""
        f = SkillResolverLifecycleFilter()
        assert not f.is_selectable(SKILL_STATUS_PENDING_REVIEW)

    def test_deprecated_not_selectable(self):
        """deprecated 状态不可选择。"""
        f = SkillResolverLifecycleFilter()
        assert not f.is_selectable(SKILL_STATUS_DEPRECATED)


class TestPermissionIntersection:
    """权限交集计算。"""

    def test_full_cover_returns_true(self):
        """用户权限完全覆盖 Skill 要求时返回 True。"""
        effective, is_sufficient = PermissionIntersection.compute(
            user_permissions=["report:read", "report:generate", "quality:read"],
            skill_required_permissions=["report:generate"],
        )
        assert is_sufficient
        assert effective == {"report:generate"}

    def test_partial_cover_returns_false(self):
        """用户权限只覆盖部分时返回 False。"""
        effective, is_sufficient = PermissionIntersection.compute(
            user_permissions=["report:generate"],
            skill_required_permissions=["report:generate", "quality:read"],
        )
        assert not is_sufficient
        assert effective == {"report:generate"}

    def test_no_cover_returns_false(self):
        """用户权限与 Skill 要求完全不交时返回 False。"""
        effective, is_sufficient = PermissionIntersection.compute(
            user_permissions=["other:read"],
            skill_required_permissions=["report:generate"],
        )
        assert not is_sufficient
        assert effective == set()

    def test_empty_skill_requirements_returns_true(self):
        """Skill 无权限要求时一定可用。"""
        effective, is_sufficient = PermissionIntersection.compute(
            user_permissions=[],
            skill_required_permissions=[],
        )
        assert is_sufficient
        assert effective == set()

    def test_is_skill_available(self):
        """is_skill_available 简化调用。"""
        assert PermissionIntersection.is_skill_available(
            user_permissions=["a:read", "b:read"],
            skill_required_permissions=["a:read"],
        )
        assert not PermissionIntersection.is_skill_available(
            user_permissions=["a:read"],
            skill_required_permissions=["a:read", "b:read"],
        )


class TestIsSkillUsable:
    """综合判定 Skill 可用性。"""

    def test_active_and_full_cover(self):
        """active + 权限完全覆盖 → 可用。"""
        usable, reason = is_skill_usable(
            skill_status=SKILL_STATUS_ACTIVE,
            skill_required_permissions=["a:read"],
            user_permissions=["a:read", "b:read"],
        )
        assert usable
        assert reason == ""

    def test_deprecated_unusable(self):
        """deprecated 不可用。"""
        usable, reason = is_skill_usable(
            skill_status=SKILL_STATUS_DEPRECATED,
            skill_required_permissions=["a:read"],
            user_permissions=["a:read"],
        )
        assert not usable
        assert "deprecated" in reason

    def test_active_but_no_permission_unusable(self):
        """active 但权限不够 → 不可用。"""
        usable, reason = is_skill_usable(
            skill_status=SKILL_STATUS_ACTIVE,
            skill_required_permissions=["a:read"],
            user_permissions=["other:read"],
        )
        assert not usable
        assert "权限" in reason


class TestSummarizeGovernance:
    """治理摘要生成。"""

    def test_summarize(self):
        """摘要字段填充。"""
        governance = {
            "skill_id": "skill_001",
            "version": "1.0.0",
            "status": "active",
            "created_by": "alice",
            "approved_by": "bob",
            "change_log": [
                {"timestamp": "2026-01-01", "actor": "alice"},
            ],
            "tenant_overrides": {"t1": True},
        }
        summary = summarize_governance(governance)
        assert summary["skill_id"] == "skill_001"
        assert summary["status"] == "active"
        assert summary["change_log_count"] == 1
        assert summary["tenant_overrides"] == {"t1": True}


class TestSelectableStatuses:
    """SELECTABLE_SKILL_STATUSES 内容校验。"""

    def test_only_active_and_approved(self):
        """可被选择的状态只有 active / approved。"""
        assert SKILL_STATUS_ACTIVE in SELECTABLE_SKILL_STATUSES
        assert SKILL_STATUS_APPROVED in SELECTABLE_SKILL_STATUSES
        assert SKILL_STATUS_DRAFT not in SELECTABLE_SKILL_STATUSES
        assert SKILL_STATUS_PENDING_REVIEW not in SELECTABLE_SKILL_STATUSES
        assert SKILL_STATUS_DEPRECATED not in SELECTABLE_SKILL_STATUSES
