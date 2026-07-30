"""Report Tool 数据模型。

按 docs/报告生成Tool设计.md 第 4 节定义的所有数据结构：
- ReportToolInput：报告工具输入
- ReportToolOutput：报告工具输出
- ReportArtifact：报告产物
- ReportSection：报告章节
- ChartSpec：图表规格
- TableSpec：表格规格
- VerificationResult：验证结果
- ReportPlan：章节规划

所有模型使用 TypedDict 保持与现有 AgentState 风格一致。
"""
from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict


# ========== 输入输出 ==========
ReportType = Literal[
    "business_analysis",
    "quality_analysis",
    "operations_analysis",
    "factory_performance",
    "delivery_analysis",
    "production_daily",
    "cost_analysis",
    "trend_analysis",
    "comparison",
    "knowledge_summary",
    "management_briefing",
]
"""支持的报告类型。"""

ReportFormat = Literal["markdown", "html", "pdf", "xlsx", "pptx"]
"""支持的报告导出格式（阶段一仅实现 markdown / html）。"""

ReportLanguage = Literal["zh_CN", "zh_TW", "en"]
"""报告语言。"""

SectionType = Literal[
    "overview",
    "analysis",
    "data",
    "conclusion",
    "recommendation",
    "appendix",
]
"""章节类型。"""


class ReportToolInput(TypedDict, total=False):
    """ReportTool 输入定义。"""

    title: str
    report_type: ReportType
    skill_id: str
    data_skill_id: str
    retrieval_skill_id: str
    skill_set: Any
    objective: str
    audience: str
    format: ReportFormat
    language: ReportLanguage
    tenant_id: str
    user_id: str
    query_lang: str
    evidence: list[dict]
    tool_results: list[dict]
    route_decision: Optional[dict]
    conversation_history: list[dict]
    max_sections: int
    max_charts: int
    max_tables: int
    include_appendix: bool
    include_raw_data: bool
    include_evidence_refs: bool
    export_options: dict
    template_id: str
    style_config: dict


class ReportToolOutput(TypedDict, total=False):
    """ReportTool 输出定义。"""

    success: bool
    error_message: str
    error_code: str
    artifact: dict
    summary: str
    quality_score: float
    section_count: int
    chart_count: int
    table_count: int
    evidence_count: int
    generation_time_ms: int
    export_time_ms: int
    file_uri: str
    download_url: str
    partial: bool  # 是否部分成功（部分章节生成）
    publish_mode: Literal["full", "partial", "summary_only"]
    missing_required_evidence: list[str]


class ReportArtifact(TypedDict, total=False):
    """报告产物。"""

    report_id: str
    title: str
    report_type: ReportType
    format: ReportFormat
    language: ReportLanguage
    objective: str
    audience: str
    summary: str
    sections: list[dict]
    charts: list[dict]
    tables: list[dict]
    appendix: list[dict]
    evidence_refs: list[str]
    source_summary: list[dict]
    verification_result: dict
    metadata: dict
    tenant_id: str
    user_id: str
    created_at: str
    file_uri: str
    file_size_bytes: int
    partial: bool
    failed_sections: list[str]
    coverage_score: float


class ReportSection(TypedDict, total=False):
    """报告章节。"""

    section_id: str
    title: str
    order: int
    section_type: SectionType
    content: str
    evidence_refs: list[str]
    chart_refs: list[str]
    table_refs: list[str]
    confidence: float
    llm_call_id: Optional[str]  # 用于审计


# ========== 图表与表格 ==========
class ChartSpec(TypedDict, total=False):
    """图表规格。"""

    chart_id: str
    chart_type: Literal["bar", "line", "pie", "table", "kpi"]
    title: str
    x_axis: str
    y_axis: str
    series: list[dict]
    data: list[dict]
    evidence_refs: list[str]
    unit: str
    notes: str


class TableSpec(TypedDict, total=False):
    """表格规格。"""

    table_id: str
    title: str
    columns: list[str]
    rows: list[list[Any]]
    evidence_refs: list[str]
    max_rows: int


# ========== 验证结果 ==========
class VerificationIssue(TypedDict, total=False):
    """验证问题。"""

    code: str
    severity: Literal["low", "medium", "high", "critical"]
    message: str
    section_id: Optional[str]
    chart_id: Optional[str]
    table_id: Optional[str]
    field: Optional[str]


class VerificationResult(TypedDict, total=False):
    """验证结果。"""

    passed: bool
    score: float  # 0.0 - 1.0
    issues: list[VerificationIssue]
    warnings: list[str]
    checks_performed: list[str]  # 已执行的检查名称
    checked_at: str


# ========== 报告计划 ==========
class ReportPlan(TypedDict, total=False):
    """报告章节计划。"""

    title: str
    report_type: ReportType
    template_id: str
    sections: list[dict]
    estimated_charts: int
    estimated_tables: int
    warnings: list[str]
