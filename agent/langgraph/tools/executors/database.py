"""Database Tool Adapter。

承接 ``ToolDispatcher`` 原 ``_execute_database`` / ``_resolve_sql_agent_fallback`` /
``_execute_structured_database`` / ``_format_structured_db_result`` 逻辑。
底层仍复用 ``get_sql_agent_runner()`` 与结构化查询后端，不改动工具本身。
"""
from __future__ import annotations

import logging

from agent.langgraph.tools.base import BaseTool
from agent.langgraph.tools.contract import ToolContext, ToolOutcome
from agent.langgraph.tools.registry import register_tool

logger = logging.getLogger(__name__)


@register_tool("database")
class DatabaseToolExecutor(BaseTool):
    """数据库查询 Adapter。"""

    name = "database"

    async def _do_execute(
        self, input_data: dict, *, ctx: ToolContext
    ) -> ToolOutcome:
        step = input_data["step"]
        state = input_data["state"]
        return await self._execute_database(step, state)

    async def _execute_database(self, step, state) -> ToolOutcome:
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

        return ToolOutcome(
            success=True,
            tool=self.name,
            payload={
                "db_result": result,
                "db_quality_score": result.get("quality_score", 0.0),
                "db_id": db_id,
            },
        )

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
                f"[DatabaseToolExecutor] 模板 {extra.get('query_template_id')} 未在 "
                f"DataSkill {data_skill.skill_id} 声明，fallback_mode=sql_agent，"
                f"降级到 SQL Agent（注入 exploration_hints）"
            )
            return data_skill.exploration_hints
        return None

    async def _execute_structured_database(
        self,
        step,
        state,
        extra: dict,
    ) -> ToolOutcome:
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
            return ToolOutcome(
                success=False,
                error=result.error_message,
                error_code=result.error_code,
                payload={
                    "db_result": result.model_dump(),
                    "db_quality_score": 0.0,
                    "db_id": result.db_id,
                },
            )

        db_result = result.model_dump()
        db_result["formatted_result"] = self._format_structured_db_result(db_result)
        return ToolOutcome(
            success=True,
            tool=self.name,
            payload={
                "db_result": db_result,
                "db_quality_score": 1.0 if result.rows else 0.0,
                "db_id": result.db_id,
            },
        )

    @staticmethod
    def _format_structured_db_result(result: dict) -> str:
        """Render minimal compatibility text for the existing evidence normalizer."""
        return (
            f"查询到 {result['row_count']} 条记录，"
            f"数据来源：数据库 {result['db_id']}，表 {result['table_name']}。"
        )