"""审批链状态机单元测试（设计文档 §12.1.1）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.langgraph.skills.approval import (
    ApprovalChain,
    ApprovalState,
    ApprovalStateMachine,
    ApprovalStep,
)


# ========== 辅助构造 ==========


def _make_state(
    steps: list[ApprovalStep] | None = None,
    status: str = "draft",
) -> ApprovalState:
    """构造测试用 ApprovalState。"""
    if steps is None:
        steps = [ApprovalStep(role="reviewer", approver_ids=["alice"])]
    return ApprovalState(chain=ApprovalChain(steps=steps), status=status)


# ========== submit 测试 ==========


def test_submit_draft_to_pending():
    """draft → pending_review。"""
    sm = ApprovalStateMachine()
    state = _make_state()
    state = sm.submit(state)
    assert state.status == "pending_review"
    assert state.chain.current_step == 0
    assert state.started_at != ""


def test_submit_non_draft_ignored():
    """非 draft 状态 submit 不变更。"""
    sm = ApprovalStateMachine()
    state = _make_state(status="active")
    state = sm.submit(state)
    assert state.status == "active"


def test_submit_empty_chain_ignored():
    """空审批链 submit 不变更。"""
    sm = ApprovalStateMachine()
    state = ApprovalState(chain=ApprovalChain(steps=[]), status="draft")
    state = sm.submit(state)
    assert state.status == "draft"


# ========== approve 测试 ==========


def test_approve_advances_step():
    """approve 推进 current_step。"""
    sm = ApprovalStateMachine()
    steps = [
        ApprovalStep(role="data_owner", approver_ids=["alice"]),
        ApprovalStep(role="security", approver_ids=["bob"]),
    ]
    state = _make_state(steps=steps)
    state = sm.submit(state)
    state = sm.approve(state, "alice")
    assert state.chain.current_step == 1
    assert state.status == "pending_review"


def test_approve_final_step_activates():
    """终审通过后再次 approve 激活上线。"""
    sm = ApprovalStateMachine()
    state = _make_state(steps=[ApprovalStep(role="reviewer", approver_ids=["alice"])])
    state = sm.submit(state)
    state = sm.approve(state, "alice")  # 终审完成 → approved
    assert state.status == "approved"
    state = sm.approve(state, "alice")  # 激活 → active
    assert state.status == "active"


# ========== reject 测试 ==========


def test_reject_sets_rejected():
    """reject 打回重审：状态 draft，记录 decision=rejected。"""
    sm = ApprovalStateMachine()
    state = _make_state()
    state = sm.submit(state)
    state = sm.reject(state, "alice", reason="不合规")
    assert state.status == "draft"
    assert state.chain.current_step == 0
    assert state.approvals[-1].decision == "rejected"
    assert state.approvals[-1].reason == "不合规"


# ========== withdraw 测试 ==========


def test_withdraw_sets_withdrawn():
    """withdraw 从 active 撤回下线 → deprecated。"""
    sm = ApprovalStateMachine()
    state = _make_state(status="active")
    state = sm.withdraw(state)
    assert state.status == "deprecated"


# ========== check_timeout 测试 ==========


def test_timeout_expired():
    """超时自动打回：状态 draft，记录 decision=timeout。"""
    sm = ApprovalStateMachine()
    steps = [ApprovalStep(role="reviewer", approver_ids=["alice"], timeout_hours=1)]
    state = _make_state(steps=steps)
    state = sm.submit(state)
    # 模拟 updated_at 为 2 小时前
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    state.updated_at = old
    state = sm.check_timeout(state)
    assert state.status == "draft"
    assert state.approvals[-1].decision == "timeout"


# ========== 非法迁移测试 ==========


def test_invalid_transition_ignored():
    """非法迁移被忽略（approve 在 draft 状态不生效）。"""
    sm = ApprovalStateMachine()
    state = _make_state(status="draft")
    state = sm.approve(state, "alice")
    assert state.status == "draft"
