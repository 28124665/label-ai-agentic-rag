"""工具分发器。

根据 PlanStep.tool 调用对应的工具实例，
复用现有 RAGTool / DatabaseTool / WebTool 的 invoke() 方法。

核心设计：
- 步骤参数优先：step.args 中的参数覆盖 state 中的默认值
- 多实例支持：step.args.db_id 可指定不同的数据库实例
- 异常隔离：单个步骤失败不影响其他步骤
"""

import logging
import time
from typing import TYPE_CHECKING

from agent.langgraph.routers.models import PlanStep

if TYPE_CHECKING:
    from agent.langgraph.state import AgentState, ToolResult

logger = logging.getLogger(__name__)


class ToolDispatcher:
    """工具分发器。

    根据 PlanStep.tool 类型，调用对应的工具实例。
    复用现有的 get_rag_tool() / get_database_tool() / get_web_tool()，
    不重写工具逻辑。
    """

    async def dispatch(
        self,
        step: PlanStep,
        state: "AgentState",
        previous_results: dict[str, "ToolResult"],
    ) -> "ToolResult":
        """分发步骤到对应工具。

        Args:
            step: 执行步骤
            state: 全局状态
            previous_results: 前置步骤结果（可用于步骤间数据传递）

        Returns:
            ToolResult: 工具执行结果
        """
        from agent.langgraph.state import ToolResult

        start_time = time.time()
        step_desc = step.description or step.step_id

        try:
            if step.tool == "rag":
                result = await self._execute_rag(step, state)
            elif step.tool == "database":
                result = await self._execute_database(step, state)
            elif step.tool == "web":
                result = await self._execute_web(step, state)
            elif step.tool == "report":
                result = await self._execute_report(step, state, previous_results)
            else:
                raise ValueError(f"未知工具类型: {step.tool}")

            success = bool(result.pop("success", True))
            result["step_id"] = step.step_id
            result["tool"] = step.tool
            result["success"] = success
            result["latency_ms"] = int((time.time() - start_time) * 1000)

            log_message = (
                f"[ToolDispatcher] 步骤 {step.step_id} ({step.tool}, {step_desc}) "
                f"执行{'成功' if success else '失败'}: {result['latency_ms']}ms"
            )
            if success:
                logger.info(log_message)
            else:
                logger.warning(log_message)
            return ToolResult(**result)

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"[ToolDispatcher] 步骤 {step.step_id} ({step.tool}, {step_desc}) "
                f"执行失败: {e} ({latency_ms}ms)"
            )
            return ToolResult(
                step_id=step.step_id,
                tool=step.tool,
                success=False,
                error=str(e),
                error_code="TOOL_EXECUTION_FAILED",
                latency_ms=latency_ms,
            )

    async def _execute_rag(self, step: PlanStep, state: "AgentState") -> dict:
        """执行 RAG 检索。

        复用 get_rag_tool()，但使用 step.args 中的参数覆盖 state 默认值。
        支持 step.args.kb_ids 指定特定知识库。
        """
        extra = step.args.extra or {}
        if extra.get("retrieval_skill_id"):
            return await self._execute_skill_rag(step, state, extra)

        from agent.langgraph.tools.rag_tool import get_rag_tool
        rag_tool = get_rag_tool()

        # 步骤参数优先，state 兜底
        query = step.args.query or state.get("user_question", "")

        input_data = {
            "query": query,
            "query_lang": state.get("query_lang", "zh_CN"),
            "kb_ids": step.args.kb_ids or state.get("kb_ids", []),
            "tenant_id": state.get("tenant_id", ""),
            "llm_id": state.get("llm_id", ""),
        }

        # 合并 extra 参数
        if step.args.extra:
            input_data.update(step.args.extra)

        result = await rag_tool.invoke(input_data)

        return {
            "rag_docs": result.get("docs", []),
            "rag_quality_score": result.get("quality_score", 0.0),
            "rag_has_relevant": result.get("has_relevant", False),
            "rag_relevant_count": result.get("relevant_count", 0),
            "rag_top_score": result.get("top_score", 0.0),
            "kb_ids": step.args.kb_ids or state.get("kb_ids", []),
        }

    async def _execute_skill_rag(
        self, step: PlanStep, state: "AgentState", extra: dict
    ) -> dict:
        """Execute RAG exclusively against a RetrievalSkill-declared target."""
        from agent.langgraph.tools.rag.skill_rag import SkillRAGExecutor

        skill_input = {
            **extra,
            "query": step.args.query or state.get("user_question", ""),
            "tenant_id": state.get("tenant_id", ""),
            "time_range": extra.get("time_range", state.get("time_range")),
            "report_date": extra.get("report_date", state.get("report_date")),
        }
        executor = SkillRAGExecutor()
        prepared = executor.prepare(skill_input)
        if prepared is None:
            result = executor.skipped_result()
            kb_ids: list[str] = []
        else:
            result = await executor.invoke_prepared(prepared)
            kb_ids = prepared.input_data["kb_ids"]

        return {
            "rag_docs": result.get("docs", []),
            "rag_quality_score": result.get("quality_score", 0.0),
            "rag_has_relevant": result.get("has_relevant", False),
            "rag_relevant_count": result.get("relevant_count", 0),
            "rag_top_score": result.get("top_score", 0.0),
            "kb_ids": kb_ids,
        }

    async def _execute_database(self, step: PlanStep, state: "AgentState") -> dict:
        """执行数据库查询。

        关键：使用 step.args.db_id 指定具体数据库实例，
        支持同一计划中查询多个不同数据库（如 hr_system + supply_chain）。
        """
        extra = step.args.extra or {}
        if extra.get("data_skill_id") and extra.get("query_template_id"):
            fallback_hints = await self._resolve_sql_agent_fallback(extra)
            if fallback_hints is None:
                return await self._execute_structured_database(step, state, extra)
            # fallback_mode=sql_agent：模板未命中，降级到 SQL Agent 并注入 hints
            extra = {**extra, "exploration_hints": fallback_hints}

        from agent.langgraph.tools.sql_agent.runner import get_sql_agent_runner

        runner = get_sql_agent_runner()

        agent_config = state.get("agent_config", {}) or {}
        db_config = agent_config.get("database_config", {}) or {}

        # 步骤参数优先，state 兜底
        query = step.args.query or state.get("user_question", "")
        query_simplified = (
            step.args.query_simplified
            or state.get("query_simplified", "")
            or query
        )

        # ★ 关键：使用步骤指定的 db_id，而非 state 中的全局 db_id
        db_id = step.args.db_id or state.get("db_id", "")

        input_data = {
            "query": query,
            "query_simplified": query_simplified,
            "query_lang": state.get("query_lang", "zh_CN"),
            "db_id": db_id,
            "tenant_id": state.get("tenant_id", ""),
            "llm_id": state.get("llm_id", ""),
            "mcp_server_name": step.args.mcp_server_name
            or state.get("mcp_server_name", "database_mcp_server"),
            "enable_self_healing": db_config.get("enable_self_healing", True),
            "enable_template": db_config.get("enable_template", True),
        }

        # SQL Agent 扩展参数（空值不传，由 runner 兜底）
        for key, value in {
            "exploration_mode": db_config.get("exploration_mode"),
            "sql_agent_config": db_config.get("sql_agent_config"),
        }.items():
            if value:
                input_data[key] = value

        # 合并 extra 参数
        if step.args.extra:
            input_data.update(step.args.extra)

        result = await runner.invoke(input_data)

        return {
            "db_result": result,
            "db_quality_score": result.get("quality_score", 0.0),
            "db_id": db_id,
        }

    async def _resolve_sql_agent_fallback(self, extra: dict) -> dict | None:
        """判断结构化路径是否应降级到 SQL Agent（DataSkill.fallback_mode）。

        策略（docs/数据库Tool渐进式披露LLM化落地设计.md §2.3 决策总序 ①）：
        - data_skill_id + query_template_id 齐全 → 结构化路径（返回 None）
        - query_template_id 未在 DataSkill 中声明且 fallback_mode=sql_agent
          → 返回 exploration_hints（调用方降级到 SQL Agent）
        - 其余情况 → 结构化路径（让 build 抛出明确错误）

        Returns:
            dict | None: exploration_hints；None 表示继续走结构化路径
        """
        from agent.langgraph.skills import DataSkill, SkillRegistry

        data_skill = SkillRegistry().get_by_id(extra.get("data_skill_id", ""))
        if not isinstance(data_skill, DataSkill):
            return None

        declared = {
            t.get("template_id") for t in data_skill.query_templates
        }
        if extra.get("query_template_id") in declared:
            return None

        if data_skill.fallback_mode == "sql_agent" and data_skill.exploration_hints:
            logger.info(
                f"[ToolDispatcher] 模板 {extra.get('query_template_id')} 未在 "
                f"DataSkill {data_skill.skill_id} 声明，fallback_mode=sql_agent，"
                f"降级到 SQL Agent（注入 exploration_hints）"
            )
            return data_skill.exploration_hints
        return None

    async def _execute_structured_database(
        self,
        step: PlanStep,
        state: "AgentState",
        extra: dict,
    ) -> dict:
        """Execute a DataSkill-declared query through the structured DB path."""
        from agent.langgraph.skills import DataSkill, SkillRegistry
        from agent.langgraph.tools.database import (
            DatabaseToolStructuredBackend,
            StructuredQueryBuilder,
            StructuredQueryExecutor,
        )

        data_skill = SkillRegistry().get_by_id(extra["data_skill_id"])
        if not isinstance(data_skill, DataSkill):
            raise ValueError(f"DataSkill not found: {extra['data_skill_id']}")

        context = {
            "tenant_id": state.get("tenant_id", ""),
            "time_range": extra.get("time_range", state.get("time_range")),
            "report_date": extra.get("report_date", state.get("report_date")),
            "filters": extra.get("filters", {}),
            "limit": extra.get("limit"),
        }
        context = {key: value for key, value in context.items() if value is not None}
        builder = StructuredQueryBuilder(data_skill)
        query = builder.build(extra["query_template_id"], context)
        policy = builder.policy_for(query)
        backend = state.get("structured_db_execute_sql")
        if backend is None:
            backend = DatabaseToolStructuredBackend(
                tenant_id=state.get("tenant_id", ""),
                mcp_server_name=step.args.mcp_server_name
                or state.get("mcp_server_name", "database_mcp_server"),
            )
        result = await StructuredQueryExecutor(backend).execute_async(query, policy)
        if not result.success:
            return {
                "success": False,
                "error": result.error_message,
                "error_code": result.error_code,
                "db_result": result.model_dump(),
                "db_quality_score": 0.0,
                "db_id": result.db_id,
            }

        db_result = result.model_dump()
        db_result["formatted_result"] = self._format_structured_db_result(db_result)
        return {
            "db_result": db_result,
            "db_quality_score": 1.0 if result.rows else 0.0,
            "db_id": result.db_id,
        }

    @staticmethod
    def _format_structured_db_result(result: dict) -> str:
        """Render minimal compatibility text for the existing evidence normalizer."""
        return (
            f"查询到 {result['row_count']} 条记录，"
            f"数据来源：数据库 {result['db_id']}，表 {result['table_name']}。"
        )

    async def _execute_web(self, step: PlanStep, state: "AgentState") -> dict:
        """执行 Web 搜索。"""
        from agent.langgraph.tools.web_tool import get_web_tool

        web_tool = get_web_tool()

        query = step.args.query or state.get("user_question", "")

        input_data = {
            "query": query,
            "query_lang": state.get("query_lang", "zh_CN"),
        }

        # 合并 extra 参数
        if step.args.extra:
            input_data.update(step.args.extra)

        result = await web_tool.invoke(input_data)

        return {
            "web_docs": result.get("docs", []),
        }

    async def _execute_report(
        self,
        step: PlanStep,
        state: "AgentState",
        previous_results: dict[str, "ToolResult"],
    ) -> dict:
        """执行 Report Tool。

        按 docs/报告生成Tool设计.md §5.2 设计：
        - 从 step.args.extra 提取 report 参数
        - 复用 state.evidence + 前置步骤结果
        - 调用 ReportTool.invoke 生成 ReportArtifact

        Args:
            step: report 步骤定义
            state: 全局状态
            previous_results: 前置步骤结果（RAG/DB/Web 已收集的 evidence）

        Returns:
            dict: 包含 report_artifacts / report_summary / report_quality_score
        """
        from agent.langgraph.tools.report import ReportTool

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
        # 提取 governance 字段（按 docs/报告可信治理 §4-6）
        return {
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
        }


# 全局实例
_dispatcher_instance: ToolDispatcher | None = None


def get_tool_dispatcher() -> ToolDispatcher:
    """获取 ToolDispatcher 单例实例。

    Returns:
        ToolDispatcher: 工具分发器实例
    """
    global _dispatcher_instance
    if _dispatcher_instance is None:
        _dispatcher_instance = ToolDispatcher()
    return _dispatcher_instance
