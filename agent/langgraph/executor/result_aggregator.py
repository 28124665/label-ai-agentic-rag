"""结果聚合器。

将多个工具的执行结果聚合为统一格式，
兼容现有 quality_check 和 prompt_assembly 的字段读取方式。

聚合策略：
- RAG 结果：合并所有 docs，按 score 降序排列
- DB 结果：合并所有 rows，标注来源 db_id（_source_db 字段）
- Web 结果：合并所有 docs
- 质量评分：取所有成功步骤的最高分
- 失败步骤：跳过，不参与聚合
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ResultAggregator:
    """结果聚合器。

    将多个 ToolResult 聚合为与原 AgentState 兼容的字段结构，
    确保 quality_check 和 prompt_assembly 无需大改即可正常工作。
    """

    @staticmethod
    def aggregate(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
        """聚合多工具结果。

        Args:
            tool_results: 工具执行结果列表（ToolResult dict 格式）

        Returns:
            dict: 聚合后的结果，字段与原 AgentState 兼容：
                - rag_docs: 合并后的 RAG 文档列表（按 score 降序）
                - rag_quality_score: 最高 RAG 质量分
                - rag_has_relevant: 是否有相关文档
                - rag_relevant_count: 相关文档总数
                - rag_top_score: 最高文档分
                - db_result: 合并后的 DB 结果（含 _source_db 标注）
                - db_quality_score: 最高 DB 质量分
                - web_docs: 合并后的 Web 文档列表
        """
        all_rag_docs: list[dict] = []
        all_db_rows: list[dict] = []
        all_web_docs: list[dict] = []
        all_report_artifacts: list[dict] = []
        latest_report_summary = ""
        max_report_score = 0.0
        max_rag_score = 0.0
        max_db_score = 0.0
        db_sources: list[str] = []

        success_count = 0
        fail_count = 0

        for result in tool_results:
            if not result.get("success", False):
                fail_count += 1
                logger.warning(
                    f"[ResultAggregator] 跳过失败步骤: "
                    f"{result.get('step_id', 'unknown')} - "
                    f"{result.get('error', 'unknown error')}"
                )
                continue

            success_count += 1

            # 聚合 RAG 结果
            rag_docs = result.get("rag_docs", [])
            if rag_docs:
                all_rag_docs.extend(rag_docs)
                max_rag_score = max(
                    max_rag_score, result.get("rag_quality_score", 0.0)
                )

            # 聚合 DB 结果（支持多数据库）
            db_result = result.get("db_result", {})
            if db_result and db_result.get("rows"):
                db_id = result.get("db_id", "unknown")
                # 为每行标注来源数据库
                for row in db_result["rows"]:
                    row["_source_db"] = db_id
                all_db_rows.extend(db_result["rows"])
                max_db_score = max(
                    max_db_score, result.get("db_quality_score", 0.0)
                )
                db_sources.append(db_id)

            # 聚合 Web 结果
            web_docs = result.get("web_docs", [])
            if web_docs:
                all_web_docs.extend(web_docs)

            # 聚合 Report 结果
            report_artifacts = result.get("report_artifacts", []) or []
            if report_artifacts:
                all_report_artifacts.extend(report_artifacts)
                # 取最近一次 report summary
                if result.get("report_summary"):
                    latest_report_summary = result["report_summary"]
                # 取最高分
                max_report_score = max(
                    max_report_score, result.get("report_quality_score", 0.0)
                )

        # RAG docs 按 score 降序排列
        all_rag_docs.sort(key=lambda x: x.get("score", 0.0), reverse=True)

        # 构建聚合后的 db_result
        aggregated_db_result: dict[str, Any] = {
            "rows": all_db_rows,
            "row_count": len(all_db_rows),
            "source": "multi_db" if len(db_sources) > 1 else (
                db_sources[0] if db_sources else ""
            ),
            "is_aggregated": True,
            "source_dbs": db_sources,  # 所有来源数据库列表
        }

        logger.info(
            f"[ResultAggregator] 聚合完成: "
            f"success={success_count}, failed={fail_count}, "
            f"rag_docs={len(all_rag_docs)}, db_rows={len(all_db_rows)}, "
            f"web_docs={len(all_web_docs)}, db_sources={db_sources}, "
            f"report_artifacts={len(all_report_artifacts)}"
        )

        return {
            "rag_docs": all_rag_docs,
            "rag_quality_score": max_rag_score,
            "rag_has_relevant": len(all_rag_docs) > 0,
            "rag_relevant_count": len(all_rag_docs),
            "rag_top_score": all_rag_docs[0].get("score", 0.0)
            if all_rag_docs
            else 0.0,
            "db_result": aggregated_db_result,
            "db_quality_score": max_db_score,
            "web_docs": all_web_docs,
            "report_artifacts": all_report_artifacts,
            "report_summary": latest_report_summary,
            "report_quality_score": max_report_score,
        }
