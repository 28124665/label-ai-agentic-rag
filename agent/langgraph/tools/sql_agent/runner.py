#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""SqlAgentRunner — SQL Agent 对外入口（DB 查询统一门面）。

职责（docs/数据库Tool渐进式披露LLM化落地设计.md）：
1. 模式切换：exploration_mode=sql_agent（默认）| legacy（应急回滚）
2. 模板短路：TemplateMatcher 命中 → 直接执行（优先级 ②，零 LLM 开销）
3. SQL Agent 子图：业界标准 Agent 驱动渐进式披露（优先级 ③）
4. db_result 契约：与 DatabaseToolOutput 对齐 + 新增
   exploration_verdict / exploration_stats / step_trace（只增不改）

降级链：模板 → SQL Agent →（LLM 不可用/子图异常）legacy 规则流水线
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import yaml

from agent.langgraph.tools.database_tool import (
    DatabaseToolInput,
    DatabaseToolOutput,
    TemplateMatcher,
    get_database_tool,
)
from agent.langgraph.tools.db_runtime import DbRuntime, get_db_runtime
from agent.langgraph.tools.sql_agent.budget import ExplorationBudget
from agent.langgraph.tools.sql_agent.state import (
    VERDICT_BUDGET_EXHAUSTED,
    VERDICT_DATA_FOUND,
    VERDICT_EXPLORATION_FAILED,
    VERDICT_NO_DATA_CONFIRMED,
    SqlAgentState,
)
from agent.langgraph.tools.sql_agent.subgraph import LLMCallable, SqlAgentSubgraph
from agent.langgraph.tools.sql_agent.tools import SqlAgentToolset

logger = logging.getLogger(__name__)

# 探索模式
MODE_SQL_AGENT = "sql_agent"
MODE_LEGACY = "legacy"

# LLM 生成参数
SQL_AGENT_GEN_CONF = {"temperature": 0.1, "max_tokens": 2048}


