"""HumanReview 节点单元测试（按 docs §6）。"""
from __future__ import annotations

import pytest

from agent.langgraph.tools.report.human_review import (
    DEFAULT_LOW_CONFIDENCE_RATIO_THRESHOLD,
    DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    HIGH_SENSITIVITY_SKILL_TYPES,
    HumanReviewNode,
    build_initial_human_review_result,
    should_require_human_review,
    transition_publish_status,
)


def _make_artifact(
    claims: list[dict] | None = None,
    publish_status: str = "draft",
) -> dict:
    return {
        "claims": claims or [],
        "sections": [],
        "charts": [],
        "tables": [],
        "publish_status": publish_status,
    }


def _make_claim(
    claim_id: str = "c1",
    confidence: float = 0.9,
    needs_human_review: bool = False,
    support_status: str = "supported",
) -> dict:
    return {
        "claim_id": claim_id,
        "text": "test",
        "claim_type": "fact",
        "evidence_refs": ["ev_001"],
        "confidence": confidence,
        "needs_human_review": needs_human_review,
        "support_status": support_status,
    }


class TestShouldRequireHumanReview:
    """should_require_human_review 判定测试。"""

    def test_publish_policy_requires(self):
        """publish_policy.require_human_review=True 触发。"""
        requires, reason = should_require_human_review(
            artifact=_make_artifact(),
            publish_policy={"require_human_review": True},
        )
        assert requires
        assert "publish_policy" in reason

    def test_high_sensitivity_skill(self):
        """高敏 Skill 类型触发。"""
        requires, reason = should_require_human_review(
            artifact=_make_artifact(),
            skill_type="cost_analysis",
        )
        assert requires
        assert "cost_analysis" in reason

    def test_high_sensitivity_skill_all_types(self):
        """所有高敏 Skill 类型都触发。"""
        for s in HIGH_SENSITIVITY_SKILL_TYPES:
            requires, reason = should_require_human_review(
                artifact=_make_artifact(),
                skill_type=s,
            )
            assert requires, f"{s} 应触发"

    def test_low_confidence_ratio_above_threshold(self):
        """低置信度占比超过阈值触发。"""
        # 4 个 Claim，2 个低置信（0.5），比例 50% > 30%
        claims = [
            _make_claim(claim_id="c1", confidence=0.5),
            _make_claim(claim_id="c2", confidence=0.5),
            _make_claim(claim_id="c3", confidence=0.9),
            _make_claim(claim_id="c4", confidence=0.9),
        ]
        requires, reason = should_require_human_review(
            artifact=_make_artifact(claims=claims),
        )
        assert requires
        assert "0.50" in reason

    def test_low_confidence_ratio_below_threshold(self):
        """低置信度占比低于阈值不触发。"""
        # 4 个 Claim，1 个低置信，比例 25% < 30%
        claims = [
            _make_claim(claim_id="c1", confidence=0.5),
            _make_claim(claim_id="c2", confidence=0.9),
            _make_claim(claim_id="c3", confidence=0.9),
            _make_claim(claim_id="c4", confidence=0.9),
        ]
        requires, reason = should_require_human_review(
            artifact=_make_artifact(claims=claims),
        )
        assert not requires

    def test_no_claims_no_review(self):
        """无 Claim 不触发。"""
        requires, reason = should_require_human_review(
            artifact=_make_artifact(claims=[]),
        )
        assert not requires
        assert "无 Claim" in reason

    def test_normal_report_no_review(self):
        """普通报告不触发。"""
        claims = [_make_claim(claim_id="c1", confidence=0.95)]
        requires, _ = should_require_human_review(
            artifact=_make_artifact(claims=claims),
        )
        assert not requires


