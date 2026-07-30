"""报告模板与章节规划模板。

按 docs/报告生成Tool设计.md 第 2.3 / 6.5 节设计：
- 每种 report_type 对应一个章节模板
- 模板定义章节顺序、类型、所需 evidence 类型
- Planner 根据 evidence 实际内容筛选/调整模板
"""
from __future__ import annotations

import logging
from typing import Any

from agent.langgraph.tools.report.models import ReportPlan, ReportType

logger = logging.getLogger(__name__)


# ========== 章节模板（按 report_type 索引） ==========
# 每个章节包含:
#   section_id: 唯一标识
#   title: 章节标题
#   section_type: 章节类型（overview/analysis/data/conclusion/recommendation/appendix）
#   required_evidence_types: 所需 evidence 类型列表
#   required_min_count: 至少需要多少条 evidence
SECTION_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "business_analysis": [
        {
            "section_id": "overview",
            "title": "一、业务概览",
            "section_type": "overview",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "key_metrics",
            "title": "二、关键指标分析",
            "section_type": "data",
            "required_evidence_types": ["db"],
            "required_min_count": 1,
        },
        {
            "section_id": "trend",
            "title": "三、趋势分析",
            "section_type": "analysis",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "conclusion",
            "title": "四、结论",
            "section_type": "conclusion",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "recommendation",
            "title": "五、建议",
            "section_type": "recommendation",
            "required_evidence_types": ["rag"],
            "required_min_count": 0,
        },
    ],
    "quality_analysis": [
        {
            "section_id": "overview",
            "title": "一、总体概况",
            "section_type": "overview",
            "required_evidence_types": ["db"],
            "required_min_count": 1,
        },
        {
            "section_id": "trend",
            "title": "二、异常趋势分析",
            "section_type": "analysis",
            "required_evidence_types": ["db"],
            "required_min_count": 1,
        },
        {
            "section_id": "root_cause",
            "title": "三、原因分析",
            "section_type": "analysis",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "conclusion",
            "title": "四、结论",
            "section_type": "conclusion",
            "required_evidence_types": ["db"],
            "required_min_count": 1,
        },
        {
            "section_id": "recommendation",
            "title": "五、改进建议",
            "section_type": "recommendation",
            "required_evidence_types": ["rag", "db"],
            "required_min_count": 0,
        },
    ],
    "trend_analysis": [
        {
            "section_id": "overview",
            "title": "一、趋势概览",
            "section_type": "overview",
            "required_evidence_types": ["db"],
            "required_min_count": 1,
        },
        {
            "section_id": "trend",
            "title": "二、趋势详细分析",
            "section_type": "analysis",
            "required_evidence_types": ["db"],
            "required_min_count": 1,
        },
        {
            "section_id": "drivers",
            "title": "三、驱动因素",
            "section_type": "analysis",
            "required_evidence_types": ["db", "rag", "web"],
            "required_min_count": 1,
        },
        {
            "section_id": "conclusion",
            "title": "四、结论与预测",
            "section_type": "conclusion",
            "required_evidence_types": ["db"],
            "required_min_count": 1,
        },
    ],
    "comparison": [
        {
            "section_id": "overview",
            "title": "一、对比概览",
            "section_type": "overview",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "comparison_data",
            "title": "二、对比数据",
            "section_type": "data",
            "required_evidence_types": ["db"],
            "required_min_count": 1,
        },
        {
            "section_id": "analysis",
            "title": "三、对比分析",
            "section_type": "analysis",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "conclusion",
            "title": "四、结论",
            "section_type": "conclusion",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
    ],
    "knowledge_summary": [
        {
            "section_id": "overview",
            "title": "一、知识背景",
            "section_type": "overview",
            "required_evidence_types": ["rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "key_points",
            "title": "二、核心要点",
            "section_type": "data",
            "required_evidence_types": ["rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "deep_dive",
            "title": "三、深入分析",
            "section_type": "analysis",
            "required_evidence_types": ["rag", "db"],
            "required_min_count": 0,
        },
        {
            "section_id": "conclusion",
            "title": "四、总结",
            "section_type": "conclusion",
            "required_evidence_types": ["rag"],
            "required_min_count": 1,
        },
    ],
    "management_briefing": [
        {
            "section_id": "executive_summary",
            "title": "一、执行摘要",
            "section_type": "overview",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "key_findings",
            "title": "二、关键发现",
            "section_type": "data",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "risks",
            "title": "三、风险提示",
            "section_type": "analysis",
            "required_evidence_types": ["db", "rag"],
            "required_min_count": 1,
        },
        {
            "section_id": "recommendation",
            "title": "四、建议事项",
            "section_type": "recommendation",
            "required_evidence_types": ["rag"],
            "required_min_count": 0,
        },
    ],
}


# 报告类型默认标题前缀
REPORT_TYPE_TITLES: dict[str, str] = {
    "business_analysis": "业务分析报告",
    "quality_analysis": "质量分析报告",
    "trend_analysis": "趋势分析报告",
    "comparison": "对比分析报告",
    "knowledge_summary": "知识摘要报告",
    "management_briefing": "管理层汇报材料",
}


def get_template(report_type: ReportType) -> list[dict[str, Any]]:
    """获取报告类型对应的章节模板。

    Args:
        report_type: 报告类型

    Returns:
        list: 章节模板列表（深拷贝）
    """
    import copy

    template = SECTION_TEMPLATES.get(report_type, SECTION_TEMPLATES["business_analysis"])
    return copy.deepcopy(template)


def get_default_title(report_type: ReportType) -> str:
    """获取报告类型默认标题。"""
    return REPORT_TYPE_TITLES.get(report_type, "分析报告")


def list_report_types() -> list[str]:
    """列出所有支持的报告类型。"""
    return list(SECTION_TEMPLATES.keys())


def build_report_plan(
    report_type: ReportType,
    title: str,
    evidence: list[dict],
    max_sections: int = 10,
) -> ReportPlan:
    """构建 ReportPlan。

    按 docs/报告生成Tool设计.md 第 6.5 节：
    - 根据 report_type 选择模板
    - 检查 evidence 实际类型，标记缺失章节
    - 限制章节数不超过 max_sections

    Args:
        report_type: 报告类型
        title: 报告标题
        evidence: 已收集的 evidence 列表
        max_sections: 最大章节数

    Returns:
        ReportPlan: 报告章节计划
    """
    template_sections = get_template(report_type)
    available_types = {ev.get("source_type") for ev in evidence or []}

    sections: list[dict[str, Any]] = []
    warnings: list[str] = []

    for idx, tmpl in enumerate(template_sections[:max_sections]):
        section = {
            "section_id": tmpl["section_id"],
            "title": tmpl["title"],
            "order": idx,
            "section_type": tmpl["section_type"],
            "required_evidence_types": tmpl["required_evidence_types"],
            "required_min_count": tmpl.get("required_min_count", 1),
        }
        # 检查 evidence 是否满足章节要求
        required_types = set(tmpl["required_evidence_types"])
        min_count = tmpl.get("required_min_count", 1)

        matching_evidences = [
            ev for ev in (evidence or [])
            if ev.get("source_type") in required_types
        ]

        if min_count > 0 and len(matching_evidences) < min_count:
            section["evidence_ready"] = False
            warnings.append(
                f"章节「{tmpl['title']}」所需的 evidence 类型 {required_types} 不足"
                f"（找到 {len(matching_evidences)} 条，期望 {min_count} 条）"
            )
        else:
            section["evidence_ready"] = True

        section["available_evidence_count"] = len(matching_evidences)
        sections.append(section)

    if not available_types:
        warnings.append("当前 evidence 列表为空，建议先收集数据")

    return {
        "title": title or get_default_title(report_type),
        "report_type": report_type,
        "template_id": report_type,
        "sections": sections,
        "estimated_charts": min(len(evidence or []), 5),
        "estimated_tables": min(len(evidence or []), 3),
        "warnings": warnings,
    }
