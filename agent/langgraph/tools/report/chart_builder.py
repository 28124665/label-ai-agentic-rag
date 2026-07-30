"""图表构建器。

按 docs/报告生成Tool设计.md 第 6.7 节设计：
- 只从结构化 Evidence（DB 类型）生成图表
- 不允许 LLM 自由编造图表数据
- 图表数据必须绑定 evidence_refs
- 支持图表类型：bar / line / pie / table / kpi
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agent.langgraph.tools.report.models import ChartSpec

logger = logging.getLogger(__name__)


class ChartBuilder:
    """图表构建器。

    从 DB Evidence 的 structured_data 中提取数据，
    生成符合 ChartSpec 规范的图表定义。

    注意：本类**不调用 LLM**，只做数据提取和格式转换。
    """

    def build(
        self,
        evidence_list: list[dict],
        max_charts: int = 6,
    ) -> tuple[list[ChartSpec], list[dict]]:
        """构建图表列表。

        Args:
            evidence_list: evidence 列表（仅使用 source_type=db）
            max_charts: 最大图表数

        Returns:
            tuple: (charts, failed_charts)
                - charts: 成功构建的 ChartSpec 列表
                - failed_charts: 构建失败的图表定义（含 error 字段）
        """
        charts: list[ChartSpec] = []
        failed: list[dict] = []

        # 筛选 DB evidence（含结构化数据）
        db_evidences = [ev for ev in evidence_list if ev.get("source_type") == "db"]

        if not db_evidences:
            logger.info("[ChartBuilder] 无 DB evidence，跳过图表生成")
            return charts, failed

        for ev in db_evidences:
            if len(charts) >= max_charts:
                break

            try:
                chart = self._build_from_db_evidence(ev, chart_index=len(charts))
                if chart:
                    charts.append(chart)
            except Exception as e:
                logger.warning(f"[ChartBuilder] 从 evidence {ev.get('evidence_id')} 构建图表失败: {e}")
                failed.append(
                    {
                        "evidence_id": ev.get("evidence_id"),
                        "error": str(e),
                    }
                )

        logger.info(
            f"[ChartBuilder] 构建完成: success={len(charts)}, failed={len(failed)}"
        )
        return charts, failed

    def _build_from_db_evidence(
        self,
        evidence: dict,
        chart_index: int = 0,
    ) -> ChartSpec | None:
        """从 DB evidence 构建单个图表。

        优先从 structured_data 中提取：
        - 有 rows: 生成 bar/table 图表
        - 有 KPI 字段: 生成 kpi 图表
        - 都没有: 返回 None
        """
        ev_id = evidence.get("evidence_id", f"ev_unknown_{chart_index}")
        structured = evidence.get("structured_data", {}) or {}
        rows = structured.get("rows", []) or []
        columns = structured.get("columns", []) or []

        if not rows:
            return None

        # 取前 N 行作为数据
        preview_rows = rows[:50]

        # 简单启发式：
        # - 1 行 1 数字列 → kpi
        # - 2 列（label + value）→ bar
        # - 3+ 列 → table

        numeric_columns = self._find_numeric_columns(preview_rows, columns)

        if len(preview_rows) == 1 and len(numeric_columns) >= 1:
            # KPI 图表：单行多指标
            return self._build_kpi_chart(evidence, preview_rows[0], numeric_columns, chart_index)

        if len(columns) == 2 and len(numeric_columns) >= 1:
            # Bar 图表：双列
            return self._build_bar_chart(evidence, preview_rows, columns, chart_index)

        # 默认：table 图表
        return self._build_table_chart(evidence, preview_rows, columns, chart_index)

    def _build_kpi_chart(
        self,
        evidence: dict,
        row: dict,
        numeric_columns: list[str],
        chart_index: int,
    ) -> ChartSpec:
        """构建 KPI 图表。"""
        ev_id = evidence.get("evidence_id", "")
        series = []
        for col in numeric_columns:
            value = row.get(col)
            if value is not None:
                series.append({"name": col, "value": value})

        return {
            "chart_id": f"chart_{ev_id}_{chart_index}",
            "chart_type": "kpi",
            "title": evidence.get("title", f"关键指标 {chart_index + 1}"),
            "x_axis": "",
            "y_axis": "",
            "series": series,
            "data": [row],
            "evidence_refs": [ev_id] if ev_id else [],
            "unit": "",
            "notes": "",
        }

    def _build_bar_chart(
        self,
        evidence: dict,
        rows: list[dict],
        columns: list[str],
        chart_index: int,
    ) -> ChartSpec:
        """构建柱状图。"""
        ev_id = evidence.get("evidence_id", "")
        x_col, y_col = columns[0], columns[1]

        data = [
            {x_col: row.get(x_col, ""), y_col: row.get(y_col, 0)}
            for row in rows
        ]

        return {
            "chart_id": f"chart_{ev_id}_{chart_index}",
            "chart_type": "bar",
            "title": evidence.get("title", f"对比分析 {chart_index + 1}"),
            "x_axis": x_col,
            "y_axis": y_col,
            "series": [{"name": y_col, "data": [row.get(y_col, 0) for row in rows]}],
            "data": data,
            "evidence_refs": [ev_id] if ev_id else [],
            "unit": "",
            "notes": "",
        }

    def _build_table_chart(
        self,
        evidence: dict,
        rows: list[dict],
        columns: list[str],
        chart_index: int,
    ) -> ChartSpec:
        """构建表格图表。"""
        ev_id = evidence.get("evidence_id", "")

        return {
            "chart_id": f"chart_{ev_id}_{chart_index}",
            "chart_type": "table",
            "title": evidence.get("title", f"数据表 {chart_index + 1}"),
            "x_axis": "",
            "y_axis": "",
            "series": [],
            "data": rows,
            "evidence_refs": [ev_id] if ev_id else [],
            "unit": "",
            "notes": "",
        }

    def _find_numeric_columns(
        self,
        rows: list[dict],
        columns: list[str],
    ) -> list[str]:
        """查找包含数值的列。"""
        if not rows:
            return []
        numeric_cols = []
        for col in columns:
            sample_values = [row.get(col) for row in rows[:5]]
            if any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in sample_values):
                numeric_cols.append(col)
        return numeric_cols
