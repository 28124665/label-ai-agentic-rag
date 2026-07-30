"""Report Tool 模块入口。

按 docs/报告生成Tool设计.md 实现的报告/报表生成工具集。

提供：
- ReportTool: 主入口，串联 Planner / Generators / Verifier / Exporter / Storage
- ReportPlanner: 章节规划
- ReportVerifier: 验证器
- ReportExporter: 导出器（Markdown / HTML）
- LocalArtifactStorage: 本地文件存储
- SensitiveScanner: 敏感信息扫描
"""
from agent.langgraph.tools.report.exporter import (
    ReportExporter,
    render_html,
    render_markdown,
)
from agent.langgraph.tools.report.models import (
    ChartSpec,
    ReportArtifact,
    ReportFormat,
    ReportPlan,
    ReportSection,
    ReportToolInput,
    ReportToolOutput,
    ReportType,
    SectionType,
    TableSpec,
    VerificationIssue,
    VerificationResult,
)
from agent.langgraph.tools.report.planner import ReportPlanner
from agent.langgraph.tools.report.report_tool import (
    ALLOWED_FORMATS_STAGE1,
    ALLOWED_REPORT_TYPES,
    REPORT_CHART_BUILD_FAILED,
    REPORT_EVIDENCE_INSUFFICIENT,
    REPORT_EXPORT_FAILED,
    REPORT_INPUT_INVALID,
    REPORT_PLAN_FAILED,
    REPORT_SECTION_GENERATION_FAILED,
    REPORT_STORAGE_FAILED,
    REPORT_TABLE_BUILD_FAILED,
    REPORT_VERIFICATION_FAILED,
    ReportTool,
    get_report_tool,
)
from agent.langgraph.tools.report.sensitive_scanner import (
    SENSITIVE_PATTERNS,
    has_critical,
    has_high,
    scan_artifact,
    scan_text,
)
from agent.langgraph.tools.report.storage import (
    LocalArtifactStorage,
    generate_report_id,
    get_default_storage,
)
from agent.langgraph.tools.report.templates import (
    REPORT_TYPE_TITLES,
    SECTION_TEMPLATES,
    build_report_plan,
    get_default_title,
    get_template,
    list_report_types,
)
from agent.langgraph.tools.report.verifier import ReportVerifier

__all__ = [
    # 主类
    "ReportTool",
    "get_report_tool",
    # 模块
    "ReportPlanner",
    "ReportVerifier",
    "ReportExporter",
    "LocalArtifactStorage",
    "get_default_storage",
    "generate_report_id",
    # 工具函数
    "render_markdown",
    "render_html",
    "scan_text",
    "scan_artifact",
    "has_critical",
    "has_high",
    "build_report_plan",
    "get_template",
    "get_default_title",
    "list_report_types",
    # 数据模型
    "ReportToolInput",
    "ReportToolOutput",
    "ReportArtifact",
    "ReportSection",
    "ReportPlan",
    "ChartSpec",
    "TableSpec",
    "VerificationResult",
    "VerificationIssue",
    "ReportType",
    "ReportFormat",
    "SectionType",
    # 常量
    "ALLOWED_REPORT_TYPES",
    "ALLOWED_FORMATS_STAGE1",
    "REPORT_INPUT_INVALID",
    "REPORT_EVIDENCE_INSUFFICIENT",
    "REPORT_PLAN_FAILED",
    "REPORT_SECTION_GENERATION_FAILED",
    "REPORT_CHART_BUILD_FAILED",
    "REPORT_TABLE_BUILD_FAILED",
    "REPORT_VERIFICATION_FAILED",
    "REPORT_EXPORT_FAILED",
    "REPORT_STORAGE_FAILED",
    "SENSITIVE_PATTERNS",
    "SECTION_TEMPLATES",
    "REPORT_TYPE_TITLES",
]
