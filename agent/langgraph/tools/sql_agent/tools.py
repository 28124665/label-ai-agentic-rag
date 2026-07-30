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
"""SQL Agent 细粒度工具集（薄封装 + 独立护栏）。

4 个工具（docs/数据库Tool渐进式披露LLM化落地设计.md §3.2）：
- list_databases: 列出可见库（db_id + description）
- list_tables: 列出指定库表白名单（表名+注释，截断）
- describe_table: 查看指定表 schema（幻觉表名护栏）
- execute_sql: 执行只读 SQL（AST 校验 + 白名单 + LIMIT 注入 + 行数截断）

核心原则：工具错误不抛异常，返回结构化错误作为 Observation，
让 Agent 基于错误信息自愈（业界 SQL Agent 模式的关键：
错误信息本身就是自愈的输入）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from agent.langgraph.tools.db_runtime import DbRuntime

logger = logging.getLogger(__name__)

# Observation 文本最大字符数（与 react/executor 一致）
MAX_OBSERVATION_CHARS = 3000
# list_tables 一次最多返回的表数量
MAX_LIST_TABLES = 200
# 表结构/结果摘要中最多展示的列数
MAX_COLUMNS_IN_OBSERVATION = 50


class SqlAgentToolset:
    """SQL Agent 工具集。

    每个 invoke_xxx 返回 (observation_text, structured_data, success)，
    structured_data 供子图状态更新使用（如选定 db_id、缓存表清单）。
    """

    def __init__(
        self,
        runtime: DbRuntime,
        max_result_rows: int = 500,
        allowed_db_ids: Optional[list[str]] = None,
    ):
        """初始化。

        Args:
            runtime: DbRuntime 原子层
            max_result_rows: execute_sql 返回行数上限（同时用于 LIMIT 注入）
            allowed_db_ids: 可选的库级白名单（如 DataSkill db_targets 约束），
                None 表示不限制
        """
        self._runtime = runtime
        self._max_result_rows = max_result_rows
        self._allowed_db_ids = set(allowed_db_ids) if allowed_db_ids else None
        # describe 缓存：{(db_id, table_name): schema}，命中缓存不计 describe 次数
        self._describe_cache: dict[tuple[str, str], dict] = {}
        # 各库已披露的表清单缓存：{db_id: [table_name, ...]}（幻觉护栏依据）
        self._listed_tables: dict[str, list[str]] = {}

    # ========== list_databases ==========
    async def invoke_list_databases(self) -> tuple[str, dict[str, Any], bool]:
        """列出当前租户可见数据库。"""
        databases = self._runtime.list_databases()
        if self._allowed_db_ids is not None:
            databases = [d for d in databases if d["db_id"] in self._allowed_db_ids]

        if not databases:
            return "当前没有可用的数据库。", {"databases": []}, False

        observation = json.dumps(databases, ensure_ascii=False, indent=2)
        return (
            self._truncate(f"可用数据库清单：\n{observation}"),
            {"databases": databases},
            True,
        )

    # ========== list_tables ==========
    async def invoke_list_tables(self, db_id: str) -> tuple[str, dict[str, Any], bool]:
        """列出指定数据库的表白名单（表名+注释）。"""
        guard_error = self._guard_db_id(db_id)
        if guard_error:
            return guard_error, {}, False

        try:
            entries = await self._runtime.list_tables(db_id)
        except Exception as e:
            return f"[ERROR] 获取表清单失败: {e}", {}, False

        table_names = [e["table_name"] for e in entries if e.get("table_name")]
        self._listed_tables[db_id] = table_names

        truncated = len(entries) > MAX_LIST_TABLES
        shown = entries[:MAX_LIST_TABLES]
        observation = json.dumps(shown, ensure_ascii=False, indent=2)
        if truncated:
            observation += f"\n...（共 {len(entries)} 张表，仅展示前 {MAX_LIST_TABLES} 张，请用更精确的条件）"

        return (
            self._truncate(f"数据库 {db_id} 的表清单（{len(entries)} 张）：\n{observation}"),
            {"tables": shown, "table_count": len(entries), "truncated": truncated},
            True,
        )

    # ========== describe_table ==========
    async def invoke_describe_table(
        self, db_id: str, table_name: str
    ) -> tuple[str, dict[str, Any], bool]:
        """查看指定表结构（幻觉表名护栏：必须已在 list_tables 披露清单内）。"""
        guard_error = self._guard_db_id(db_id)
        if guard_error:
            return guard_error, {}, False

        # 幻觉护栏：表名必须在该库已披露的清单内
        listed = self._listed_tables.get(db_id)
        if listed is not None and table_name not in listed:
            return (
                f"[ERROR] 表 {table_name} 不在数据库 {db_id} 的已披露表清单内，"
                f"禁止查询未披露的表。请先从 list_tables 返回的清单中选择。",
                {},
                False,
            )

        cache_key = (db_id, table_name)
        if cache_key in self._describe_cache:
            schema = self._describe_cache[cache_key]
            return (
                self._truncate(f"表 {table_name} 的结构（缓存）：\n{self._format_schema(schema)}"),
                {"schema": schema, "cache_hit": True},
                True,
            )

        try:
            schema = await self._runtime.describe_table(db_id, table_name)
        except Exception as e:
            return f"[ERROR] 获取表结构失败: {e}", {}, False

        self._describe_cache[cache_key] = schema
        return (
            self._truncate(f"表 {table_name} 的结构：\n{self._format_schema(schema)}"),
            {"schema": schema, "cache_hit": False},
            True,
        )

    # ========== execute_sql ==========
    async def invoke_execute_sql(
        self, db_id: str, sql: str
    ) -> tuple[str, dict[str, Any], bool]:
        """执行只读 SQL（五层防护：AST/黑名单 → 表白名单 → LIMIT 注入 → 执行 → 截断）。"""
        guard_error = self._guard_db_id(db_id)
        if guard_error:
            return guard_error, {}, False

        # 1+2. SQL 只读校验 + 表白名单校验
        allowed_tables = self._listed_tables.get(db_id)
        try:
            self._runtime.validate_sql(sql, allowed_tables=allowed_tables)
        except Exception as e:
            return f"[ERROR] SQL 安全校验失败: {e}", {}, False

        # 3. LIMIT 注入：无 LIMIT 时强制追加
        executed_sql = self._ensure_limit(sql)

        # 4. 执行（错误作为 Observation 返回，供 Agent 自愈）
        try:
            rows = await self._runtime.execute_sql(db_id, executed_sql)
        except Exception as e:
            return f"[ERROR] SQL 执行失败: {e}", {}, False

        # 5. 行数截断
        truncated = len(rows) > self._max_result_rows
        shown_rows = rows[: self._max_result_rows]

        row_count = len(shown_rows)
        if row_count == 0:
            observation = f"查询成功，但结果为空（0 行）。SQL：{executed_sql}"
        else:
            observation = (
                f"查询成功，返回 {row_count} 行"
                f"{'（已截断）' if truncated else ''}。\n"
                f"{json.dumps(shown_rows[:20], ensure_ascii=False, indent=2, default=str)}"
            )
            if row_count > 20:
                observation += f"\n...（仅展示前 20 行，共 {row_count} 行）"

        return (
            self._truncate(observation),
            {
                "rows": shown_rows,
                "row_count": row_count,
                "truncated": truncated,
                "executed_sql": executed_sql,
            },
            True,
        )

    # ========== 内部工具方法 ==========
    def _guard_db_id(self, db_id: str) -> str:
        """校验 db_id 合法性。返回空串表示通过，否则返回错误 Observation。"""
        if not db_id:
            return "[ERROR] 缺少 db_id 参数"
        if self._allowed_db_ids is not None and db_id not in self._allowed_db_ids:
            return f"[ERROR] 数据库 {db_id} 不在允许的库白名单内"
        if not self._runtime.validate_database(db_id):
            return f"[ERROR] 数据库 {db_id} 不存在或无权访问"
        return ""

    def _ensure_limit(self, sql: str) -> str:
        """无 LIMIT 时强制注入。"""
        if re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
            return sql
        return f"{sql.rstrip().rstrip(';')} LIMIT {self._max_result_rows}"

    @staticmethod
    def _format_schema(schema: dict) -> str:
        """将表结构格式化为精简文本（限制列数防爆炸）。"""
        if "raw" in schema:
            return str(schema["raw"])[:MAX_OBSERVATION_CHARS]

        columns = schema.get("columns") or schema.get("fields") or []
        if isinstance(columns, list) and columns:
            shown = columns[:MAX_COLUMNS_IN_OBSERVATION]
            text = json.dumps(shown, ensure_ascii=False, indent=2, default=str)
            if len(columns) > MAX_COLUMNS_IN_OBSERVATION:
                text += f"\n...（共 {len(columns)} 列，仅展示前 {MAX_COLUMNS_IN_OBSERVATION} 列）"
            return text

        return json.dumps(schema, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _truncate(text: str) -> str:
        """截断 Observation 文本。"""
        if len(text) <= MAX_OBSERVATION_CHARS:
            return text
        return text[:MAX_OBSERVATION_CHARS] + "…"


# ========== 工具描述（注入 System Prompt，极简，低 Token） ==========
TOOL_DESCRIPTIONS = """可用工具（JSON action 协议）：
1. {"action": "list_databases", "args": {}} — 列出可见数据库（db_id + 业务描述）
2. {"action": "list_tables", "args": {"db_id": "..."}} — 列出指定库的表（表名+注释）
3. {"action": "describe_table", "args": {"db_id": "...", "table_name": "..."}} — 查看表结构
4. {"action": "execute_sql", "args": {"db_id": "...", "sql": "..."}} — 执行只读 SELECT
5. {"action": "finish", "args": {"summary": "..."}} — 数据已足够，结束探索
6. {"action": "give_up", "args": {"reason_type": "no_data|error", "reason": "..."}} — 放弃（确认无数据/无法解决）"""
