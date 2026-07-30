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
"""DbRuntime — 数据库访问共享原子层。

从 DatabaseTool 抽取的 MCP 会话管理、库/表探查、SQL 校验与执行等
原子能力，供以下路径共享：
- DatabaseTool（legacy 启发式探索规则流水线，deprecated）
- SQLAgentSubgraph（业界标准 Agent 驱动渐进式披露）
- DatabaseToolStructuredBackend（DataSkill 结构化路径）

设计约束（docs/数据库Tool渐进式披露LLM化落地设计.md §3.1）：
- 本层不做任何流程编排，只提供原子操作与安全校验
- 方法签名与历史 DatabaseTool 私有方法保持语义等价，保证纯重构零行为变化
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

import yaml

from common.mcp_tool_call_conn import MCPToolCallSession
from api.db.services.mcp_server_service import MCPServerService

logger = logging.getLogger(__name__)

# SQL 黑名单关键词（兜底校验，AST 不可用时使用）
DANGEROUS_SQL_KEYWORDS = [
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER",
    "TRUNCATE", "CREATE", "EXEC", "EXECUTE", "MERGE",
]


class DbRuntime:
    """数据库访问共享原子层。

    职责：
    1. 加载租户数据库配置（conf/db_schema.yaml）
    2. 初始化 MCP 会话并发现数据库工具（query_/list_tables_/describe_table_ 前缀）
    3. 提供 list_databases / list_tables / describe_table / execute_sql 原子操作
    4. SQL 只读校验（优先 sqlglot AST，降级黑名单关键词）
    5. 结果格式化与表名提取
    """

    def __init__(self):
        self._mcp_session: Optional[MCPToolCallSession] = None
        self._db_tools: dict[str, dict[str, Any]] = {}
        self._db_config: dict[str, Any] = {}

    # ========== 配置与会话 ==========
    @property
    def db_tools(self) -> dict[str, dict[str, Any]]:
        """已发现的数据库工具。"""
        return self._db_tools

    @property
    def db_config(self) -> dict[str, Any]:
        """已加载的数据库配置。"""
        return self._db_config

    def load_db_config(self, config_path: str) -> None:
        """加载数据库配置文件。"""
        if not os.path.exists(config_path):
            logger.warning(f"[DbRuntime] 配置文件不存在: {config_path}")
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                self._db_config = config.get("databases", {})
                logger.info(f"[DbRuntime] 加载数据库配置: {list(self._db_config.keys())}")
        except Exception as e:
            logger.error(f"[DbRuntime] 加载数据库配置失败: {e}")

    def init_session(self, tenant_id: str, mcp_server_name: str) -> None:
        """初始化 MCP 会话并获取可用 Tool 列表。

        Raises:
            Exception: MCP Server 不存在
        """
        _, mcp_server = MCPServerService.get_by_name_and_tenant(mcp_server_name, tenant_id)
        if not mcp_server:
            raise Exception(f"MCP Server not found: {mcp_server_name}")

        self._mcp_session = MCPToolCallSession(mcp_server)

        tools = self._mcp_session.get_tools()
        for tool in tools:
            if tool.name.startswith("query_") or \
               tool.name.startswith("list_tables_") or \
               tool.name.startswith("describe_table_"):
                parts = tool.name.split("_", 1)
                if len(parts) > 1:
                    self._db_tools[tool.name] = {
                        "description": tool.description,
                        "db_id": parts[1],
                    }

        logger.info(f"[DbRuntime] 加载 {len(self._db_tools)} 个数据库工具")

    # ========== 库/表探查原子操作 ==========
    def validate_database(self, db_id: str) -> bool:
        """验证数据库是否存在。"""
        return f"query_{db_id}" in self._db_tools

    def list_databases(self) -> list[dict[str, str]]:
        """列出当前租户可见数据库（db_id + description）。

        description 优先取 conf/db_schema.yaml 中的业务描述，
        缺失时取 MCP query_{db_id} 工具的描述。
        """
        db_ids: set[str] = set()
        for tool_name, tool_info in self._db_tools.items():
            if tool_name.startswith("query_"):
                db_id = tool_info.get("db_id", "")
                if db_id:
                    db_ids.add(db_id)

        databases: list[dict[str, str]] = []
        for db_id in sorted(db_ids):
            config_entry = self._db_config.get(db_id, {}) or {}
            description = config_entry.get("description", "")
            if not description:
                tool_info = self._db_tools.get(f"query_{db_id}", {})
                description = tool_info.get("description", "") or ""
            databases.append({"db_id": db_id, "description": description})
        return databases

    async def list_tables(self, db_id: str) -> list[dict[str, str]]:
        """获取数据库表清单（含注释，归一化为 dict 列表）。

        MCP 返回的表项可能是字符串或带 comment 的对象，统一归一化为：
        [{"table_name": ..., "comment": ...}]

        Raises:
            Exception: 数据库不存在或 MCP 调用失败
        """
        list_tool_name = f"list_tables_{db_id}"
        if list_tool_name not in self._db_tools:
            raise Exception(f"数据库 {db_id} 不存在或无权访问")

        tables_result = await asyncio.to_thread(
            self._mcp_session.tool_call,
            name=list_tool_name,
            arguments={},
        )

        if tables_result.startswith("MCP server error") or tables_result.startswith("Error"):
            raise Exception(f"获取表清单失败: {tables_result}")

        raw_tables: list[Any] = []
        try:
            tables_data = json.loads(tables_result)
            raw_tables = tables_data.get("tables", [])
        except (json.JSONDecodeError, TypeError):
            raw_tables = [line.strip() for line in tables_result.split("\n") if line.strip()]

        return [self._normalize_table_entry(t) for t in raw_tables]

    @staticmethod
    def _normalize_table_entry(entry: Any) -> dict[str, str]:
        """将 MCP 返回的表项归一化为 {table_name, comment}。"""
        if isinstance(entry, dict):
            return {
                "table_name": str(entry.get("table_name") or entry.get("name") or ""),
                "comment": str(entry.get("comment") or entry.get("description") or ""),
            }
        return {"table_name": str(entry), "comment": ""}

    async def describe_table(self, db_id: str, table_name: str) -> dict:
        """获取表结构详情。

        Raises:
            Exception: 数据库不存在或 MCP 调用失败
        """
        describe_tool_name = f"describe_table_{db_id}"
        if describe_tool_name not in self._db_tools:
            raise Exception(f"数据库 {db_id} 不存在或无权访问")

        table_schema_result = await asyncio.to_thread(
            self._mcp_session.tool_call,
            name=describe_tool_name,
            arguments={"table_name": table_name},
        )

        if table_schema_result.startswith("MCP server error") or \
           table_schema_result.startswith("Error"):
            raise Exception(f"获取表结构失败: {table_schema_result}")

        try:
            return json.loads(table_schema_result)
        except (json.JSONDecodeError, TypeError):
            return {"raw": table_schema_result}

    # ========== SQL 校验与执行 ==========
    def validate_sql(self, sql: str, allowed_tables: Optional[list[str]] = None) -> None:
        """校验 SQL 只读性（优先 AST，降级黑名单）。

        Args:
            sql: 待校验 SQL
            allowed_tables: 可选的表白名单，提供时校验 SQL 引用的表均在名单内

        Raises:
            Exception: 校验失败
        """
        sql_upper = sql.strip().upper()

        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            raise Exception(f"仅允许 SELECT/WITH 查询，当前 SQL 以 {sql_upper.split()[0]} 开头")

        if not self._validate_sql_ast(sql):
            self._validate_sql_blacklist(sql)

        if allowed_tables is not None:
            referenced = {t.lower() for t in self.extract_tables(sql)}
            allowed = {t.lower() for t in allowed_tables}
            unknown = referenced - allowed
            if unknown:
                raise Exception(f"SQL 引用了白名单外的表: {sorted(unknown)}")

    @staticmethod
    def _validate_sql_ast(sql: str) -> bool:
        """使用 sqlglot AST 校验（仅允许查询语句）。

        Returns:
            bool: True 表示 AST 校验通过；False 表示 sqlglot 不可用或解析失败，
                  调用方应降级到黑名单校验
        """
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            return False

        try:
            statements = sqlglot.parse(sql)
        except Exception:
            return False

        if not statements:
            return False

        query_node_types = (
            exp.Select,
            exp.Union,
            exp.Subquery,
            exp.With,
            exp.Intersect,
            exp.Except,
        )
        for statement in statements:
            if statement is None:
                continue
            if not isinstance(statement, query_node_types):
                raise Exception(
                    f"AST 校验仅允许查询语句，检测到 {type(statement).__name__}"
                )
        return True

    @staticmethod
    def _validate_sql_blacklist(sql: str) -> None:
        """黑名单关键词兜底校验。

        Raises:
            Exception: 命中危险关键词
        """
        sql_upper = sql.strip().upper()
        sql_words = set(re.findall(r"[A-Z_]+", sql_upper))
        for keyword in DANGEROUS_SQL_KEYWORDS:
            if keyword in sql_words:
                raise Exception(f"SQL 包含禁止关键词: {keyword}")

    async def execute_sql(self, db_id: str, sql: str) -> list[dict]:
        """执行 SQL 查询。

        Raises:
            Exception: 数据库不存在或 MCP 调用失败
        """
        query_tool_name = f"query_{db_id}"
        if query_tool_name not in self._db_tools:
            raise Exception(f"数据库 {db_id} 不存在或无权访问")

        result = await asyncio.to_thread(
            self._mcp_session.tool_call,
            name=query_tool_name,
            arguments={"sql": sql},
        )

        if result.startswith("MCP server error") or result.startswith("Error"):
            raise Exception(f"SQL 执行失败: {result}")

        try:
            data = json.loads(result)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return [data]
            else:
                return []
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[DbRuntime] 无法解析 SQL 结果: {result}")
            return []

    # ========== 工具方法 ==========
    @staticmethod
    def extract_tables(sql: str) -> list[str]:
        """从 SQL 中提取表名。"""
        tables = re.findall(r"FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
        joins = re.findall(r"JOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
        return list(set(tables + joins))

    @staticmethod
    def format_rows(
        rows: list[dict],
        sql: str,
        db_id: str,
        tables: list[str],
        query_lang: str = "zh_CN",
    ) -> str:
        """格式化查询结果为 Markdown 表格 + 自然语言摘要。"""
        if not rows:
            return "未查询到数据。"

        columns = list(rows[0].keys()) if rows else []
        header = "| " + " | ".join(columns) + " |"
        separator = "|" + "|".join([" --- " for _ in columns]) + "|"
        body_lines = [header, separator]
        for row in rows[:20]:
            body_lines.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")

        if len(rows) > 20:
            body_lines.append(f"| ...（省略 {len(rows) - 20} 行） |" + " |".join(["" for _ in columns]) + "|")

        markdown = "\n".join(body_lines)
        summary = f"查询到 {len(rows)} 条记录，数据来源：数据库 {db_id}，表 {', '.join(tables)}。"

        return f"{summary}\n\n{markdown}\n\nSQL：\n```sql\n{sql}\n```"

    def get_chat_model(self, tenant_id: str, llm_id: str) -> Optional[Any]:
        """获取 Chat 模型（与历史 DatabaseTool._get_chat_model 语义一致）。"""
        try:
            from api.db.services.llm_service import LLMBundle
            from api.db.joint_services.tenant_model_service import (
                get_model_config_by_type_and_name,
                get_tenant_default_model_by_type,
            )
            from common.constants import LLMType
        except Exception as e:
            logger.warning(f"[DbRuntime] 无法导入 LLM 相关模块: {e}")
            return None

        try:
            if tenant_id and llm_id:
                chat_model_config = get_model_config_by_type_and_name(
                    tenant_id, LLMType.CHAT, llm_id
                )
                if chat_model_config:
                    return LLMBundle(tenant_id, chat_model_config)

            if tenant_id:
                chat_model_config = get_tenant_default_model_by_type(
                    tenant_id, LLMType.CHAT
                )
                if chat_model_config:
                    return LLMBundle(tenant_id, chat_model_config)

        except Exception as e:
            logger.warning(f"[DbRuntime] 获取 Chat 模型失败: {e}")

        return None


# 全局实例（与 get_database_tool 单例模式一致，共享 MCP 会话状态）
_db_runtime_instance: Optional[DbRuntime] = None


def get_db_runtime() -> DbRuntime:
    """获取 DbRuntime 单例实例。"""
    global _db_runtime_instance
    if _db_runtime_instance is None:
        _db_runtime_instance = DbRuntime()
    return _db_runtime_instance
