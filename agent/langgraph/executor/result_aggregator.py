"""结果聚合器。

将多个工具的执行结果聚合为统一格式，
兼容现有 quality_check 和 prompt_assembly 的字段读取方式。

聚合策略（P0 修正版）：
- RAG 结果：合并所有 docs，使用 get_document_relevance_score() 统一分数后按 relevance_score 降序排列
- 去重：按 doc_id 去重，保留最高分文档
- 全局截断：聚合后重新执行 max_keep 截断（默认 20）
- 分数来源：仅当所有参与聚合的步骤 score_source 一致时才沿用；否则标记为 "mixed"
- DB 结果：合并所有 rows，标注来源 db_id（_source_db 字段）
- Web 结果：合并所有 docs
- 质量评分：截断后重新计算
- 失败步骤：跳过，不参与聚合
"""

import logging
from typing import Any

from agent.langgraph.tools.rag_tool import get_document_relevance_score

logger = logging.getLogger(__name__)

# 聚合阶段全局截断上限
_AGGREGATION_MAX_KEEP = 20


class ResultAggregator:
    """结果聚合器。

    将多个 ToolResult 聚合为与原 AgentState 兼容的字段结构，
    确保 quality_check 和 prompt_assembly 无需大改即可正常工作。

    P0 修正：使用统一分数函数、文档去重、全局截断和 score_source 聚合。
    """

    @staticmethod
    def aggregate(
        tool_results: list[dict[str, Any]],
        max_keep: int = _AGGREGATION_MAX_KEEP,
    ) -> dict[str, Any]:
        """聚合多工具结果。

        Args:
            tool_results: 工具执行结果列表（ToolResult dict 格式）
            max_keep: 全局截断上限，默认 20

        Returns:
            dict: 聚合后的结果，字段与原 AgentState 兼容：
                - rag_docs: 合并后的 RAG 文档列表（按 relevance_score 降序）
                - rag_quality_score: 最高 RAG 质量分
                - rag_has_relevant: 是否有相关文档
                - rag_relevant_count: 相关文档总数
                - rag_top_score: 最高文档规范化分数
                - rag_avg_score: 平均规范化分数（P0 新增）
                - rag_result_count: 聚合后结果数（P0 新增）
                - rag_score_source: 聚合后分数来源（P0 新增）
                - db_result: 合并后的 DB 结果（含 _source_db 标注）
                - db_quality_score: 最高 DB 质量分
                - web_docs: 合并后的 Web 文档列表
        """
        all_rag_docs: list[dict] = []
        all_db_rows: list[dict] = []
        all_web_docs: list[dict] = []
        all_rest_docs: list[dict] = []
        all_report_artifacts: list[dict] = []
        latest_report_summary = ""
        max_report_score = 0.0
        max_db_score = 0.0
        max_rest_score = 0.0
        db_sources: list[str] = []
        erp_domains: list[str] = []
        score_sources: set[str] = set()

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
                # 收集 score_source 用于聚合判断
                step_score_source = result.get("rag_score_source", "")
                if step_score_source:
                    score_sources.add(step_score_source)

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

            # 聚合 REST/ERP 结果
            rest_docs = result.get("rest_result", {}).get("docs", [])
            if rest_docs:
                all_rest_docs.extend(rest_docs)
                max_rest_score = max(
                    max_rest_score, result.get("rest_quality_score", 0.0)
                )
                erp_domain = result.get("erp_domain", "")
                if erp_domain:
                    erp_domains.append(erp_domain)

            # 聚合 Report 结果
            report_artifacts = result.get("report_artifacts", []) or []
            if report_artifacts:
                all_report_artifacts.extend(report_artifacts)
                if result.get("report_summary"):
                    latest_report_summary = result["report_summary"]
                max_report_score = max(
                    max_report_score, result.get("report_quality_score", 0.0)
                )

        # ★ P0 修正：使用统一分数函数排序 + 去重 + 全局截断
        if all_rag_docs:
            # 计算规范化分数
            for doc in all_rag_docs:
                relevance_score, _raw_source = get_document_relevance_score(doc)
                doc["_relevance_score"] = relevance_score

            # 按 doc_id 去重，保留最高分
            seen_doc_ids: set[str] = set()
            deduped_docs: list[dict] = []
            # 按规范化分数降序
            all_rag_docs.sort(key=lambda x: x.get("_relevance_score", 0.0), reverse=True)
            for doc in all_rag_docs:
                doc_id = doc.get("doc_id", "") or doc.get("chunk_id", "")
                if doc_id and doc_id in seen_doc_ids:
                    continue
                if doc_id:
                    seen_doc_ids.add(doc_id)
                deduped_docs.append(doc)

            # 全局截断
            all_rag_docs = deduped_docs[:max_keep]

        # 分数来源聚合
        if len(score_sources) == 1:
            rag_score_source = next(iter(score_sources))
        elif len(score_sources) > 1:
            rag_score_source = "mixed"
        else:
            rag_score_source = "base"

        # 截断后统计
        if all_rag_docs:
            scores = [d.get("_relevance_score", 0.0) for d in all_rag_docs]
            rag_top_score = max(scores) if scores else 0.0
            rag_avg_score = sum(scores) / len(scores) if scores else 0.0
            rag_result_count = len(all_rag_docs)
        else:
            rag_top_score = 0.0
            rag_avg_score = 0.0
            rag_result_count = 0

        # 构建聚合后的 db_result
        aggregated_db_result: dict[str, Any] = {
            "rows": all_db_rows,
            "row_count": len(all_db_rows),
            "source": "multi_db" if len(db_sources) > 1 else (
                db_sources[0] if db_sources else ""
            ),
            "is_aggregated": True,
            "source_dbs": db_sources,
        }

        logger.info(
            f"[ResultAggregator] 聚合完成: "
            f"success={success_count}, failed={fail_count}, "
            f"rag_docs={rag_result_count}, db_rows={len(all_db_rows)}, "
            f"web_docs={len(all_web_docs)}, rest_docs={len(all_rest_docs)}, "
            f"db_sources={db_sources}, erp_domains={erp_domains}, "
            f"report_artifacts={len(all_report_artifacts)}, "
            f"rag_score_source={rag_score_source}"
        )

        return {
            "rag_docs": all_rag_docs,
            "rag_quality_score": rag_avg_score,
            "rag_has_relevant": rag_result_count > 0,
            "rag_relevant_count": rag_result_count,
            "rag_top_score": rag_top_score,
            "rag_avg_score": rag_avg_score,
            "rag_result_count": rag_result_count,
            "rag_score_source": rag_score_source,
            "db_result": aggregated_db_result,
            "db_quality_score": max_db_score,
            "web_docs": all_web_docs,
            "rest_docs": all_rest_docs,
            "rest_quality_score": max_rest_score,
            "report_artifacts": all_report_artifacts,
            "report_summary": latest_report_summary,
            "report_quality_score": max_report_score,
        }
