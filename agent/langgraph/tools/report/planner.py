"""报告规划器。

按 docs/报告生成Tool设计.md 第 6.5 节设计：
- 输入：report_type / evidence 列表 / max_sections
- 输出：ReportPlan（含章节列表、图表/表格预估、warnings）
- 职责：根据 report_type 选择模板，校验 evidence 充分性
"""
from __future__ import annotations

import logging
from typing import Any

from agent.langgraph.skills.models import ReportSkill
from agent.langgraph.tools.report.models import ReportPlan, ReportType
from agent.langgraph.tools.report.templates import build_report_plan

logger = logging.getLogger(__name__)


class ReportPlanner:
    """报告规划器。

    设计原则（docs/受限ReAct子图落地设计.md §5.2 类似思想）：
    - Planner 本身轻量，不调用 LLM
    - 章节拆分基于预置模板
    - evidence 不足时标记 warnings，不硬性失败
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """初始化规划器。

        Args:
            config: 配置字典（含 max_sections 等）
        """
        self._config = config or {}

    def plan(
        self,
        report_type: ReportType,
        title: str,
        evidence: list[dict],
        objective: str = "",
        max_sections: int | None = None,
    ) -> ReportPlan:
        """规划报告章节。

        Args:
            report_type: 报告类型
            title: 报告标题
            evidence: 已收集的 evidence 列表
            objective: 报告目标（可选）
            max_sections: 最大章节数（覆盖配置）

        Returns:
            ReportPlan: 报告章节计划
        """
        effective_max_sections = max_sections or self._config.get("max_sections", 10)

        plan = build_report_plan(
            report_type=report_type,
            title=title,
            evidence=evidence or [],
            max_sections=effective_max_sections,
        )

        # 如果提供了 objective，附加到第一个 overview 章节
        if objective and plan["sections"]:
            first_section = plan["sections"][0]
            first_section["objective"] = objective

        # 添加全局 warnings
        if not evidence:
            plan["warnings"].append("evidence 列表为空，报告将基于模板生成占位内容")

        if not title:
            plan["warnings"].append("未提供报告标题，已使用默认标题")

        logger.info(
            f"[ReportPlanner] 规划完成: report_type={report_type}, "
            f"sections={len(plan['sections'])}, "
            f"warnings={len(plan['warnings'])}"
        )

        return plan

    def plan_from_skill(
        self,
        report_skill: ReportSkill,
        title: str,
        evidence: list[dict],
        objective: str = "",
        max_sections: int | None = None,
    ) -> ReportPlan:
        """Build a deterministic plan from a ReportSkill's section templates."""
        effective_max_sections = max_sections or self._config.get("max_sections", 10)
        sections = sorted(
            report_skill.section_templates,
            key=lambda section: section.get("order", 0),
        )[:effective_max_sections]
        evidence_types = {item.get("source_type") for item in evidence}
        planned_sections: list[dict] = []
        for section in sections:
            planned_section = dict(section)
            required_types = set(section.get("required_evidence_types", []))
            planned_section["evidence_ready"] = required_types.issubset(evidence_types)
            if objective:
                planned_section["objective"] = objective
            planned_sections.append(planned_section)

        warnings: list[str] = []
        missing_core_sections = set(report_skill.core_sections) - {
            section.get("section_id") for section in planned_sections
        }
        if missing_core_sections:
            warnings.append(
                f"章节上限排除了核心章节: {sorted(missing_core_sections)}"
            )

        return {
            "title": title,
            "report_type": report_skill.report_type,
            "template_id": report_skill.skill_id,
            "sections": planned_sections,
            "estimated_charts": min(len(report_skill.metric_definitions), 6),
            "estimated_tables": min(len(report_skill.metric_definitions), 8),
            "warnings": warnings,
        }
