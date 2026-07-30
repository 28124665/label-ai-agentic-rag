"""用户反馈模块。

按 docs/报告可信治理与人机协同补强设计.md §8 设计：
- ReportFeedback：单条用户反馈（点赞/点踩/编辑/评论/请求重新生成）
- FeedbackCollector：内存级反馈采集器
- summarize_optimization_suggestions：从反馈生成 Skill 优化建议

生产环境应替换为持久化实现（PostgreSQL / MongoDB）。
"""
from agent.langgraph.feedback.collector import (
    ALLOWED_FEEDBACK_ACTIONS,
    FEEDBACK_ACTION_COMMENT,
    FEEDBACK_ACTION_DISLIKE,
    FEEDBACK_ACTION_EDIT,
    FEEDBACK_ACTION_LIKE,
    FEEDBACK_ACTION_REQUEST_REGENERATE,
    FEEDBACK_TARGET_CLAIM,
    FEEDBACK_TARGET_REPORT,
    FEEDBACK_TARGET_SECTION,
    FeedbackCollector,
    OptimizationSuggestion,
    ReportFeedback,
    get_default_feedback_collector,
)


__all__ = [
    "ALLOWED_FEEDBACK_ACTIONS",
    "FEEDBACK_ACTION_COMMENT",
    "FEEDBACK_ACTION_DISLIKE",
    "FEEDBACK_ACTION_EDIT",
    "FEEDBACK_ACTION_LIKE",
    "FEEDBACK_ACTION_REQUEST_REGENERATE",
    "FEEDBACK_TARGET_CLAIM",
    "FEEDBACK_TARGET_REPORT",
    "FEEDBACK_TARGET_SECTION",
    "FeedbackCollector",
    "OptimizationSuggestion",
    "ReportFeedback",
    "get_default_feedback_collector",
]
