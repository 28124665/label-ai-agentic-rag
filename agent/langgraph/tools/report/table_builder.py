"""表格构建器。

按 docs/报告生成Tool设计.md 第 6.8 节设计：
- 将 DB Evidence 或结构化结果转为报告表格
- columns / rows 来自 structured_data
- 表格绑定 evidence_refs
"""
from __future__ import annotations

import logging
from typing import Any

from agent.langgraph.tools.report.models import TableSpec

logger = logging.getLogger(__name__)


class TableBuilder:
    """表格构建器。

    从 DB Evidence 的 structured_data 中提取数据，
    生成符合 TableSpec 规范的表格定义。

    本类不调用 LLM，只做数据提取和格式转换。
    """

    def __init__(self, max_rows: int = 100):
        """初始化表格构建器。

        Args:
            max_rows: 单个表格最大行数
        """
        self.max_rows = max_rows

    def build(
        self,
        evidence_list: list[dict],
        max_tables: int = 8,
    ) -> tuple[list[TableSpec], list[dict]]:
        """构建表格列表。

        Args:
            evidence_list: evidence 列表（仅使用 source_type=db）
            max_tables: 最大表格数

        Returns:
            tuple: (tables, failed_tables)
                - tables: 成功构建的 TableSpec 列表
                - failed_tables: 构建失败的表格定义（含 error 字段）
        """
        tables: list[TableSpec] = []
        failed: list[dict] = []

        db_evidences = [ev for ev in evidence_list if ev.get("source_type") == "db"]

        if not db_evidences:
            logger.info("[TableBuilder] 无 DB evidence，跳过表格生成")
            return tables, failed

        for idx, ev in enumerate(db_evidences):
            if len(tables) >= max_tables:
                break

            try:
                table = self._build_from_db_evidence(ev, table_index=len(tables))
                if table:
                    tables.append(table)
            except Exception as e:
                logger.warning(
                    f"[TableBuilder] 从 evidence {ev.get('evidence_id')} 构建表格失败: {e}"
                )
                failed.append(
                    {
                        "evidence_id": ev.get("evidence_id"),
                        "error": str(e),
                    }
                )

        logger.info(
            f"[TableBuilder] 构建完成: success={len(tables)}, failed={len(failed)}"
        )
        return tables, failed

    def _build_from_db_evidence(
        self,
        evidence: dict,
        table_index: int = 0,
    ) -> TableSpec | None:
        """从 DB evidence 构建单个表格。"""
        ev_id = evidence.get("evidence_id", "")
        structured = evidence.get("structured_data", {}) or {}
        rows = structured.get("rows", []) or []
        columns = structured.get("columns", []) or []

        if not rows or not columns:
            return None

        # 截断到 max_rows
        truncated = len(rows) > self.max_rows
        if truncated:
            rows = rows[: self.max_rows]

        # 转换行：dict → list（按 columns 顺序）
        matrix_rows: list[list[Any]] = []
        for row in rows:
            matrix_rows.append([row.get(col, "") for col in columns])

        return {
            "table_id": f"table_{ev_id}_{table_index}",
            "title": evidence.get("title", f"数据表 {table_index + 1}"),
            "columns": columns,
            "rows": matrix_rows,
            "evidence_refs": [ev_id] if ev_id else [],
            "max_rows": self.max_rows,
        }
