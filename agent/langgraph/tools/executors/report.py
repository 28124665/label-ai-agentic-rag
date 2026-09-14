"""Report Tool Adapter。

承接 ``ToolDispatcher`` 原 ``_execute_report`` 逻辑，复用 ``ReportTool``。
前置步骤结果经 ``input_data["previous_results"]`` 传入。
"""
from __future__ import annotations

from agent.langgraph.tools.base import BaseTool
from agent.langgraph.tools.contract import ToolContext, ToolOutcome
from agent.langgraph.tools.registry import register_tool


@register_tool("report")
class ReportToolExecutor(BaseTool):
    """报告生成 Adapter。"""

    name = "report"

    async def _do_execute(
        self, input_data: dict, *, ctx: ToolContext
    ) -> ToolOutcome:
        from agent.langgraph.tools.report import ReportTool

        step = input_data["step"]
        state = input_data["state"]
        previous_results = input_data.get("previous_results", {})

        extra = step.args.extra or {}

        # 收集 evidence：state.evidence + 前置步骤结果
        state_evidence = state.get("evidence", []) or []
        previous_evidence: list[dict] = []
        for tool_result in previous_results.values():
            # TypedDict 必须用 dict() 访问
            tr = dict(tool_result)
            if not tr.get("success", False):
                continue
            # 提取前置步骤的 evidence
            rdata = tr
            if rdata.get("rag_docs"):
                for doc in rdata["rag_docs"]:
                    previous_evidence.append(
                        {
                            "evidence_id": f"ev_{step.step_id}_{len(previous_evidence)}",
                            "source_type": "rag",
                            "title": doc.get("title", ""),
                            "content": doc.get("content", ""),
                            "structured_data": {},
                            "source_uri": f"rag://{doc.get('doc_id', '')}/{doc.get('chunk_id', '')}",
                            "tenant_id": state.get("tenant_id", ""),
                            "confidence": doc.get("score", 0.5),
                            "relevance_score": doc.get("score", 0.5),
                            "authority_score": 0.8,
                            "freshness_score": 1.0,
                            "created_at": "",
                            "metadata": {"step_id": step.step_id},
                        }
                    )
            if rdata.get("db_result", {}).get("rows"):
                db_res = rdata["db_result"]
                previous_evidence.append(
                    {
                        "evidence_id": f"ev_{step.step_id}_db",
                        "source_type": "db",
                        "title": f"DB 查询结果（{db_res.get('row_count', 0)} 行）",
                        "content": db_res.get("formatted_result", ""),
                        "structured_data": {
                            "columns": list(db_res["rows"][0].keys()) if db_res["rows"] else [],
                            "rows": db_res["rows"][:100],
                        },
                        "source_uri": f"db://{db_res.get('source', 'unknown')}",
                        "tenant_id": state.get("tenant_id", ""),
                        "confidence": rdata.get("db_quality_score", 0.5),
                        "relevance_score": 0.8,
                        "authority_score": 0.95,
                        "freshness_score": 1.0,
                        "created_at": "",
                        "metadata": {"step_id": step.step_id, "sql": db_res.get("sql", "")},
                    }
                )

        all_evidence = state_evidence + previous_evidence

        # 构造 ReportToolInput
        report_input: dict = {
            "title": extra.get("title", ""),
            "report_type": extra.get("report_type", "business_analysis"),
            "skill_id": extra.get("skill_id"),
            "data_skill_id": extra.get("data_skill_id"),
            "retrieval_skill_id": extra.get("retrieval_skill_id"),
            "skill_set": extra.get("skill_set", state.get("skill_set")),
            "objective": step.args.query or state.get("user_question", ""),
            "format": extra.get("format", state.get("report_format", "markdown")),
            "language": state.get("query_lang", "zh_CN"),
            "tenant_id": state.get("tenant_id", ""),
            "user_id": state.get("user_id", ""),
            "evidence": all_evidence,
            "max_sections": extra.get("max_sections", 10),
            "max_charts": extra.get("max_charts", 6),
            "max_tables": extra.get("max_tables", 8),
        }

        # 调用 ReportTool
        report_tool = ReportTool()
        result = await report_tool.invoke(report_input)

        artifact = result.get("artifact", {}) or {}
        return ToolOutcome(
            success=True,
            tool=self.name,
            payload={
                "report_artifacts": [artifact] if result.get("success") else [],
                "report_summary": result.get("summary", ""),
                "report_quality_score": result.get("quality_score", 0.0),
                "report_error": result.get("error_message", ""),
                "report_error_code": result.get("error_code", ""),
                "report_file_uri": result.get("file_uri", ""),
                "report_download_url": result.get("download_url", ""),
                "report_partial": result.get("partial", False),
                # 下一期：可信治理与人机协同（按 docs §4-6）
                "report_claims": artifact.get("claims", []) or [],
                "report_data_sources": artifact.get("data_sources", []) or [],
                "report_human_review": artifact.get("human_review_result", {}) or {},
                "report_publish_status": result.get(
                    "publish_status", artifact.get("publish_status", "draft")
                ),
                "report_needs_human_review": result.get("needs_human_review", False),
            },
        )