class SqlAgentRunner:
    """SQL Agent 统一入口。

    使用方式（与 get_database_tool 同构）：
    ```python
        runner = get_sql_agent_runner()
        result = await runner.invoke(input_data)
    ```
    """

    def __init__(
        self,
        runtime: Optional[DbRuntime] = None,
        config_path: str = "conf/sql_agent.yaml",
    ):
        self._runtime = runtime or get_db_runtime()
        self._file_config = self._load_file_config(config_path)

    @staticmethod
    def _load_file_config(config_path: str) -> dict[str, Any]:
        """加载 conf/sql_agent.yaml（缺失时返回空配置，使用代码默认值）。"""
        if not os.path.exists(config_path):
            return {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"[SqlAgentRunner] 加载 {config_path} 失败: {e}")
            return {}

    async def invoke(self, input_data: DatabaseToolInput) -> dict[str, Any]:
        """执行数据库查询。

        Args:
            input_data: DatabaseToolInput + 扩展字段：
                - exploration_mode: "sql_agent" | "legacy"
                - exploration_hints: DataSkill 业务域提示
                - sql_agent_config: 预算覆盖（budget 字段字典）
                - allowed_db_ids: 库级白名单（DataSkill db_targets 约束）

        Returns:
            dict: db_result（DatabaseToolOutput 超集）
        """
        start_time = time.time()
        query = input_data.get("query", "")
        query_simplified = input_data.get("query_simplified", "") or query
        query_lang = input_data.get("query_lang", "zh_CN")
        tenant_id = input_data.get("tenant_id", "")
        llm_id = input_data.get("llm_id", "")
        mcp_server_name = input_data.get("mcp_server_name", "database_mcp_server")
        db_schema_config = input_data.get("db_schema_config", "conf/db_schema.yaml")
        query_templates_config = input_data.get("query_templates_config", "conf/query_templates.yaml")
        enable_template = input_data.get("enable_template", True)
        db_id = input_data.get("db_id", "")

        exploration_mode = input_data.get(  # type: ignore[typeddict-item]
            "exploration_mode",
            self._file_config.get("exploration_mode", MODE_SQL_AGENT),
        )
        exploration_hints = input_data.get("exploration_hints") or {}  # type: ignore[typeddict-item]
        allowed_db_ids = input_data.get("allowed_db_ids")  # type: ignore[typeddict-item]

        if not query:
            return self._error_result(start_time, "query 不能为空")

        # legacy 模式：整体回滚到启发式规则流水线
        if exploration_mode == MODE_LEGACY:
            logger.info("[SqlAgentRunner] exploration_mode=legacy，回滚到启发式规则流水线")
            return await get_database_tool().invoke(input_data)

        # 1. 加载配置并初始化 MCP 会话（共享 DbRuntime）
        self._runtime.load_db_config(db_schema_config)
        try:
            self._runtime.init_session(tenant_id, mcp_server_name)
        except Exception as e:
            logger.error(f"[SqlAgentRunner] MCP 会话初始化失败: {e}")
            return self._error_result(start_time, f"MCP 会话初始化失败: {e}")

        if not self._runtime.db_tools:
            return self._error_result(start_time, "未从 MCP Server 获取到任何数据库工具")

        # 库白名单约束（DataSkill db_targets）
        if allowed_db_ids and db_id and db_id not in allowed_db_ids:
            return self._error_result(
                start_time, f"数据库 {db_id} 不在 DataSkill 允许的库白名单内"
            )

        # 2. 模板短路（优先级 ②：确定性知识路径，零 LLM 开销）
        if enable_template:
            template_result = await self._try_template_shortcut(
                query=query,
                db_id=db_id,
                allowed_db_ids=allowed_db_ids,
                query_templates_config=query_templates_config,
                query_lang=query_lang,
                start_time=start_time,
            )
            if template_result is not None:
                return template_result

        # 3. 构建 LLM callable；不可用 → 降级 legacy 流水线（降级链兜底）
        llm_callable = self._build_llm_callable(tenant_id, llm_id)
        if llm_callable is None:
            logger.warning(
                "[SqlAgentRunner] LLM 不可用，降级到 legacy 启发式规则流水线"
            )
            return await get_database_tool().invoke(input_data)

        # 4. SQL Agent 子图探索（优先级 ③）
        # 预算配置优先级：input 覆盖 > 文件配置 > 代码默认值
        budget = ExplorationBudget.from_config(
            input_data.get("sql_agent_config")  # type: ignore[typeddict-item]
            or self._file_config
        )
        toolset = SqlAgentToolset(
            runtime=self._runtime,
            max_result_rows=budget.max_result_rows,
            allowed_db_ids=allowed_db_ids,
        )
        subgraph = SqlAgentSubgraph(toolset, budget, llm_callable)

        try:
            final_state = await subgraph.run(
                query=query_simplified,
                query_lang=query_lang,
                db_id=db_id,
                exploration_hints=exploration_hints,
            )
        except Exception as e:
            logger.error(f"[SqlAgentRunner] 子图执行异常，降级 legacy: {e}")
            return await get_database_tool().invoke(input_data)

        return self._build_db_result(final_state, budget, query_lang, start_time)

    # ========== 模板短路 ==========
    async def _try_template_shortcut(
        self,
        query: str,
        db_id: str,
        allowed_db_ids: Optional[list[str]],
        query_templates_config: str,
        query_lang: str,
        start_time: float,
    ) -> Optional[dict[str, Any]]:
        """模板匹配命中 → 直接执行返回；未命中/失败 → None 继续探索。"""
        matcher = TemplateMatcher(query_templates_config)
        template_match = matcher.match(query)
        if not template_match:
            return None

        sql = template_match.sql
        target_db_id = db_id or self._default_db_id(allowed_db_ids)
        if not target_db_id:
            logger.info("[SqlAgentRunner] 模板命中但无法确定目标库，交给 SQL Agent")
            return None

        try:
            self._runtime.validate_sql(sql)
        except Exception as e:
            logger.warning(f"[SqlAgentRunner] 模板 SQL 安全校验失败: {e}，交给 SQL Agent")
            return None

        try:
            rows = await self._runtime.execute_sql(target_db_id, sql)
        except Exception as e:
            logger.warning(f"[SqlAgentRunner] 模板 SQL 执行失败: {e}，交给 SQL Agent")
            return None

        logger.info(f"[SqlAgentRunner] 模板短路命中: {sql}")
        tables = DbRuntime.extract_tables(sql)
        verdict = VERDICT_DATA_FOUND if rows else VERDICT_NO_DATA_CONFIRMED
        return {
            "sql": sql,
            "rows": rows,
            "row_count": len(rows),
            "source": "template",
            "quality_score": 1.0 if rows else 0.0,
            "execution_time_ms": int((time.time() - start_time) * 1000),
            "db_id": target_db_id,
            "tables": tables,
            "schema_discovery_log": [f"命中模板: {sql}"],
            "error_message": "",
            "formatted_result": DbRuntime.format_rows(rows, sql, target_db_id, tables, query_lang),
            "exploration_verdict": verdict,
            "exploration_stats": {"agent_steps": 0, "llm_calls": 0, "sql_execs": 1, "describe_calls": 0, "wall_time_ms": int((time.time() - start_time) * 1000)},
            "step_trace": [{"step": 0, "thought": "模板短路", "action": "template", "args": {"sql": sql[:300]}, "observation": f"返回 {len(rows)} 行", "success": True, "latency_ms": int((time.time() - start_time) * 1000)}],
        }

    def _default_db_id(self, allowed_db_ids: Optional[list[str]]) -> str:
        """模板短路时的默认库：唯一可用库时自动选定。"""
        databases = self._runtime.list_databases()
        if allowed_db_ids:
            databases = [d for d in databases if d["db_id"] in allowed_db_ids]
        if len(databases) == 1:
            return databases[0]["db_id"]
        return ""

    # ========== LLM callable 构建 ==========
    def _build_llm_callable(self, tenant_id: str, llm_id: str) -> Optional[LLMCallable]:
        """将 ModelGateway 适配为 async (prompt) -> str 接口（与 react/graph.py 同构）。

        PR-0.3a（§5.2 调用点 4）：改走 ModelGateway.async_chat，
        返回 ChatResult.content（SQL Agent 协议不变，仍是 async (prompt) -> str）。
        配置不可用时返回 None，触发降级到 legacy 流水线。
        """
        from agent.langgraph.gateways.errors import ModelConfigResolveError
        from agent.langgraph.gateways.factory import get_gateway_resolver

        # 预检：配置不可用时返回 None，触发降级（与历史 get_chat_model 返回 None 同语义）
        # 通过尝试解析配置来预检（不实际调用 LLM）
        try:
            from agent.langgraph.gateways.llm_bundle_adapter import LLMBundleAdapter
            LLMBundleAdapter._resolve_chat_model(tenant_id, llm_id)
        except ModelConfigResolveError as e:
            logger.warning(
                f"[SqlAgentRunner] Chat 模型配置不可用，将降级到 legacy: {e}"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[SqlAgentRunner] Chat 模型预检异常，将降级到 legacy: {e}"
            )
            return None

        resolver = get_gateway_resolver()

        async def llm_callable(prompt: str) -> str:
            """LLMCallable 协议：async (prompt) -> str。

            内部通过 ModelGateway.async_chat 调用，返回 ChatResult.content。
            **ERROR** 语义保持（SQL Agent 子图已有 startswith("**ERROR**") 判断）。
            """
            try:
                gw = await resolver.model_for(tenant_id)
                result = await gw.async_chat(
                    tenant_id=tenant_id,
                    llm_id=llm_id,
                    system="",
                    history=[{"role": "user", "content": prompt}],
                    gen_conf=SQL_AGENT_GEN_CONF,
                    prefer_bundle=True,
                )
                return result.content
            except ModelConfigResolveError as e:
                # 配置解析失败 → 返回 **ERROR**（SQL Agent 子图会走降级路径）
                logger.warning(f"[SqlAgentRunner] ModelGateway 配置解析失败: {e}")
                return f"**ERROR**:ModelConfigResolveError: {e}"
            except Exception as e:
                logger.warning(f"[SqlAgentRunner] ModelGateway 调用异常: {e}")
                return f"**ERROR**:{type(e).__name__}: LLM 调用失败"

        return llm_callable

    # ========== db_result 组装 ==========
    def _build_db_result(
        self,
        final_state: SqlAgentState,
        budget: ExplorationBudget,
        query_lang: str,
        start_time: float,
    ) -> dict[str, Any]:
        """从子图终态组装标准 db_result（契约只增不改）。"""
        verdict = final_state.get("exploration_verdict", VERDICT_EXPLORATION_FAILED)
        sql = final_state.get("final_sql", "")
        rows = final_state.get("final_rows", [])
        db_id = final_state.get("final_db_id", "")
        tables = DbRuntime.extract_tables(sql) if sql else final_state.get("final_tables", [])
        step_trace = final_state.get("step_trace", [])

        # 终态 → 质量分与错误信息
        if verdict == VERDICT_DATA_FOUND:
            quality_score = 1.0
            error_message = ""
        elif verdict == VERDICT_NO_DATA_CONFIRMED:
            quality_score = 0.5
            error_message = ""
        elif verdict == VERDICT_BUDGET_EXHAUSTED:
            quality_score = 0.0
            error_message = f"探索预算耗尽: {final_state.get('budget_stop_reason', '')}"
        else:
            quality_score = 0.0
            error_message = (
                final_state.get("llm_error")
                or final_state.get("give_up_reason")
                or "SQL Agent 探索失败"
            )

        # 格式化结果
        if rows:
            formatted_result = DbRuntime.format_rows(rows, sql, db_id, tables, query_lang)
        elif verdict == VERDICT_NO_DATA_CONFIRMED:
            formatted_result = "经探查确认，数据库中无符合条件的数据。"
        else:
            formatted_result = ""

        # schema_discovery_log 兼容：从 step_trace 派生字符串摘要
        schema_discovery_log = [
            f"step {entry.get('step')}: {entry.get('action')} "
            f"{entry.get('args', {})} → {entry.get('observation', '')[:100]}"
            for entry in step_trace
        ]

        execution_time_ms = int((time.time() - start_time) * 1000)

        return {
            "sql": sql,
            "rows": rows,
            "row_count": len(rows),
            "source": "sql_agent",
            "quality_score": quality_score,
            "execution_time_ms": execution_time_ms,
            "db_id": db_id,
            "tables": tables,
            "schema_discovery_log": schema_discovery_log,
            "error_message": error_message,
            "formatted_result": formatted_result,
            # ---- 新增字段（契约扩展） ----
            "exploration_verdict": verdict,
            "exploration_stats": budget.snapshot(
                step_count=final_state.get("step_count", 0),
                llm_call_count=final_state.get("llm_call_count", 0),
                sql_exec_count=final_state.get("sql_exec_count", 0),
                describe_count=final_state.get("describe_count", 0),
            ),
            "step_trace": step_trace,
            "finish_summary": final_state.get("finish_summary", ""),
            "give_up_reason": final_state.get("give_up_reason", ""),
        }

    def _error_result(self, start_time: float, error_message: str) -> dict[str, Any]:
        """错误结果（前置失败，未进入探索）。"""
        return {
            "sql": "",
            "rows": [],
            "row_count": 0,
            "source": "sql_agent",
            "quality_score": 0.0,
            "execution_time_ms": int((time.time() - start_time) * 1000),
            "db_id": "",
            "tables": [],
            "schema_discovery_log": [],
            "error_message": error_message,
            "formatted_result": "",
            "exploration_verdict": VERDICT_EXPLORATION_FAILED,
            "exploration_stats": {"agent_steps": 0, "llm_calls": 0, "sql_execs": 0, "describe_calls": 0, "wall_time_ms": int((time.time() - start_time) * 1000)},
            "step_trace": [],
            "finish_summary": "",
            "give_up_reason": "",
        }


# 全局实例
_sql_agent_runner_instance: Optional[SqlAgentRunner] = None


def get_sql_agent_runner() -> SqlAgentRunner:
    """获取 SqlAgentRunner 单例实例。"""
    global _sql_agent_runner_instance
    if _sql_agent_runner_instance is None:
        _sql_agent_runner_instance = SqlAgentRunner()
    return _sql_agent_runner_instance


__all__ = [
    "DatabaseToolOutput",
    "MODE_LEGACY",
    "MODE_SQL_AGENT",
    "SqlAgentRunner",
    "get_sql_agent_runner",
]
