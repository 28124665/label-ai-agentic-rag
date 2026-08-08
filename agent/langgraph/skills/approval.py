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
"""审批链状态机（设计文档 §12.1.1）。

为 Skill 生命周期提供多级审批能力，与 ``governance.py`` 的状态机互补：
``governance.py`` 描述单状态迁移规则，本模块描述"多审批人 + 超时 + 链式推进"
的完整审批流程。

状态机（§12.1.1）：
    draft → pending_review → approved → active → deprecated → archived

    - submit:    draft → pending_review（启动审批链）
    - approve:   pending_review 链式推进；终审完成 → approved；
                 approved 状态下再次 approve → active（激活上线）
    - reject:    pending_review → draft（打回重审）
    - withdraw:  active → deprecated；deprecated → archived；其余活跃态 → deprecated
    - check_timeout: pending_review 超时 → draft（按当前步骤 timeout_hours 判定）

类比 Java：
    ``ApprovalStateMachine`` ≈ ``@Service`` 状态机服务，
    ``ApprovalState`` ≈ ``@Entity`` 聚合根，
    ``ApprovalStep`` / ``ApprovalChain`` / ``ApprovalRecord`` ≈ ``@Embeddable`` 值对象。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ========== 状态与迁移表 ==========
ApprovalStatus = Literal[
    "draft", "pending_review", "approved", "active", "deprecated", "archived"
]

# 合法状态迁移（§12.1.1）
APPROVAL_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_review", "deprecated"},
    "pending_review": {"approved", "draft", "deprecated"},
    "approved": {"active", "deprecated"},
    "active": {"deprecated"},
    "deprecated": {"archived"},
    "archived": set(),  # 终态
}


def _now_iso() -> str:
    """当前 UTC 时间 ISO8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ========== 值对象 ==========
class ApprovalStep(BaseModel):
    """单级审批步骤（§12.1.1）。

    Attributes:
        role: 审批角色（如 "data_owner" / "security_officer"）
        approver_ids: 该角色下有权审批的用户 ID 列表
        timeout_hours: 本步骤超时阈值（小时），超时由 ``check_timeout`` 处理
        required: 是否必需步骤（False = 可跳过）
    """

    role: str
    approver_ids: list[str] = Field(default_factory=list)
    timeout_hours: int = 48
    required: bool = True


class ApprovalChain(BaseModel):
    """审批链（多级步骤顺序推进）。

    Attributes:
        steps: 有序审批步骤列表
        current_step: 当前推进到的步骤下标
    """

    steps: list[ApprovalStep] = Field(default_factory=list)
    current_step: int = 0


class ApprovalRecord(BaseModel):
    """单条审批记录（审计用）。"""

    step_index: int
    approver_id: str
    decision: Literal["approved", "rejected", "timeout"]
    reason: str = ""
    timestamp: str = ""


class ApprovalState(BaseModel):
    """审批状态聚合根（§12.1.1）。

    Attributes:
        chain: 审批链
        status: 当前生命周期状态
        approvals: 已发生的审批记录（审计链）
        started_at: 审批启动时间（ISO8601）
        updated_at: 最近一次状态变更时间（也作为当前步骤计时基准）
    """

    chain: ApprovalChain = Field(default_factory=ApprovalChain)
    status: ApprovalStatus = "draft"
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    started_at: str = ""
    updated_at: str = ""


