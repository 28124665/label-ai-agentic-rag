"""HumanReview 节点（按 docs §6）。

HumanReview 节点决定报告是否需要人工审核、审核动作、publish_status 转换。

支持审核动作：
- approve
- reject
- edit
- comment
- request_regenerate

强制进入 HumanReview 的规则（按 docs §6.3）：
1. 高敏 Skill（财报 / 成本 / 质量 / EHS / 人事）
2. publish_policy.require_human_review = true
3. 低置信度 Claim 超过阈值
"""
from __future__ import annotations

import logging
from typing import Any

from agent.langgraph.tools.report.models import (
    HumanReviewResult,
    ReportArtifact,
)

logger = logging.getLogger(__name__)


# 高敏 Skill 类型（按 docs §6.3）
HIGH_SENSITIVITY_SKILL_TYPES = {
    "financial_report",
    "cost_analysis",
    "quality_analysis",
    "ehs_report",
    "hr_report",
    "compliance_report",
}


# 低置信度触发人工审核阈值（按 docs §6.4）
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.7
# 低置信度 Claim 占比触发人工审核阈值
DEFAULT_LOW_CONFIDENCE_RATIO_THRESHOLD = 0.3


def should_require_human_review(
    artifact: dict,
    publish_policy: dict | None = None,
    skill_type: str = "",
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    low_confidence_ratio_threshold: float = DEFAULT_LOW_CONFIDENCE_RATIO_THRESHOLD,
) -> tuple[bool, str]:
    """判定报告是否需要人工审核（按 docs §6.3）。

    触发条件（任一满足即需审核）：
    1. publish_policy.require_human_review = true
    2. 高敏 Skill 类型
    3. 低置信度 Claim 占比超过阈值

    Args:
        artifact: ReportArtifact 字典
        publish_policy: 发布策略
        skill_type: 当前 Skill 类型
        low_confidence_threshold: 单条 Claim 置信度阈值
        low_confidence_ratio_threshold: 触发人工审核的低置信度 Claim 占比

    Returns:
        tuple: (requires_review, reason)
    """
    if publish_policy and publish_policy.get("require_human_review", False):
        return True, "publish_policy.require_human_review=true"

    if skill_type in HIGH_SENSITIVITY_SKILL_TYPES:
        return True, f"高敏 Skill 类型: {skill_type}"

    claims = artifact.get("claims", []) or []
    if not claims:
        return False, "无 Claim，跳过"

    low_conf_count = sum(
        1
        for c in claims
        if c.get("needs_human_review", False)
        or (c.get("confidence", 1.0) or 1.0) < low_confidence_threshold
    )
    ratio = low_conf_count / len(claims) if claims else 0.0
    if ratio > low_confidence_ratio_threshold:
        return (
            True,
            f"低置信度 Claim 占比 {ratio:.2f} 超过阈值 {low_confidence_ratio_threshold}",
        )

    return False, ""


def transition_publish_status(
    current_status: str,
    review_result: HumanReviewResult | None,
) -> tuple[str, str]:
    """根据审核结果转换 publish_status（按 docs §6.2 / §6.3）。

    状态机：
    draft → pending_review → approved → published
                                       → rejected
                                       → draft (request_regenerate)

    Args:
        current_status: 当前状态
        review_result: 审核结果

    Returns:
        tuple: (new_status, reason)
    """
    if not review_result:
        return current_status, ""

    action = review_result.get("action", "")
    approved = review_result.get("approved", False)
    publish_allowed = review_result.get("publish_allowed", False)

    if action == "approve" and approved:
        if publish_allowed:
            return "published", "用户已批准且允许发布"
        return "approved", "用户已批准，等待发布"
    if action == "reject":
        return "rejected", "用户已驳回"
    if action == "request_regenerate":
        return "draft", "用户请求重新生成"
    if action in ("edit", "comment"):
        # 编辑或评论后状态保持（draft 或 pending_review）
        return current_status, f"用户 {action}"

    return current_status, ""


def build_initial_human_review_result(
    requires: bool,
    reason: str,
    skill_type: str = "",
) -> HumanReviewResult:
    """构造初始 HumanReviewResult（用于触发审核时）。

    Args:
        requires: 是否需要审核
        reason: 触发原因
        skill_type: Skill 类型

    Returns:
        HumanReviewResult: 初始状态（无 reviewer）
    """
    if not requires:
        return {
            "action": "comment",
            "reviewer_id": "",
            "reviewed_at": "",
            "comments": [],
            "edited_sections": [],
            "approved": True,
            "publish_allowed": True,
        }
    return {
        "action": "comment",
        "reviewer_id": "",
        "reviewed_at": "",
        "comments": [
            {
                "reviewer_id": "system",
                "comment": f"触发人工审核: {reason}",
                "skill_type": skill_type,
            }
        ],
        "edited_sections": [],
        "approved": False,
        "publish_allowed": False,
    }


class HumanReviewNode:
    """HumanReview 节点（按 docs §6.1）。

    决策流程：
    1. 调用 should_require_human_review 判断是否需要审核
    2. 若需要：
       - publish_status = "pending_review"
       - 构造初始 HumanReviewResult
       - 返回等待审核的 artifact
    3. 若不需要：
       - publish_status = "approved"（自动通过）
       - 构造自动 approve 的 HumanReviewResult
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ):
        """初始化。

        Args:
            config: 配置字典
                - low_confidence_threshold: float
                - low_confidence_ratio_threshold: float
        """
        self._config = config or {}
        self._low_confidence_threshold = self._config.get(
            "low_confidence_threshold", DEFAULT_LOW_CONFIDENCE_THRESHOLD
        )
        self._low_confidence_ratio_threshold = self._config.get(
            "low_confidence_ratio_threshold",
            DEFAULT_LOW_CONFIDENCE_RATIO_THRESHOLD,
        )

    def evaluate(
        self,
        artifact: dict,
        publish_policy: dict | None = None,
        skill_type: str = "",
    ) -> tuple[str, str, HumanReviewResult]:
        """评估报告是否需要人工审核。

        Args:
            artifact: ReportArtifact 字典
            publish_policy: 发布策略
            skill_type: Skill 类型

        Returns:
            tuple: (publish_status, reason, human_review_result)
        """
        requires, reason = should_require_human_review(
            artifact=artifact,
            publish_policy=publish_policy,
            skill_type=skill_type,
            low_confidence_threshold=self._low_confidence_threshold,
            low_confidence_ratio_threshold=self._low_confidence_ratio_threshold,
        )

        review_result = build_initial_human_review_result(requires, reason, skill_type)
        if requires:
            publish_status = "pending_review"
        else:
            publish_status = "approved"
            review_result["approved"] = True
            review_result["publish_allowed"] = True

        return publish_status, reason, review_result

    def apply_review(
        self,
        artifact: dict,
        review_result: HumanReviewResult,
    ) -> tuple[str, dict]:
        """应用审核结果，更新 publish_status。

        Args:
            artifact: ReportArtifact 字典
            review_result: HumanReviewResult

        Returns:
            tuple: (new_publish_status, updated_artifact)
        """
        current_status = artifact.get("publish_status", "draft")
        new_status, reason = transition_publish_status(current_status, review_result)
        artifact["publish_status"] = new_status
        artifact["human_review_result"] = review_result
        if reason:
            artifact["publish_status_reason"] = reason
        return new_status, artifact
