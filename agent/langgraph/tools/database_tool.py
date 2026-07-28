#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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
"""Database Tool 封装模块 - LangGraph 版本。

将历史 RAGFlow 数据库查询能力（多 Tool 调度、渐进式 Schema 发现、
模板匹配、NL-to-SQL、自愈重试、结果格式化）封装为独立工具类，
供 LangGraph 调用。

参考原有实现：
- agent/tools/database_common.py: DatabaseToolBase 基类
- agent/tools/database_list_tables.py: ListTablesTool
- agent/tools/database_describe_table.py: DescribeTableTool
- agent/tools/database_execute_sql.py: ExecuteSQLTool
- docs/dbtoolprd.md: 数据库 Tool 设计文档
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, TypedDict

import yaml

from common.mcp_tool_call_conn import MCPToolCallSession
from api.db.services.mcp_server_service import MCPServerService

logger = logging.getLogger(__name__)


class DatabaseToolInput(TypedDict, total=False):
    """Database Tool 输入定义。"""

    query: str  # 用户查询（自然语言，保留原始语言）
    query_simplified: str  # 繁转简后的查询（用于意图路由和表名匹配）
    query_lang: Literal["zh_CN", "zh_TW", "en"]  # 查询语言
    db_id: str  # 目标数据库 ID（可选，未提供时由意图路由决定）
    tenant_id: str  # 租户 ID
    llm_id: str  # LLM 模型 ID（用于 NL-to-SQL）
    mcp_server_name: str  # MCP Server 名称
    db_schema_config: str  # 数据库配置文件路径
    query_templates_config: str  # 查询模板配置文件路径
    enable_self_healing: bool  # 是否启用 SQL 自愈重试
    enable_template: bool  # 是否优先使用模板
    max_schema_tables: int  # 渐进式 Schema 发现最多描述几张表


class DatabaseToolOutput(TypedDict, total=False):
    """Database Tool 输出定义。"""

    sql: str  # 执行的 SQL 语句
    rows: list[dict]  # 查询结果行
    row_count: int  # 结果行数
    source: Literal["template", "nl_to_sql"]  # SQL 来源
    quality_score: float  # 质量评分 0.0 ~ 1.0
    execution_time_ms: int  # 执行耗时（毫秒）
    db_id: str  # 使用的数据库 ID
    tables: list[str]  # 用到的表
    schema_discovery_log: list[str]  # Schema 发现日志
    error_message: str  # 错误信息（如有）
    formatted_result: str  # 格式化后的结果（Markdown + 自然语言）


@dataclass
class TemplateMatch:
    """预定义模板匹配结果。"""

    sql: str
    params: dict[str, Any]


@dataclass
class QueryTemplate:
    """预定义查询模板。"""

    name: str
    description: str
    keywords: list[str]
    sql: str
    parameters: list[dict] = field(default_factory=list)


class TemplateMatcher:
    """预定义查询模板匹配器。"""

    def __init__(self, config_path: str = "conf/query_templates.yaml"):
        self._config_path = config_path
        self._templates: list[QueryTemplate] = []
        self._load_templates()

    def _load_templates(self) -> None:
        """从配置文件加载模板。"""
        if not os.path.exists(self._config_path):
            logger.warning(f"[TemplateMatcher] 模板文件不存在: {self._config_path}")
            return

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            for item in config.get("templates", []):
                self._templates.append(
                    QueryTemplate(
                        name=item.get("name", ""),
                        description=item.get("description", ""),
                        keywords=[str(k).lower() for k in item.get("keywords", [])],
                        sql=item.get("sql", ""),
                        parameters=item.get("parameters", []),
                    )
                )

            logger.info(f"[TemplateMatcher] 加载 {len(self._templates)} 个模板")
        except Exception as e:
            logger.error(f"[TemplateMatcher] 加载模板失败: {e}")

    def match(self, query: str) -> Optional[TemplateMatch]:
        """
        根据查询匹配模板。

        匹配策略：
        1. 查询中包含模板关键词
        2. 提取模板所需参数
        3. 所有 required 参数都提取到才返回
        """
        query_lower = query.lower()

        for template in self._templates:
            if not any(kw in query_lower for kw in template.keywords):
                continue

            params = self._extract_params(query, template.parameters)
            if self._has_all_required_params(params, template.parameters):
                return TemplateMatch(
                    sql=self._substitute_params(template.sql, params),
                    params=params,
                )

        return None

    def _extract_params(self, query: str, parameters: list[dict]) -> dict[str, Any]:
        """从查询中提取模板参数。"""
        params: dict[str, Any] = {}
        for param in parameters:
            name = param.get("name", "")
            param_type = param.get("type", "string")
            extracted = self._extract_param_value(query, name, param_type)
            if extracted is not None:
                params[name] = extracted
        return params

    def _extract_param_value(self, query: str, name: str, param_type: str) -> Optional[Any]:
        """根据参数类型从查询中抽取值。"""
        # SKU / ID 类：查找大写字母+数字组合
        if name.lower() in ("sku_id", "sku", "order_id", "product_id"):
            candidates = re.findall(r"[A-Z0-9]{2,}(?:-[A-Z0-9]+)*", query, re.IGNORECASE)
            for candidate in candidates:
                if "-" in candidate or any(c.isdigit() for c in candidate):
                    return candidate
            return candidates[0] if candidates else None

        # 日期类：查找 YYYY-MM-DD 或 YYYY/MM/DD
        if param_type in ("date", "datetime"):
            match = re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", query)
            if match:
                return match.group(0)
            return None

        # 数字类：提取第一个数字
        if param_type in ("int", "integer", "float", "number"):
            match = re.search(r"\d+", query)
            if match:
                return int(match.group(0))
            return None

        # 默认：尝试提取引号内容或返回 None
        match = re.search(r"['\"]([^'\"]+)['\"]", query)
        if match:
            return match.group(1)

        return None

    def _has_all_required_params(self, params: dict[str, Any], parameters: list[dict]) -> bool:
        """检查是否所有 required 参数都已提取。"""
        for param in parameters:
            if param.get("required", False):
                name = param.get("name", "")
                if name not in params:
                    return False
        return True

    def _substitute_params(self, sql: str, params: dict[str, Any]) -> str:
        """将模板中的 :param 替换为实际值。"""
        result = sql
        for key, value in params.items():
            placeholder = f":{key}"
            if isinstance(value, str):
                # 简单转义单引号，防止 SQL 注入
                safe_value = value.replace("'", "''")
                result = result.replace(placeholder, f"'{safe_value}'")
            else:
                result = result.replace(placeholder, str(value))
        return result


class ExploreCounter:
    """Schema 探查频次限制器。"""

    def __init__(self, max_list_tables: int = 3, max_describe_table: int = 3):
        self._max_list_tables = max_list_tables
        self._max_describe_table = max_describe_table
        self._counters: dict[str, dict[str, int]] = {}

    def can_explore(self, db_id: str, operation: str) -> bool:
        """检查是否还可以继续探查。"""
        counters = self._counters.setdefault(db_id, {"list_tables": 0, "describe_table": 0})
        max_limit = self._max_list_tables if operation == "list_tables" else self._max_describe_table
        return counters.get(operation, 0) < max_limit

    def increment(self, db_id: str, operation: str) -> None:
        """增加探查计数。"""
        counters = self._counters.setdefault(db_id, {"list_tables": 0, "describe_table": 0})
        counters[operation] = counters.get(operation, 0) + 1

    def reset(self, db_id: str) -> None:
        """重置计数器。"""
        if db_id in self._counters:
            del self._counters[db_id]


class DatabaseTool:
    """数据库查询工具 - LangGraph 版本。

    将历史 RAGFlow 数据库查询能力封装为独立工具：
    1. 意图路由（根据用户问题匹配目标数据库）
    2. 混合模式（预定义模板 + NL-to-SQL）
    3. 渐进式 Schema 发现（list_tables → describe_table）
    4. SQL 安全检查（只读 + 黑名单）
    5. SQL 执行（带自愈重试）
    6. 结果格式化（Markdown 表格 + 自然语言）
    """

    component_name = "DatabaseTool"

    # 业务关键词库，用于意图路由和表筛选
    BUSINESS_KEYWORDS = [
        "产量", "产出", "output", "production",
        "成本", "cost", "采购", "purchase",
        "良率", "yield", "品质", "quality", "检验",
        "库存", "stock", "inventory", "warehouse",
        "设备", "equipment", "oee", "工单", "order",
        "订单", "销售", "sale", "revenue", "营收",
    ]

    def __init__(self):
        self._mcp_session: Optional[MCPToolCallSession] = None
        self._db_tools: dict[str, dict[str, Any]] = {}
        self._db_config: dict[str, Any] = {}
        self._template_matcher = TemplateMatcher()
        self._explore_counter = ExploreCounter()

    async def invoke(self, input_data: DatabaseToolInput) -> DatabaseToolOutput:
        """执行数据库查询流程。"""
        start_time = time.time()
        query = input_data.get("query", "")
        query_simplified = input_data.get("query_simplified", "") or query
        query_lang = input_data.get("query_lang", "zh_CN")
        db_id = input_data.get("db_id", "")
        tenant_id = input_data.get("tenant_id", "")
        mcp_server_name = input_data.get("mcp_server_name", "database_mcp_server")
        db_schema_config = input_data.get("db_schema_config", "conf/db_schema.yaml")
        query_templates_config = input_data.get("query_templates_config", "conf/query_templates.yaml")
        enable_self_healing = input_data.get("enable_self_healing", True)
        enable_template = input_data.get("enable_template", True)
        max_schema_tables = input_data.get("max_schema_tables", 3)
        llm_id = input_data.get("llm_id", "")

        if not query:
            return self._empty_result(start_time, "query 不能为空")

        self._template_matcher = TemplateMatcher(query_templates_config)

        # 1. 加载配置并初始化 MCP 会话
        self._load_db_config(db_schema_config)
        try:
            self._init_mcp_session(tenant_id, mcp_server_name)
        except Exception as e:
            logger.error(f"[DatabaseTool] MCP 会话初始化失败: {e}")
            return self._empty_result(start_time, f"MCP 会话初始化失败: {e}")

        if not self._db_tools:
            return self._empty_result(start_time, "未从 MCP Server 获取到任何数据库工具")

        # 2. 意图路由（未指定 db_id 时自动匹配，使用简体查询提高匹配率）
        if not db_id:
            db_id = self._route_by_intent(query_simplified)
            if not db_id:
                return self._empty_result(start_time, "无法根据问题判断目标数据库，请指定 db_id")
            logger.info(f"[DatabaseTool] 意图路由: 目标数据库={db_id}")

        if not self._validate_database(db_id):
            return self._empty_result(start_time, f"数据库 {db_id} 不存在或无权访问")

        # 3. 混合模式：优先模板匹配
        schema_discovery_log: list[str] = []
        table_schemas: list[dict] = []
        sql = ""
        source: Literal["template", "nl_to_sql"] = "nl_to_sql"

        if enable_template:
            template_match = self._template_matcher.match(query)
            if template_match:
                sql = template_match.sql
                source = "template"
                schema_discovery_log.append(f"命中模板: {sql}")
                logger.info(f"[DatabaseTool] 命中模板: {sql}")

        # 4. 未命中模板时进行渐进式 Schema 发现 + NL-to-SQL
        if not sql:
            try:
                schema = await self._progressive_schema_discovery(
                    query_simplified, db_id, max_schema_tables, schema_discovery_log
                )
                table_schemas = list(schema.values())
            except Exception as e:
                return self._empty_result(start_time, f"Schema 发现失败: {e}")

            try:
                sql = await self._generate_sql(query_simplified, schema, db_id, query_lang, tenant_id, llm_id)
                schema_discovery_log.append(f"生成 SQL: {sql}")
                logger.info(f"[DatabaseTool] NL-to-SQL 生成: {sql}")
            except Exception as e:
                logger.error(f"[DatabaseTool] SQL 生成失败: {e}")
                return self._empty_result(start_time, f"SQL 生成失败: {e}")

        # 5. SQL 安全检查
        try:
            self._validate_sql(sql)
        except Exception as e:
            return self._empty_result(start_time, f"SQL 安全检查失败: {e}")

        # 6. 执行 SQL（带自愈重试）
        rows: list[dict] = []
        try:
            rows, sql, schema_discovery_log = await self._execute_with_self_healing(
                query=query_simplified,
                sql=sql,
                db_id=db_id,
                table_schemas=table_schemas,
                schema_discovery_log=schema_discovery_log,
                tenant_id=tenant_id,
                llm_id=llm_id,
                enable_self_healing=enable_self_healing,
            )
        except Exception as e:
            return self._empty_result(start_time, f"SQL 执行失败: {e}")

        # 7. 结果格式化
        row_count = len(rows)
        quality_score = self._evaluate_quality(rows, sql)
        execution_time_ms = int((time.time() - start_time) * 1000)
        tables = self._extract_tables(sql)
        formatted_result = self._format_result(rows, sql, db_id, tables, query_lang)

        logger.info(
            f"[DatabaseTool] 查询完成: db_id={db_id}, sql='{sql}', rows={row_count}, "
            f"score={quality_score:.2f}, source={source}"
        )

        return DatabaseToolOutput(
            sql=sql,
            rows=rows,
            row_count=row_count,
            source=source,
            quality_score=quality_score,
            execution_time_ms=execution_time_ms,
            db_id=db_id,
            tables=tables,
            schema_discovery_log=schema_discovery_log,
            error_message="",
            formatted_result=formatted_result,
        )

    def _load_db_config(self, config_path: str) -> None:
        """加载数据库配置文件。"""
        if not os.path.exists(config_path):
            logger.warning(f"[DatabaseTool] 配置文件不存在: {config_path}")
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                self._db_config = config.get("databases", {})
                logger.info(f"[DatabaseTool] 加载数据库配置: {list(self._db_config.keys())}")
        except Exception as e:
            logger.error(f"[DatabaseTool] 加载数据库配置失败: {e}")

    def _init_mcp_session(self, tenant_id: str, mcp_server_name: str) -> None:
        """初始化 MCP 会话并获取可用 Tool 列表。"""
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

        logger.info(f"[DatabaseTool] 加载 {len(self._db_tools)} 个数据库工具")

    def _validate_database(self, db_id: str) -> bool:
        """验证数据库是否存在。"""
        return f"query_{db_id}" in self._db_tools

    def _route_by_intent(self, query: str) -> Optional[str]:
        """
        根据用户意图路由到目标数据库。

        匹配策略：
        1. 提取查询中的业务关键词
        2. 匹配各 query_{db_id} Tool 的 description
        3. 返回最匹配的 db_id
        """
        query_lower = query.lower()
        keywords = [kw for kw in self.BUSINESS_KEYWORDS if kw in query_lower]

        if not keywords:
            return None

        for tool_name, tool_info in self._db_tools.items():
            if not tool_name.startswith("query_"):
                continue
            description = (tool_info.get("description") or "").lower()
            if any(kw in description for kw in keywords):
                return tool_info.get("db_id")

        # 如果关键词没匹配到 description，默认返回第一个可用的 db_id
        available = self._get_available_databases()
        return available[0] if available else None

    def _get_available_databases(self) -> list[str]:
        """获取可用数据库 ID 列表。"""
        db_ids = set()
        for tool_name, tool_info in self._db_tools.items():
            if tool_name.startswith("query_"):
                db_ids.add(tool_info.get("db_id", ""))
        return sorted([d for d in db_ids if d])

    async def _progressive_schema_discovery(
        self,
        query: str,
        db_id: str,
        max_tables: int,
        log: list[str],
    ) -> dict[str, Any]:
        """
        渐进式 Schema 发现。

        步骤：
        1. 调用 list_tables_{db_id} 获取表名清单
        2. 按关键词筛选最相关的表
        3. 调用 describe_table_{db_id} 获取字段详情
        """
        if not self._explore_counter.can_explore(db_id, "list_tables"):
            raise Exception("list_tables 探查次数超过上限")

        tables = await self._list_tables(db_id)
        self._explore_counter.increment(db_id, "list_tables")
        log.append(f"获取表清单: {len(tables)} 张表")
        logger.info(f"[DatabaseTool] 获取到 {len(tables)} 张表")

        relevant_tables = self._filter_relevant_tables(query, tables, max_tables)
        log.append(f"筛选相关表: {relevant_tables}")

        schema: dict[str, Any] = {}
        for table_name in relevant_tables:
            if not self._explore_counter.can_explore(db_id, "describe_table"):
                log.append(f"describe_table 探查次数超过上限，跳过 {table_name}")
                break

            table_schema = await self._describe_table(db_id, table_name)
            schema[table_name] = table_schema
            self._explore_counter.increment(db_id, "describe_table")
            log.append(f"获取表结构: {table_name}")

        logger.info(f"[DatabaseTool] 获取到 {len(schema)} 张表的结构")
        return schema

    async def _list_tables(self, db_id: str) -> list[str]:
        """获取数据库表清单。"""
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

        try:
            tables_data = json.loads(tables_result)
            return tables_data.get("tables", [])
        except (json.JSONDecodeError, TypeError):
            return [line.strip() for line in tables_result.split("\n") if line.strip()]

    async def _describe_table(self, db_id: str, table_name: str) -> dict:
        """获取表结构详情。"""
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

    def _filter_relevant_tables(self, query: str, tables: list[str], max_tables: int) -> list[str]:
        """根据查询关键词筛选最相关的表。"""
        query_lower = query.lower()
        query_keywords = [kw for kw in self.BUSINESS_KEYWORDS if kw in query_lower]

        scored_tables: list[tuple[int, str]] = []
        for table in tables:
            table_lower = table.lower()
            score = sum(1 for kw in query_keywords if kw in table_lower)
            scored_tables.append((score, table))

        scored_tables.sort(reverse=True, key=lambda x: x[0])
        return [t[1] for t in scored_tables[:max_tables]]

    async def _generate_sql(
        self,
        query: str,
        schema: dict[str, Any],
        db_id: str,
        query_lang: str,
        tenant_id: str,
        llm_id: str,
    ) -> str:
        """生成 SQL：优先使用 LLM，否则使用占位规则。"""
        chat_mdl = self._get_chat_model(tenant_id, llm_id)
        if chat_mdl is None:
            logger.warning("[DatabaseTool] 未获取到 LLM 模型，使用规则 SQL")
            return self._generate_rule_sql(query, schema, db_id)

        prompt = self._build_nl2sql_prompt(query, schema, db_id, query_lang)
        response = await chat_mdl.async_chat(
            "",
            [{"role": "user", "content": prompt}],
            {"temperature": 0.2, "max_tokens": 2048},
        )

        sql = self._extract_sql(response)
        if not sql:
            raise Exception("LLM 未返回有效 SQL")
        return sql

    def _generate_rule_sql(self, query: str, schema: dict[str, Any], db_id: str) -> str:
        """无 LLM 时的兜底规则 SQL。"""
        if not schema:
            return f"SELECT * FROM placeholder_table WHERE query = '{self._escape_sql_string(query)}' LIMIT 10"

        first_table = next(iter(schema.keys()))
        return f"SELECT * FROM {first_table} LIMIT 10"

    async def _self_healing_retry(
        self,
        query: str,
        failed_sql: str,
        error_message: str,
        db_id: str,
        table_schemas: list[dict],
        tenant_id: str,
        llm_id: str,
    ) -> str:
        """SQL 自愈：根据错误信息重新生成 SQL。"""
        chat_mdl = self._get_chat_model(tenant_id, llm_id)
        if chat_mdl is None:
            logger.warning("[DatabaseTool] 无 LLM 模型，无法进行 SQL 自愈")
            return failed_sql

        prompt = self._build_self_healing_prompt(
            query, failed_sql, error_message, table_schemas, db_id
        )
        response = await chat_mdl.async_chat(
            "",
            [{"role": "user", "content": prompt}],
            {"temperature": 0.2, "max_tokens": 2048},
        )

        healed_sql = self._extract_sql(response)
        return healed_sql or failed_sql

    def _build_nl2sql_prompt(
        self,
        query: str,
        schema: dict[str, Any],
        db_id: str,
        query_lang: str,
    ) -> str:
        """构建 NL-to-SQL Prompt。"""
        lang_instruction = {
            "zh_TW": "請使用繁體中文理解問題並生成 SQL。",
            "en": "Please understand the question in English and generate SQL.",
        }.get(query_lang, "请使用简体中文理解问题并生成 SQL。")

        schema_text = json.dumps(schema, ensure_ascii=False, indent=2)

        return f"""{lang_instruction}

