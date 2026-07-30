"""FeedbackCollector 单元测试（按 docs §8）。"""
from __future__ import annotations

import pytest

from agent.langgraph.feedback import (
    FEEDBACK_ACTION_COMMENT,
    FEEDBACK_ACTION_DISLIKE,
    FEEDBACK_ACTION_EDIT,
    FEEDBACK_ACTION_LIKE,
    FEEDBACK_ACTION_REQUEST_REGENERATE,
    FEEDBACK_TARGET_CLAIM,
    FEEDBACK_TARGET_REPORT,
    FEEDBACK_TARGET_SECTION,
    FeedbackCollector,
)


class TestFeedbackCollector:
    """FeedbackCollector 行为测试。"""

    def test_collect_like(self):
        """记录点赞反馈。"""
        collector = FeedbackCollector()
        fb = collector.collect(
            report_id="rpt_001",
            action=FEEDBACK_ACTION_LIKE,
            user_id="user_001",
            target_type=FEEDBACK_TARGET_REPORT,
        )
        assert fb["action"] == FEEDBACK_ACTION_LIKE
        assert fb["report_id"] == "rpt_001"
        assert fb["user_id"] == "user_001"
        assert fb["feedback_id"].startswith("fb_")

    def test_collect_dislike_with_target(self):
        """记录点踩反馈，关联 Claim。"""
        collector = FeedbackCollector()
        fb = collector.collect(
            report_id="rpt_001",
            action=FEEDBACK_ACTION_DISLIKE,
            user_id="user_001",
            target_type=FEEDBACK_TARGET_CLAIM,
            target_id="claim_001",
            section_id="sec_001",
            claim_id="claim_001",
            comment="数字对不上",
        )
        assert fb["claim_id"] == "claim_001"
        assert fb["comment"] == "数字对不上"
        assert fb["target_type"] == FEEDBACK_TARGET_CLAIM

    def test_invalid_action_raises(self):
        """非法 action 抛 ValueError。"""
        collector = FeedbackCollector()
        with pytest.raises(ValueError):
            collector.collect(
                report_id="rpt_001",
                action="invalid_action",
                user_id="user_001",
            )

    def test_get_feedbacks_by_report(self):
        """按 report_id 过滤。"""
        collector = FeedbackCollector()
        collector.collect(
            report_id="rpt_001",
            action=FEEDBACK_ACTION_LIKE,
            user_id="u1",
        )
        collector.collect(
            report_id="rpt_002",
            action=FEEDBACK_ACTION_LIKE,
            user_id="u1",
        )
        feedbacks = collector.get_feedbacks(report_id="rpt_001")
        assert len(feedbacks) == 1
        assert feedbacks[0]["report_id"] == "rpt_001"

    def test_max_history_truncation(self):
        """max_history 限制生效。"""
        collector = FeedbackCollector(max_history=2)
        for i in range(5):
            collector.collect(
                report_id=f"rpt_{i:03d}",
                action=FEEDBACK_ACTION_LIKE,
                user_id="u1",
            )
        all_fb = collector.get_feedbacks()
        assert len(all_fb) == 2
        # 保留最新 2 条
        assert all_fb[0]["report_id"] == "rpt_003"
        assert all_fb[1]["report_id"] == "rpt_004"

    def test_clear(self):
        """clear 清空所有反馈。"""
        collector = FeedbackCollector()
        collector.collect(
            report_id="rpt_001",
            action=FEEDBACK_ACTION_LIKE,
            user_id="u1",
        )
        assert len(collector.get_feedbacks()) == 1
        collector.clear()
        assert len(collector.get_feedbacks()) == 0


class TestSummarizeOptimizationSuggestions:
    """summarize_optimization_suggestions 触发规则测试。"""

    def test_dislike_threshold_triggers_suggestion(self):
        """dislike 超过阈值触发 low_quality_claim 建议。"""
        collector = FeedbackCollector()
        for _ in range(3):
            collector.collect(
                report_id="rpt_001",
                action=FEEDBACK_ACTION_DISLIKE,
                user_id="u1",
                target_type=FEEDBACK_TARGET_CLAIM,
                target_id="claim_001",
            )
        suggestions = collector.summarize_optimization_suggestions(
            skill_id="skill_001",
            dislike_threshold=3,
        )
        assert len(suggestions) == 1
        assert suggestions[0]["suggestion_type"] == "low_quality_claim"
        assert suggestions[0]["target_id"] == "claim_001"
        assert suggestions[0]["feedback_count"] == 3

    def test_edit_threshold_triggers_suggestion(self):
        """edit 超过阈值触发 high_edit_count 建议。"""
        collector = FeedbackCollector()
        for _ in range(2):
            collector.collect(
                report_id="rpt_001",
                action=FEEDBACK_ACTION_EDIT,
                user_id="u1",
                target_type=FEEDBACK_TARGET_SECTION,
                target_id="sec_001",
            )
        suggestions = collector.summarize_optimization_suggestions(
            edit_threshold=2,
        )
        assert len(suggestions) == 1
        assert suggestions[0]["suggestion_type"] == "high_edit_count"

    def test_regenerate_threshold_triggers_suggestion(self):
        """request_regenerate 超过阈值触发 regenerate_request 建议。"""
        collector = FeedbackCollector()
        for _ in range(2):
            collector.collect(
                report_id="rpt_001",
                action=FEEDBACK_ACTION_REQUEST_REGENERATE,
                user_id="u1",
                target_type=FEEDBACK_TARGET_REPORT,
            )
        suggestions = collector.summarize_optimization_suggestions(
            regenerate_threshold=2,
        )
        assert len(suggestions) == 1
        assert suggestions[0]["suggestion_type"] == "regenerate_request"

    def test_no_suggestions_when_below_threshold(self):
        """低于阈值不触发建议。"""
        collector = FeedbackCollector()
        collector.collect(
            report_id="rpt_001",
            action=FEEDBACK_ACTION_DISLIKE,
            user_id="u1",
            target_type=FEEDBACK_TARGET_CLAIM,
            target_id="claim_001",
        )
        suggestions = collector.summarize_optimization_suggestions(
            dislike_threshold=3,
        )
        assert len(suggestions) == 0

    def test_suggestions_sorted_by_score(self):
        """建议按 score 降序。"""
        collector = FeedbackCollector()
        # claim_001: 5 次 dislike
        for _ in range(5):
            collector.collect(
                report_id="rpt_001",
                action=FEEDBACK_ACTION_DISLIKE,
                user_id="u1",
                target_type=FEEDBACK_TARGET_CLAIM,
                target_id="claim_001",
            )
        # claim_002: 3 次 dislike
        for _ in range(3):
            collector.collect(
                report_id="rpt_001",
                action=FEEDBACK_ACTION_DISLIKE,
                user_id="u1",
                target_type=FEEDBACK_TARGET_CLAIM,
                target_id="claim_002",
            )
        suggestions = collector.summarize_optimization_suggestions(
            dislike_threshold=3,
        )
        assert len(suggestions) == 2
        # claim_001 分数更高，应排前
        assert suggestions[0]["target_id"] == "claim_001"
        assert suggestions[1]["target_id"] == "claim_002"