# ========== 状态机服务 ==========
class ApprovalStateMachine:
    """审批链状态机服务（§12.1.1）。

    所有方法原地变更 ``ApprovalState`` 并返回，便于调用方链式使用。
    非法迁移记录 warning 并跳过（不抛异常，保持与 ``governance.py`` 一致的宽容风格）。

    类比 Java 中的 ``@Service``：编排状态迁移 + 审计记录。
    """

    def submit(self, approval_state: ApprovalState) -> ApprovalState:
        """提交审批：draft → pending_review，启动审批链第 0 步。"""
        if approval_state.status != "draft":
            logger.warning(
                "[Approval] submit 仅允许从 draft 提交，当前状态=%s",
                approval_state.status,
            )
            return approval_state
        if not approval_state.chain.steps:
            logger.warning("[Approval] 审批链为空，无法提交")
            return approval_state
        approval_state.status = "pending_review"
        approval_state.chain.current_step = 0
        now = _now_iso()
        if not approval_state.started_at:
            approval_state.started_at = now
        approval_state.updated_at = now
        return approval_state

    def approve(
        self, approval_state: ApprovalState, approver_id: str
    ) -> ApprovalState:
        """审批通过。

        - ``pending_review``：记录当前步骤审批，步骤完成后推进；终审完成 → ``approved``
        - ``approved``：再次 approve 视为激活上线 → ``active``
        """
        if approval_state.status == "pending_review":
            return self._advance_chain(approval_state, approver_id)
        if approval_state.status == "approved":
            # 已通过审核，激活上线
            approval_state.status = "active"
            approval_state.updated_at = _now_iso()
            logger.info("[Approval] 激活上线: approver=%s", approver_id)
            return approval_state
        logger.warning(
            "[Approval] approve 仅允许在 pending_review/approved 状态调用，当前状态=%s",
            approval_state.status,
        )
        return approval_state

    def reject(
        self,
        approval_state: ApprovalState,
        approver_id: str,
        reason: str = "",
    ) -> ApprovalState:
        """审批驳回：pending_review → draft（打回重审）。"""
        if approval_state.status != "pending_review":
            logger.warning(
                "[Approval] reject 仅允许在 pending_review 状态调用，当前状态=%s",
                approval_state.status,
            )
            return approval_state
        step_index = approval_state.chain.current_step
        approval_state.approvals.append(ApprovalRecord(
            step_index=step_index,
            approver_id=approver_id,
            decision="rejected",
            reason=reason,
            timestamp=_now_iso(),
        ))
        approval_state.status = "draft"
        approval_state.chain.current_step = 0
        approval_state.updated_at = _now_iso()
        logger.info("[Approval] 驳回打回: approver=%s, reason=%s", approver_id, reason)
        return approval_state

    def withdraw(self, approval_state: ApprovalState) -> ApprovalState:
        """撤回下线。

        - ``active`` → ``deprecated``
        - ``deprecated`` → ``archived``（归档终态）
        - 其余活跃态（draft/pending_review/approved）→ ``deprecated``
        """
        status = approval_state.status
        if status == "active":
            approval_state.status = "deprecated"
        elif status == "deprecated":
            approval_state.status = "archived"
        elif status in ("draft", "pending_review", "approved"):
            approval_state.status = "deprecated"
        else:
            logger.warning("[Approval] withdraw 不允许从 %s 撤回", status)
            return approval_state
        approval_state.updated_at = _now_iso()
        logger.info("[Approval] 撤回: %s → %s", status, approval_state.status)
        return approval_state

    def check_timeout(self, approval_state: ApprovalState) -> ApprovalState:
        """超时检查：pending_review 当前步骤超时 → draft。

        以 ``updated_at`` 作为当前步骤计时基准（步骤推进时会刷新）。
        """
        if approval_state.status != "pending_review":
            return approval_state
        chain = approval_state.chain
        if chain.current_step >= len(chain.steps):
            return approval_state
        if not approval_state.updated_at:
            return approval_state
        step = chain.steps[chain.current_step]
        try:
            started = datetime.strptime(approval_state.updated_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return approval_state
        elapsed_hours = (datetime.now(timezone.utc) - started.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if elapsed_hours > step.timeout_hours:
            approval_state.approvals.append(ApprovalRecord(
                step_index=chain.current_step,
                approver_id="",
                decision="timeout",
                reason=f"步骤 {step.role} 超时（{step.timeout_hours}h）",
                timestamp=_now_iso(),
            ))
            approval_state.status = "draft"
            approval_state.chain.current_step = 0
            approval_state.updated_at = _now_iso()
            logger.warning(
                "[Approval] 审批超时打回: step=%s, elapsed=%.1fh",
                step.role, elapsed_hours,
            )
        return approval_state

    # ========== 内部方法 ==========
    def _advance_chain(
        self, approval_state: ApprovalState, approver_id: str
    ) -> ApprovalState:
        """推进审批链：记录审批 → 判定步骤完成 → 推进或终审。"""
        chain = approval_state.chain
        if chain.current_step >= len(chain.steps):
            return approval_state
        step = chain.steps[chain.current_step]

        # 校验审批人资格
        if step.approver_ids and approver_id not in step.approver_ids:
            logger.warning(
                "[Approval] 无权审批: approver=%s, step=%s, allowed=%s",
                approver_id, step.role, step.approver_ids,
            )
            return approval_state

        # 记录审批
        approval_state.approvals.append(ApprovalRecord(
            step_index=chain.current_step,
            approver_id=approver_id,
            decision="approved",
            timestamp=_now_iso(),
        ))

        # 判定当前步骤是否完成（必需步骤需全部 approver_ids 审批通过）
        if not self._step_satisfied(approval_state, chain.current_step):
            approval_state.updated_at = _now_iso()
            return approval_state

        # 步骤完成，推进
        chain.current_step += 1
        approval_state.updated_at = _now_iso()

        # 跳过非必需步骤
        while chain.current_step < len(chain.steps) and not chain.steps[chain.current_step].required:
            chain.current_step += 1

        # 终审完成
        if chain.current_step >= len(chain.steps):
            approval_state.status = "approved"
            logger.info("[Approval] 审批链全部通过: approver=%s", approver_id)
        else:
            logger.info(
                "[Approval] 推进至下一步: step=%s",
                chain.steps[chain.current_step].role,
            )
        return approval_state

    @staticmethod
    def _step_satisfied(approval_state: ApprovalState, step_index: int) -> bool:
        """判定指定步骤是否已满足完成条件（全部 approver_ids 已审批通过）。"""
        chain = approval_state.chain
        if step_index >= len(chain.steps):
            return True
        step = chain.steps[step_index]
        if not step.approver_ids:
            return True
        approved = {
            r.approver_id for r in approval_state.approvals
            if r.step_index == step_index and r.decision == "approved"
        }
        return set(step.approver_ids).issubset(approved)