class TestTransitionPublishStatus:
    """transition_publish_status 状态机测试。"""

    def test_approve_with_publish(self):
        """approve + publish_allowed → published。"""
        review = {
            "action": "approve",
            "approved": True,
            "publish_allowed": True,
        }
        new_status, reason = transition_publish_status("pending_review", review)
        assert new_status == "published"
        assert "批准" in reason

    def test_approve_without_publish(self):
        """approve + publish_allowed=False → approved。"""
        review = {
            "action": "approve",
            "approved": True,
            "publish_allowed": False,
        }
        new_status, reason = transition_publish_status("pending_review", review)
        assert new_status == "approved"

    def test_reject(self):
        """reject → rejected。"""
        review = {"action": "reject", "approved": False, "publish_allowed": False}
        new_status, reason = transition_publish_status("pending_review", review)
        assert new_status == "rejected"

    def test_request_regenerate(self):
        """request_regenerate → draft。"""
        review = {
            "action": "request_regenerate",
            "approved": False,
            "publish_allowed": False,
        }
        new_status, reason = transition_publish_status("pending_review", review)
        assert new_status == "draft"

    def test_comment_keeps_status(self):
        """comment 不改变状态。"""
        review = {
            "action": "comment",
            "approved": False,
            "publish_allowed": False,
        }
        new_status, _ = transition_publish_status("pending_review", review)
        assert new_status == "pending_review"

    def test_no_review_keeps_status(self):
        """无 review_result 保持现状。"""
        new_status, _ = transition_publish_status("approved", None)
        assert new_status == "approved"


class TestBuildInitialHumanReviewResult:
    """build_initial_human_review_result 初始审核结果。"""

    def test_requires_review(self):
        """requires=True → 未批准。"""
        result = build_initial_human_review_result(
            requires=True,
            reason="测试",
        )
        assert result["approved"] is False
        assert result["publish_allowed"] is False
        assert len(result["comments"]) >= 1

    def test_no_review_required(self):
        """requires=False → 自动 approve。"""
        result = build_initial_human_review_result(requires=False, reason="")
        assert result["approved"] is True
        assert result["publish_allowed"] is True


class TestHumanReviewNode:
    """HumanReviewNode 端到端测试。"""

    def test_evaluate_no_review_needed(self):
        """普通 Claim → approved。"""
        node = HumanReviewNode()
        claims = [_make_claim(confidence=0.95)]
        status, reason, result = node.evaluate(
            artifact=_make_artifact(claims=claims),
        )
        assert status == "approved"
        assert result["approved"] is True

    def test_evaluate_high_sensitivity(self):
        """高敏 Skill → pending_review。"""
        node = HumanReviewNode()
        claims = [_make_claim(confidence=0.95)]
        status, reason, result = node.evaluate(
            artifact=_make_artifact(claims=claims),
            skill_type="cost_analysis",
        )
        assert status == "pending_review"
        assert "cost_analysis" in reason
        assert result["approved"] is False

    def test_evaluate_low_confidence(self):
        """低置信度 → pending_review。"""
        node = HumanReviewNode()
        claims = [
            _make_claim(claim_id="c1", confidence=0.5, needs_human_review=True),
            _make_claim(claim_id="c2", confidence=0.5, needs_human_review=True),
            _make_claim(claim_id="c3", confidence=0.9),
        ]
        status, reason, result = node.evaluate(
            artifact=_make_artifact(claims=claims),
        )
        assert status == "pending_review"
        assert result["approved"] is False

    def test_apply_review_approve(self):
        """apply_review + approve → published。"""
        node = HumanReviewNode()
        artifact = _make_artifact(publish_status="pending_review")
        review = {
            "action": "approve",
            "approved": True,
            "publish_allowed": True,
            "reviewer_id": "user_1",
            "reviewed_at": "2026-01-01",
            "comments": [],
            "edited_sections": [],
        }
        new_status, updated = node.apply_review(artifact, review)
        assert new_status == "published"
        assert updated["publish_status"] == "published"

    def test_apply_review_reject(self):
        """apply_review + reject → rejected。"""
        node = HumanReviewNode()
        artifact = _make_artifact(publish_status="pending_review")
        review = {
            "action": "reject",
            "approved": False,
            "publish_allowed": False,
            "reviewer_id": "user_1",
            "reviewed_at": "2026-01-01",
            "comments": [],
            "edited_sections": [],
        }
        new_status, updated = node.apply_review(artifact, review)
        assert new_status == "rejected"