你是一个企业数据库查询专家。请根据以下数据库 Schema，将用户问题转换为只读的 SELECT SQL。

数据库：{db_id}

Schema：
{schema_text}

用户问题：{query}

要求：
1. 只生成 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/EXEC
2. 只使用 Schema 中存在的表名和字段名，禁止编造
3. 默认添加 LIMIT 100
4. 直接返回 SQL 代码，不要包含解释
"""

    def _build_self_healing_prompt(
        self,
        query: str,
        failed_sql: str,
        error_message: str,
        table_schemas: list[dict],
        db_id: str,
    ) -> str:
        """构建 SQL 自愈 Prompt。"""
        schema_text = json.dumps(table_schemas, ensure_ascii=False, indent=2)
        return f"""你是一个 SQL 修正专家。以下 SQL 执行时报错，请根据错误信息和 Schema 修正 SQL。

数据库：{db_id}

Schema：
{schema_text}

用户问题：{query}

原 SQL：
{failed_sql}

错误信息：
{error_message}

要求：
1. 只生成修正后的 SELECT 语句
2. 只使用 Schema 中存在的表名和字段名
3. 默认添加 LIMIT 100
4. 直接返回 SQL 代码，不要包含解释
"""

    def _extract_sql(self, text: str) -> str:
        """从 LLM 响应中提取 SQL。"""
        if not text:
            return ""

        # 尝试提取 ```sql ... ``` 块
        match = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # 尝试提取 ``` ... ``` 块
        match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # 尝试提取以 SELECT/WITH 开头的行
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.upper().startswith("SELECT") or stripped.upper().startswith("WITH"):
                return stripped

        return text.strip()

    def _get_chat_model(self, tenant_id: str, llm_id: str) -> Optional[Any]:
        """获取 Chat 模型。"""
        try:
            from api.db.services.llm_service import LLMBundle
            from api.db.joint_services.tenant_model_service import (
                get_model_config_by_type_and_name,
                get_tenant_default_model_by_type,
            )
            from common.constants import LLMType
        except Exception as e:
            logger.warning(f"[DatabaseTool] 无法导入 LLM 相关模块: {e}")
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
            logger.warning(f"[DatabaseTool] 获取 Chat 模型失败: {e}")

        return None

    def _validate_sql(self, sql: str) -> None:
        """校验 SQL 只读性。"""
        sql_upper = sql.strip().upper()

        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            raise Exception(f"仅允许 SELECT/WITH 查询，当前 SQL 以 {sql_upper.split()[0]} 开头")

        dangerous_keywords = [
            "DROP", "DELETE", "UPDATE", "INSERT", "ALTER",
            "TRUNCATE", "CREATE", "EXEC", "EXECUTE", "MERGE",
        ]
        sql_words = set(re.findall(r"[A-Z_]+", sql_upper))
        for keyword in dangerous_keywords:
            if keyword in sql_words:
                raise Exception(f"SQL 包含禁止关键词: {keyword}")

    async def _execute_with_self_healing(
        self,
        query: str,
        sql: str,
        db_id: str,
        table_schemas: list[dict],
        schema_discovery_log: list[str],
        tenant_id: str,
        llm_id: str,
        enable_self_healing: bool,
    ) -> tuple[list[dict], str, list[str]]:
        """执行 SQL，失败时进行自愈重试。"""
        max_retries = 2 if enable_self_healing else 0
        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                rows = await self._execute_sql(db_id, sql)
                logger.info(f"[DatabaseTool] SQL 执行成功: 返回 {len(rows)} 行")
                return rows, sql, schema_discovery_log
            except Exception as e:
                last_error = e
                logger.warning(f"[DatabaseTool] SQL 执行失败 (attempt {attempt + 1}): {e}")

                if attempt < max_retries and enable_self_healing:
                    schema_discovery_log.append(f"SQL 自愈重试: 错误={e}")
                    healed_sql = await self._self_healing_retry(
                        query, sql, str(e), db_id, table_schemas, tenant_id, llm_id
                    )
                    if healed_sql and healed_sql != sql:
                        sql = healed_sql
                        try:
                            self._validate_sql(sql)
                        except Exception as ve:
                            raise Exception(f"自愈 SQL 安全检查失败: {ve}") from ve
                        schema_discovery_log.append(f"自愈后 SQL: {sql}")
                        continue

                break

        raise Exception(f"SQL 执行失败，已重试 {max_retries} 次，最后错误: {last_error}")

    async def _execute_sql(self, db_id: str, sql: str) -> list[dict]:
        """执行 SQL 查询。"""
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
            logger.warning(f"[DatabaseTool] 无法解析 SQL 结果: {result}")
            return []

    def _evaluate_quality(self, rows: list[dict], sql: str) -> float:
        """评估查询质量。"""
        if not rows or not sql:
            return 0.0
        return 1.0

    def _extract_tables(self, sql: str) -> list[str]:
        """从 SQL 中提取表名。"""
        tables = re.findall(r"FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
        joins = re.findall(r"JOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, re.IGNORECASE)
        return list(set(tables + joins))

    def _format_result(
        self,
        rows: list[dict],
        sql: str,
        db_id: str,
        tables: list[str],
        query_lang: str,
    ) -> str:
        """格式化查询结果为 Markdown 表格 + 自然语言摘要。"""
        if not rows:
            return "未查询到数据。"

        # Markdown 表格
        columns = list(rows[0].keys()) if rows else []
        header = "| " + " | ".join(columns) + " |"
        separator = "|" + "|".join([" --- " for _ in columns]) + "|"
        body_lines = [header, separator]
        for row in rows[:20]:
            body_lines.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")

        if len(rows) > 20:
            body_lines.append(f"| ...（省略 {len(rows) - 20} 行） |" + " |".join(["" for _ in columns]) + "|")

        markdown = "\n".join(body_lines)

        # 自然语言摘要
        summary = f"查询到 {len(rows)} 条记录，数据来源：数据库 {db_id}，表 {', '.join(tables)}。"

        return f"{summary}\n\n{markdown}\n\nSQL：\n```sql\n{sql}\n```"

    def _escape_sql_string(self, value: str) -> str:
        """转义 SQL 字符串中的单引号。"""
        return value.replace("'", "''")

    def _empty_result(self, start_time: float, error_message: str = "") -> DatabaseToolOutput:
        """返回空结果。"""
        execution_time_ms = int((time.time() - start_time) * 1000)
        return DatabaseToolOutput(
            sql="",
            rows=[],
            row_count=0,
            source="nl_to_sql",
            quality_score=0.0,
            execution_time_ms=execution_time_ms,
            db_id="",
            tables=[],
            schema_discovery_log=[],
            error_message=error_message,
            formatted_result="",
        )


# 全局实例
_database_tool_instance: Optional[DatabaseTool] = None


def get_database_tool() -> DatabaseTool:
    """获取 DatabaseTool 单例实例。"""
    global _database_tool_instance
    if _database_tool_instance is None:
        _database_tool_instance = DatabaseTool()
    return _database_tool_instance
