"""用户反馈模块（按 docs §8）。

收集用户对报告的反馈（点赞 / 点踩 / 编辑 / 评论），
并生成 Skill 优化建议用于治理侧改进 Skill。
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Optional, TypedDict

logger = logging.getLogger(__name__)


# ========== 反馈动作定义 ==========
FEEDBACK_ACTION_LIKE = "like"  # 点赞（章节 / Claim）
FEEDBACK_ACTION_DISLIKE = "dislike"  # 点踩
FEEDBACK_ACTION_EDIT = "edit"  # 编辑内容
FEEDBACK_ACTION_COMMENT = "comment"  # 评论
FEEDBACK_ACTION_REQUEST_REGENERATE = "request_regenerate"  # 请求重新生成

ALLOWED_FEEDBACK_ACTIONS = {
    FEEDBACK_ACTION_LIKE,
    FEEDBACK_ACTION_DISLIKE,
    FEEDBACK_ACTION_EDIT,
    FEEDBACK_ACTION_COMMENT,
    FEEDBACK_ACTION_REQUEST_REGENERATE,
}

# 反馈目标类型
FEEDBACK_TARGET_REPORT = "report"  # 整份报告
FEEDBACK_TARGET_SECTION = "section"  # 章节
FEEDBACK_TARGET_CLAIM = "claim"  # Claim
FEEDBACK_TARGET_CHART = "chart"  # 图表
FEEDBACK_TARGET_TABLE = "table"  # 表格


class ReportFeedback(TypedDict, total=False):
    """单条用户反馈（按 docs §8.1）。"""

    feedback_id: str
    report_id: str
    artifact_version: str
    user_id: str
    tenant_id: str
    action: str  # like / dislike / edit / comment / request_regenerate
    target_type: str  # report / section / claim / chart / table
    target_id: str  # section_id / claim_id / chart_id / table_id / report_id
    section_id: str
    claim_id: str
    comment: str
    edited_content: str
    created_at: str  # ISO8601


class OptimizationSuggestion(TypedDict, total=False):
    """Skill 优化建议（按 docs §8.2）。"""

    suggestion_id: str
    skill_id: str
    suggestion_type: str  # "low_quality_claim" / "high_edit_count" / "dislike_cluster" / "regenerate_request"
    target_id: str
    target_type: str
    description: str
    score: float  # 严重程度或影响范围
    feedback_count: int
    created_at: str
    status: str  # pending / applied / rejected


def _make_feedback_id(report_id: str, target_id: str, user_id: str, created_at: str) -> str:
    """生成反馈 ID。"""
    import hashlib

    raw = f"{report_id}|{target_id}|{user_id}|{created_at}"
    return "fb_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


class FeedbackCollector:
    """反馈采集器（按 docs §8.1）。

    线程安全的内存实现（生产环境应替换为持久化存储）。
    """

    def __init__(self, max_history: int = 10000):
        """初始化。

        Args:
            max_history: 内存中最多保留的反馈条数（防止内存爆炸）
        """
        self._feedbacks: list[ReportFeedback] = []
        self._max_history = max_history

    def collect(
        self,
        report_id: str,
        action: str,
        user_id: str,
        target_type: str = FEEDBACK_TARGET_REPORT,
        target_id: str = "",
        section_id: str = "",
        claim_id: str = "",
        comment: str = "",
        edited_content: str = "",
        tenant_id: str = "",
        artifact_version: str = "",
    ) -> ReportFeedback:
        """记录一条反馈（按 docs §8.1）。

        Args:
            report_id: 报告 ID
            action: 反馈动作
            user_id: 用户 ID
            target_type: 目标类型
            target_id: 目标 ID
            section_id: 关联章节 ID（可选）
            claim_id: 关联 Claim ID（可选）
            comment: 文字评论
            edited_content: 编辑后内容
            tenant_id: 租户 ID
            artifact_version: artifact 版本

        Returns:
            ReportFeedback: 构造的反馈条目
        """
        if action not in ALLOWED_FEEDBACK_ACTIONS:
            raise ValueError(
                f"非法的 feedback action: {action}，"
                f"允许: {sorted(ALLOWED_FEEDBACK_ACTIONS)}"
            )

        now_iso = datetime.utcnow().isoformat() + "Z"
        feedback_id = _make_feedback_id(report_id, target_id, user_id, now_iso)
        feedback: ReportFeedback = {
            "feedback_id": feedback_id,
            "report_id": report_id,
            "artifact_version": artifact_version,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id or report_id,
            "section_id": section_id,
            "claim_id": claim_id,
            "comment": comment,
            "edited_content": edited_content,
            "created_at": now_iso,
        }
        self._feedbacks.append(feedback)
        # 防止内存无限增长
        if len(self._feedbacks) > self._max_history:
            self._feedbacks = self._feedbacks[-self._max_history :]
        logger.info(
            f"[FeedbackCollector] 收到反馈: {action} on {target_type}={target_id} "
            f"report={report_id} user={user_id}"
        )
        return feedback

    def get_feedbacks(
        self,
        report_id: str = "",
        target_type: str = "",
        target_id: str = "",
    ) -> list[ReportFeedback]:
        """查询反馈（按 report_id / target 过滤）。"""
        results: list[ReportFeedback] = []
        for fb in self._feedbacks:
            if report_id and fb.get("report_id") != report_id:
                continue
            if target_type and fb.get("target_type") != target_type:
                continue
            if target_id and fb.get("target_id") != target_id:
                continue
            results.append(fb)
        return results

    def summarize_optimization_suggestions(
        self,
        skill_id: str = "",
        dislike_threshold: int = 3,
        edit_threshold: int = 2,
        regenerate_threshold: int = 2,
    ) -> list[OptimizationSuggestion]:
        """生成 Skill 优化建议（按 docs §8.2）。

        触发规则：
        1. 同一 Claim 收到 dislike >= dislike_threshold → 建议标低质量 Claim
        2. 同一 section 收到 edit >= edit_threshold → 建议审视模板
        3. 同一 report 收到 request_regenerate >= regenerate_threshold → 建议审视 Skill

        Args:
            skill_id: 关联的 Skill ID（用于 suggestions）
            dislike_threshold: dislike 触发阈值
            edit_threshold: edit 触发阈值
            regenerate_threshold: request_regenerate 触发阈值

        Returns:
            list[OptimizationSuggestion]
        """
        suggestions: list[OptimizationSuggestion] = []

        # 1) Claim dislike 聚合
        claim_dislike: Counter[str] = Counter()
        for fb in self._feedbacks:
            if fb.get("action") != FEEDBACK_ACTION_DISLIKE:
                continue
            if fb.get("target_type") != FEEDBACK_TARGET_CLAIM:
                continue
            claim_dislike[fb.get("target_id", "")] += 1
        for claim_id, count in claim_dislike.items():
            if count >= dislike_threshold:
                suggestions.append(
                    _build_suggestion(
                        skill_id=skill_id,
                        suggestion_type="low_quality_claim",
                        target_id=claim_id,
                        target_type=FEEDBACK_TARGET_CLAIM,
                        description=(
                            f"Claim {claim_id} 收到 {count} 次点踩，"
                            f"超过阈值 {dislike_threshold}，建议标记为低质量"
                        ),
                        score=float(count) / dislike_threshold,
                        feedback_count=count,
                    )
                )

        # 2) Section edit 聚合
        section_edit: Counter[str] = Counter()
        for fb in self._feedbacks:
            if fb.get("action") != FEEDBACK_ACTION_EDIT:
                continue
            if fb.get("target_type") != FEEDBACK_TARGET_SECTION:
                continue
            section_edit[fb.get("target_id", "")] += 1
        for sec_id, count in section_edit.items():
            if count >= edit_threshold:
                suggestions.append(
                    _build_suggestion(
                        skill_id=skill_id,
                        suggestion_type="high_edit_count",
                        target_id=sec_id,
                        target_type=FEEDBACK_TARGET_SECTION,
                        description=(
                            f"章节 {sec_id} 被编辑 {count} 次，"
                            f"超过阈值 {edit_threshold}，建议审视 Skill 模板"
                        ),
                        score=float(count) / edit_threshold,
                        feedback_count=count,
                    )
                )

        # 3) Report regenerate 聚合
        report_regen: Counter[str] = Counter()
        for fb in self._feedbacks:
            if fb.get("action") != FEEDBACK_ACTION_REQUEST_REGENERATE:
                continue
            if fb.get("target_type") != FEEDBACK_TARGET_REPORT:
                continue
            report_regen[fb.get("report_id", "")] += 1
        for rpt_id, count in report_regen.items():
            if count >= regenerate_threshold:
                suggestions.append(
                    _build_suggestion(
                        skill_id=skill_id,
                        suggestion_type="regenerate_request",
                        target_id=rpt_id,
                        target_type=FEEDBACK_TARGET_REPORT,
                        description=(
                            f"报告 {rpt_id} 被请求重新生成 {count} 次，"
                            f"超过阈值 {regenerate_threshold}，建议整体审视"
                        ),
                        score=float(count) / regenerate_threshold,
                        feedback_count=count,
                    )
                )

        # 按 score 降序
        suggestions.sort(key=lambda s: s.get("score", 0.0), reverse=True)
        return suggestions

    def clear(self) -> None:
        """清空所有反馈（测试 / 重置用）。"""
        self._feedbacks = []


def _build_suggestion(
    skill_id: str,
    suggestion_type: str,
    target_id: str,
    target_type: str,
    description: str,
    score: float,
    feedback_count: int,
) -> OptimizationSuggestion:
    """构造 OptimizationSuggestion。"""
    import hashlib

    raw = f"{skill_id}|{suggestion_type}|{target_id}"
    suggestion_id = "sg_" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
    return {
        "suggestion_id": suggestion_id,
        "skill_id": skill_id,
        "suggestion_type": suggestion_type,
        "target_id": target_id,
        "target_type": target_type,
        "description": description,
        "score": round(score, 4),
        "feedback_count": feedback_count,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "status": "pending",
    }


# ========== 全局默认实例（测试 / 单进程使用） ==========
_default_collector: Optional[FeedbackCollector] = None


def get_default_feedback_collector() -> FeedbackCollector:
    """获取全局默认 FeedbackCollector（单例）。"""
    global _default_collector
    if _default_collector is None:
        _default_collector = FeedbackCollector()
    return _default_collector
