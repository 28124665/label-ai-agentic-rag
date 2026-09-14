"""RAG Tool Adapter。

承接 ``ToolDispatcher`` 原 ``_execute_rag`` / ``_execute_skill_rag`` 逻辑，
通过 ``BaseTool`` 模板方法接入注册表。底层仍复用 ``get_rag_tool()``，
不改动 RAGTool / SkillRAGExecutor 本身。
"""
from __future__ import annotations

from agent.langgraph.tools.base import BaseTool
from agent.langgraph.tools.contract import ToolContext, ToolOutcome
from agent.langgraph.tools.registry import register_tool


@register_tool("rag")
class RAGToolExecutor(BaseTool):
    """RAG 检索 Adapter（含 RetrievalSkill 专属路径）。"""

    name = "rag"

    async def _do_execute(
        self, input_data: dict, *, ctx: ToolContext
    ) -> ToolOutcome:
        step = input_data["step"]
        state = input_data["state"]

        extra = step.args.extra or {}
        if extra.get("retrieval_skill_id"):
            payload = await self._execute_skill_rag(step, state, extra)
        else:
            payload = await self._execute_rag(step, state)
        return ToolOutcome(success=True, tool=self.name, payload=payload)

    async def _execute_rag(self, step, state) -> dict:
        """执行 RAG 检索。

        复用 get_rag_tool()，但使用 step.args 中的参数覆盖 state 默认值。
        支持 step.args.kb_ids 指定特定知识库。
        """
        from agent.langgraph.tools.rag_tool import get_rag_tool

        rag_tool = get_rag_tool()

        # 步骤参数优先，state 兜底
        query = step.args.query or state.get("user_question", "")

        # §5.9 参数优先级：step.args.extra > agent_config.rag_config > 内置默认值
        agent_config = state.get("agent_config", {}) or {}
        rag_config = agent_config.get("rag_config", {}) or {}

        input_data = {
            "query": query,
            "query_lang": state.get("query_lang", "zh_CN"),
            "kb_ids": step.args.kb_ids or state.get("kb_ids", []),
            "tenant_id": state.get("tenant_id", ""),
            "llm_id": state.get("llm_id", ""),
            # ★ §5.9 补齐：从 rag_config 读取此前遗漏的参数（extra 可覆盖）
            "similarity_threshold": rag_config.get("similarity_threshold", 0.2),
            "keywords_similarity_weight": rag_config.get(
                "keywords_similarity_weight", 0.5
            ),
            "rerank_id": rag_config.get("rerank_id", ""),
        }

        # 合并 extra 参数（优先级最高，覆盖 rag_config 默认值）
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
            # §5.8 透传降级状态（供 quality_check 前置规则识别 infra 失败）
            "retrieval_error_code": result.get("retrieval_error_code", ""),
            "retrieval_mode_used": result.get("retrieval_mode_used", ""),
            # ★ P0 修正：统一 RAG 元数据透传
            "rag_score_source": result.get("score_source", "base"),
            "rag_avg_score": result.get("avg_score", 0.0),
            "rag_result_count": result.get("result_count", 0),
            "rag_raw_score_source": result.get("raw_score_source", ""),
            "rag_query_signature": result.get("query_signature", ""),
            "rag_evidence_signature": result.get("evidence_signature", ""),
            "rag_retrieval_top_k": result.get("retrieval_top_k", 0),
            "rag_rerank_top_k": result.get("rerank_top_k", 0),
        }

    async def _execute_skill_rag(self, step, state, extra: dict) -> dict:
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
            # §5.8 透传降级状态（供 quality_check 前置规则识别 infra 失败）
            "retrieval_error_code": result.get("retrieval_error_code", ""),
            "retrieval_mode_used": result.get("retrieval_mode_used", ""),
            # ★ P0 修正：统一 RAG 元数据透传
            "rag_score_source": result.get("score_source", "base"),
            "rag_avg_score": result.get("avg_score", 0.0),
            "rag_result_count": result.get("result_count", 0),
            "rag_raw_score_source": result.get("raw_score_source", ""),
            "rag_query_signature": result.get("query_signature", ""),
            "rag_evidence_signature": result.get("evidence_signature", ""),
            "rag_retrieval_top_k": result.get("retrieval_top_k", 0),
            "rag_rerank_top_k": result.get("rerank_top_k", 0),
        }