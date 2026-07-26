以下是 **Agentic RAG 系统（鸿海集团版）—— 数据库 Tool 接入**的开发需求文档。

---

# Agentic RAG 系统二期开发需求文档
## 数据库查询工具（Database Tool）接入模块

> **项目名称**：鸿海集团 Agentic RAG 智能知识问答系统 —— 数据库 Tool 扩展
> **文档版本**：V2.0
> **目标用户**：鸿海集团高管及中层管理人员
> **关联系统**：SAP S/4HANA、MES、QMS、PLM 等企业核心业务系统
> **关联文档**：[Database MCP Server 设计文档](./mcp_db_server_design.md)
> **设计原则**：分多个 Tool + 按需分步注入 Schema


## 一、项目背景与目标

### 1.1 项目背景

当前 Agentic RAG 系统已具备基于非结构化文档（PDF、Word、操作规程、报表等）的语义检索与问答能力。然而，富士康的大量核心业务数据存储在结构化数据库中，包括：

- **SAP S/4HANA**：作为集团数字化核心，承载财务、供应链、采购等关键数据
- **MES（制造执行系统）** ：管理生产工单、设备状态、在制品追踪等实时生产数据
- **QMS（品质管理系统）** ：存储来料检验、制程检验、出货检验等品质数据
- **PLM（产品生命周期管理系统）** ：管理 BOM、工程变更、工艺参数等产品数据

当前系统对这些**结构化数据**处于"盲区"状态，管理者无法通过自然语言直接查询产量、良率、库存、设备 OEE 等关键指标。

### 1.2 项目目标

本期目标是为 Agentic RAG 系统**接入数据库查询能力（Database Tool）** ，使 Agent 能够：

1. **理解自然语言问题**，自动生成并执行 SQL 查询
2. **安全地访问**企业核心数据库（只读模式、权限隔离）
3. **与现有 RAG 能力协同**，实现"向量检索找文档 + 数据库查询找数字"的混合问答
4. **以自然语言返回查询结果**，将数据库中的数字转化为管理者可读的业务洞察


## 二、需求范围

### 2.1 功能需求

| 编号 | 需求条目 | 优先级 | 详细说明 |
| :--- | :--- | :--- | :--- |
| **FR-DB-01** | 多数据库类型支持 | P0 | 支持连接 SAP HANA、PostgreSQL、SQL Server 等富士康现用数据库类型 |
| **FR-DB-02** | Schema 静态配置 | P0 | 通过 YAML 配置文件预定义可访问的表白名单及字段说明（表名、字段名、字段类型、字段注释），Agent 启动时加载，禁止动态发现未授权的表 |
| **FR-DB-03** | Text-to-SQL 生成 | P0 | Agent 将自然语言问题自动转换为可执行的 SQL 查询语句 |
| **FR-DB-04** | SQL 安全执行 | P0 | 仅允许执行只读 SELECT 查询，强制拦截 DROP/DELETE/UPDATE 等危险操作 |
| **FR-DB-05** | 查询结果自然语言化 | P0 | 将数据库返回的原始数据（元组列表）转换为自然语言描述或 Markdown 表格 |
| **FR-DB-06** | 与 RAG Tool 协同路由 | P0 | Agent 根据问题意图自动判断调用 Database Tool 还是 RAG Tool |
| **FR-DB-07** | 多表联合查询 | P1 | 支持涉及多张表的复杂查询（如 JOIN 操作） |
| **FR-DB-08** | SQL 自愈重试 | P1 | SQL 执行失败时，Agent 自动修正并重试 |
| **FR-DB-09** | 查询结果引用溯源 | P1 | 返回结果时标注数据来源（数据库名、表名、查询时间） |
| **FR-DB-10** | 查询结果导出 | P2 | 支持将查询结果导出为 CSV/Excel 格式 |
| **FR-DB-11** | 预定义查询模板 | P1 | 针对高频业务场景（如"查询库存"、"查询良率"），提供预定义 SQL 模板，用户只需填充参数，提升查询准确性和安全性 |
| **FR-DB-12** | MCP 数据库连接 | P0 | 通过 MCP Server 连接不同数据库（业务库、清洗库、历史库），Database Tool 只负责调用 MCP，不直接管理数据库连接 |
| **FR-DB-13** | 混合模式支持 | P0 | 同时支持预定义模板查询和 NL-to-SQL 生成，高频场景优先使用模板，复杂场景使用 LLM 生成 SQL |

### 2.2 非功能需求

| 编号 | 需求条目 | 目标值 |
| :--- | :--- | :--- |
| **NFR-DB-01** | Text-to-SQL 生成准确率 | ≥ 85%（基于测试集） |
| **NFR-DB-02** | SQL 执行延迟 | < 3s（简单查询）、< 10s（复杂多表查询） |
| **NFR-DB-03** | 并发查询支持 | ≥ 20 QPS |
| **NFR-DB-04** | 数据安全 | 只读账户 + 行级权限隔离 |
| **NFR-DB-05** | 审计日志 | 完整记录每次查询的：用户、问题、生成 SQL、执行结果、耗时 |
| **NFR-DB-06** | 多语言支持 | 支持繁体中文、简体中文、英文三种语言的查询输入与结果输出，与现有系统的繁简转换模块集成 |
| **NFR-DB-07** | 可观测性 | 提供 Prometheus 指标（查询次数、成功率、延迟、返回行数）和 JSON 结构化日志 |


## 三、技术方案

### 3.1 整体架构

采用 **Database Tool + MCP Server** 分层架构，遵循 **"分多个 Tool + 按需分步注入 Schema"** 设计原则。Database Tool 负责意图识别、Tool 路由、渐进式 Schema 发现和 SQL 生成；MCP Server 按业务域暴露细粒度 Tool，负责数据库连接和执行。

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户输入                                 │
└─────────────────────────────────────────────────────────────────┘
                                ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Router Agent（意图路由）                      │
│  判断：问题需要查数据库？还是查文档？还是两者都要？              │
└─────────────────────────────────────────────────────────────────┘
                    ↓                       ↓
┌───────────────────────────────┐ ┌───────────────────────────────┐
│   Database Tool（新增·V2.0）   │ │      RAG Tool（已有）          │
│  • 多 Tool 调度（按意图路由）  │ │  • 向量检索                    │
│  • 渐进式 Schema 发现          │ │  • 文档问答                    │
│  • 混合模式（模板+NL-to-SQL）  │ │                              │
│  • 探查频次限制 + 失败自愈     │ │                              │
└───────────────────────────────┘ └───────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────────────┐
│              MCP Server 层（按业务域暴露细粒度 Tool）             │
│                                                                 │
│  每个数据库自动生成 3 个 Tool：                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ query_{db_id}          → 执行 SQL（只读+LIMIT+超时）      │   │
│  │ list_tables_{db_id}    → 返回表名清单（仅表名）           │   │
│  │ describe_table_{db_id} → 返回单表字段详情（被动响应）      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │ erp-prod    │  │ mes-prod    │  │ qms-prod    │  ...       │
│  │ (3 Tools)   │  │ (3 Tools)   │  │ (3 Tools)   │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
│                                                                 │
│  • Schema 信息 TTL 缓存（300s）                                  │
│  • 工具级权限控制                                                │
│  • SQL 安全拦截（只读+黑名单+LIMIT）                             │
└─────────────────────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────────────────────┐
│                    结果格式化与答案生成                          │
└─────────────────────────────────────────────────────────────────┘
```

**架构优势：**
- **解耦**：Database Tool 不关心底层数据库连接细节，只负责意图识别、Tool 路由和 SQL 生成
- **细粒度**：MCP Server 按业务域拆分为多个独立 Tool，每个 Tool 有唯一 `name` 和聚焦的 `description`
- **按需注入**：Schema 信息通过 `list_tables` + `describe_table` 被动响应，**禁止主动推送**，让模型始终在最小上下文内获得最精准信息
- **可扩展**：新增数据库只需在 MCP Server 配置文件中添加条目，自动注册对应 Tool
- **符合现有架构**：复用项目已有的 MCP 工具调用机制（`MCPToolCallSession`、`MCPServerService`）

### 3.2 核心组件设计

#### 3.2.1 DatabaseTool 核心组件（多 Tool 调度 + 渐进式 Schema 发现）

基于现有 Agent 框架的 `ToolBase` 实现，Database Tool 负责**多 Tool 调度**、**渐进式 Schema 发现**和 **SQL 生成**：

```python
from agent.tools.base import ToolBase, ToolParamBase
from common.mcp_tool_call_conn import MCPToolCallSession
from typing import Any, Dict, List
from dataclasses import dataclass

@dataclass
class DBToolInfo:
    """数据库 Tool 信息"""
    name: str              # 如 "query_erp_prod"
    description: str       # 业务域描述（不含表名/字段名）
    db_id: str             # 数据库标识

class DatabaseQueryTool(ToolBase):
    """数据库查询工具 V2.0 - 多 Tool 调度 + 渐进式 Schema 发现"""
    
    # 探查频次限制（单轮对话）
    MAX_LIST_TABLES_CALLS = 3
    MAX_DESCRIBE_TABLE_CALLS = 3
    
    def __init__(self, tool_id: str, param: DatabaseQueryToolParam):
        super().__init__(tool_id, param)
        self.sql_generator = TextToSQLGenerator()
        self.result_formatter = QueryResultFormatter()
        self.template_matcher = TemplateMatcher()
        
        # 多 Tool 注册表（启动时从 MCP Server 获取）
        self._db_tools: Dict[str, DBToolInfo] = {}
        self._explore_counters: Dict[str, Dict[str, int]] = {}  # db_id -> {list_tables: N, describe_table: N}
    
    async def initialize(self):
        """启动时从 MCP Server 获取所有可用 DB Tool 列表"""
        mcp_session = self._create_mcp_session()
        try:
            tools = await mcp_session.list_tools()
            for tool in tools:
                if tool.name.startswith("query_") or \
                   tool.name.startswith("list_tables_") or \
                   tool.name.startswith("describe_table_"):
                    db_id = tool.name.split("_", 2)[-1] if "_" in tool.name else ""
                    self._db_tools[tool.name] = DBToolInfo(
                        name=tool.name,
                        description=tool.description,
                        db_id=db_id
                    )
        finally:
            mcp_session.close()
    
    async def execute(self, query: str, **kwargs) -> Dict[str, Any]:
        """
        执行数据库查询的主流程（V2.0）
        
        流程：
        1. 按意图路由到目标 DB Tool
        2. 渐进式 Schema 发现（list_tables → describe_table）
        3. 生成 SQL 并执行
        4. 失败自愈（最多 2 次）
        """
        # 1. 按意图路由 Tool
        target_db_id = self._route_by_intent(query)
        if not target_db_id:
            raise ValueError("未找到匹配的数据库 Tool")
        
        # 2. 混合模式路由：优先匹配预定义模板
        template_match = self.template_matcher.match(query, target_db_id)
        if template_match:
            sql = template_match.sql
            params = template_match.params
        else:
            # 3. 渐进式 Schema 发现
            schema = await self._progressive_schema_discovery(query, target_db_id)
            
            # 4. NL-to-SQL 生成
            sql = await self.sql_generator.generate(query, schema, **kwargs)
            params = {}
        
        # 5. 执行 SQL（带自愈重试）
        result = await self._execute_with_self_healing(
            query=query,
            sql=sql,
            params=params,
            db_id=target_db_id,
            schema=schema,
            max_retries=2
        )
        
        # 6. 结果格式化
        formatted_result = self.result_formatter.format(
            result["raw_result"], 
            query, 
            include_markdown=True
        )
        
        return {
            "answer": formatted_result["natural_language"],
            "markdown_table": formatted_result["markdown"],
            "sql": result["sql"],
            "db_id": target_db_id,
            "tables": self._extract_tables(result["sql"]),
            "row_count": len(result["raw_result"]),
            "query_time": formatted_result["query_time"],
            "source": f"数据库查询：{target_db_id}"
        }
    
    def _route_by_intent(self, query: str) -> str | None:
        """
        按用户意图路由到目标 DB Tool
        
        策略：
        1. 提取查询关键词（如"产量"、"成本"、"良率"）
        2. 匹配各 Tool 的 description（只含意图触发词）
        3. 返回最匹配的 db_id
        """
        keywords = self._extract_keywords(query)
        
        # 遍历所有 query_{db_id} Tool，匹配 description
        for tool_name, tool_info in self._db_tools.items():
            if not tool_name.startswith("query_"):
                continue
            
            # 检查关键词是否在 description 中
            if any(kw in tool_info.description for kw in keywords):
                return tool_info.db_id
        
        return None
    
    async def _progressive_schema_discovery(
        self, 
        query: str, 
        db_id: str,
        max_tables_to_describe: int = 3
    ) -> Dict[str, Any]:
        """
        渐进式 Schema 发现（三步递进）
        
        步骤：
        1. 调用 list_tables_{db_id} 获取表名清单
        2. 按关键词筛选最相关的 2-3 张表
        3. 调用 describe_table_{db_id} 获取选中表的字段详情
        
        优势：
        - 避免一次性加载全量 Schema 撑爆上下文
        - 只获取与查询相关的表结构
        - 符合"按需分步注入"原则
        """
        mcp_session = self._create_mcp_session()
        
        try:
            # 步骤 1：获取表名清单
            list_tool_name = f"list_tables_{db_id}"
            if list_tool_name not in self._db_tools:
                raise ValueError(f"Tool {list_tool_name} 不存在")
            
            tables_result = await mcp_session.tool_call(
                name=list_tool_name,
                arguments={}
            )
            all_tables = tables_result.get("tables", [])
            
            # 步骤 2：按关键词筛选相关表
            relevant_tables = self._filter_relevant_tables(query, all_tables, max_tables_to_describe)
            
            # 步骤 3：获取选中表的字段详情
            schema = {}
            describe_tool_name = f"describe_table_{db_id}"
            
            for table_name in relevant_tables:
                table_schema = await mcp_session.tool_call(
                    name=describe_tool_name,
                    arguments={"table_name": table_name}
                )
                schema[table_name] = table_schema
            
            return schema
        
        finally:
            mcp_session.close()
    
    def _filter_relevant_tables(
        self, 
        query: str, 
        all_tables: List[str], 
        max_tables: int
    ) -> List[str]:
        """
        按查询关键词筛选最相关的表
        
        策略：
        1. 提取查询中的业务实体（如"产量"、"订单"、"库存"）
        2. 计算每个表名与查询的相关性得分
        3. 返回得分最高的 top-N 张表
        """
        query_keywords = self._extract_keywords(query)
        
        # 简单相关性 scoring（实际可用更复杂的语义匹配）
        scored_tables = []
        for table in all_tables:
            score = sum(1 for kw in query_keywords if kw in table.lower())
            scored_tables.append((score, table))
        
        # 按得分降序排序，取 top-N
        scored_tables.sort(reverse=True)
        return [t[1] for t in scored_tables[:max_tables]]
    
    async def _execute_with_self_healing(
        self,
        query: str,
        sql: str,
        params: Dict,
        db_id: str,
        schema: Dict,
        max_retries: int = 2
    ) -> Dict[str, Any]:
        """
        带自愈重试的 SQL 执行
        
        重试策略：
        1. 首次执行 SQL
        2. 如果失败（如表不存在、字段拼写错误），分析错误信息
        3. 重新调用 list_tables / describe_table 获取更准确信息
        4. 生成修正后的 SQL 再次执行
        5. 最多重试 max_retries 次
        """
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                # 执行 SQL
                mcp_session = self._create_mcp_session()
                try:
                    raw_result = await mcp_session.tool_call(
                        name=f"query_{db_id}",
                        arguments={"sql": sql, "params": params}
                    )
                    return {"sql": sql, "raw_result": raw_result}
                finally:
                    mcp_session.close()
            
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                
                # 判断是否需要自愈
                if "table not found" in error_msg or "column not found" in error_msg:
                    # 重新探查 Schema
                    schema = await self._progressive_schema_discovery(query, db_id)
                    
                    # 携带错误信息重新生成 SQL
                    sql = await self.sql_generator.generate_with_error_context(
                        query, schema, last_error
                    )
                else:
                    # 其他错误不重试
                    raise
        
        raise Exception(f"SQL 执行失败，已重试 {max_retries} 次：{last_error}")
    
    def _create_mcp_session(self) -> MCPToolCallSession:
        """创建 MCP 工具调用会话"""
        from api.db.services.mcp_server_service import MCPServerService
        # 使用默认 DB MCP Server（或从配置获取）
        mcp_server = MCPServerService.get_by_name("database_mcp_server")
        if not mcp_server:
            raise ValueError("Database MCP Server not found")
        return MCPToolCallSession(mcp_server)
    
    def _extract_keywords(self, query: str) -> List[str]:
        """从查询中提取关键词（简化版）"""
        # 实际可用 jieba 分词 + 业务词典
        business_keywords = ["产量", "成本", "良率", "库存", "订单", "设备", "OEE"]
        return [kw for kw in business_keywords if kw in query]
    
    def _extract_tables(self, sql: str) -> List[str]:
        """从 SQL 中提取表名（简化版）"""
        # 实际可用 sqlparse 解析
        import re
        tables = re.findall(r'FROM\s+(\w+)', sql, re.IGNORECASE)
        return list(set(tables))
```

**核心子组件说明：**

| 组件 | 职责 | 实现要点 |
|------|------|----------|
| **多 Tool 注册表** | 启动时从 MCP Server 获取所有可用 DB Tool | 动态加载 `query_*`、`list_tables_*`、`describe_table_*` |
| **意图路由器** | 按用户问题匹配目标 DB Tool | 关键词匹配 + Tool description 语义匹配 |
| **渐进式 Schema 发现** | 三步递进获取表结构 | `list_tables` → 筛选相关表 → `describe_table` |
| **探查频次限制器** | 防止 Agent 无限循环探查 | 单轮对话最多调用 `list_tables` 3 次、`describe_table` 3 次 |
| **TextToSQLGenerator** | NL-to-SQL 转换 | 调用 LLM、注入 Schema 上下文、Few-shot 示例 |
| **自愈重试控制器** | SQL 执行失败时自动修正 | 分析错误 → 重新探查 Schema → 重新生成 SQL → 再次执行 |
| **MCPToolCallSession** | 调用 MCP Server 执行 Tool | 复用现有 MCP 工具调用机制 |
| **QueryResultFormatter** | 结果格式化 | 自然语言描述 + Markdown 表格 |

#### 3.2.2 安全控制层（必须实现）

企业级 Text-to-SQL 系统必须包含多层安全控制：

| 安全层级 | 实现方式 | 说明 |
| :--- | :--- | :--- |
| **连接层** | 只读数据库账户 | 数据库连接使用只读权限账户，从源头禁止写操作 |
| **SQL 生成层** | 强制 SELECT + 自动 LIMIT | System Prompt 强制约束 Agent 只生成 SELECT 语句，自动追加 LIMIT 50/100 |
| **执行前校验** | 关键词黑名单 + 语法检查 | 拦截 DROP/DELETE/UPDATE/INSERT/ALTER 等危险关键词 |
| **行级权限** | 按用户/部门过滤 | 不同管理者只能查询其权限范围内的数据（如厂区隔离） |
| **审计日志** | 全链路记录 | 记录：用户 → 问题 → 生成 SQL → 执行结果 → 耗时 |

#### 3.2.3 渐进式 Schema 发现（按需分步注入）

富士康的 ERP/MES 数据库可能包含数百张表，但并非所有表都与当前查询相关。采用**渐进式 Schema 发现**策略，遵循"按需分步注入"原则：

**核心原则：**
- **禁止主动推送**：MCP Server 不在启动时或连接时将全量 Schema 推送给模型
- **被动响应**：Schema 信息只能通过 `list_tables` 和 `describe_table` 工具被动响应 Agent 的主动调用
- **三步递进**：先探查表清单 → 再按需取表结构 → 最后生成 SQL

**三步递进流程：**

```
用户提问："Q3 各厂区产量是多少？"
    ↓
[步骤 1] 调用 list_tables_{db_id}
    ↓
返回表名清单：[production_order, output_record, wip_tracking, ...]
    ↓
[步骤 2] 按关键词筛选相关表（"产量" → output_record）
    ↓
[步骤 3] 调用 describe_table_{db_id}(table_name="output_record")
    ↓
返回字段详情：[factory_code, product_name, quantity, record_date, ...]
    ↓
[步骤 4] 生成 SQL 并执行
```

**代码实现：**

```python
async def progressive_schema_discovery(self, query: str, db_id: str) -> Dict[str, Any]:
    """
    渐进式 Schema 发现（三步递进）
    
    优势：
    - 避免一次性加载全量 Schema 撑爆上下文
    - 只获取与查询相关的表结构，节省 Token
    - 符合"按需分步注入"原则
    """
    # 步骤 1：获取表名清单（仅表名，不含字段细节）
    all_tables = await self.mcp_session.tool_call(
        name=f"list_tables_{db_id}",
        arguments={}
    )
    
    # 步骤 2：按关键词筛选最相关的 2-3 张表
    relevant_tables = self._filter_relevant_tables(query, all_tables, max_tables=3)
    
    # 步骤 3：获取选中表的字段详情
    schema = {}
    for table_name in relevant_tables:
        table_schema = await self.mcp_session.tool_call(
            name=f"describe_table_{db_id}",
            arguments={"table_name": table_name}
        )
        schema[table_name] = table_schema
    
    return schema
```

**优势对比：**

| 维度 | 静态全量注入（原方案） | 渐进式按需发现（V2.0 方案） |
|------|---------------------|-------------------------------|
| **Token 消耗** | 高（全量 Schema 可能上万 Token） | 低（仅注入相关表的 Schema） |
| **上下文利用率** | 差（大量无关表结构干扰模型） | 优（模型聚焦于相关表） |
| **安全性** | 中（全量表结构暴露） | 高（按需暴露，最小权限） |
| **性能** | 快（一次性加载） | 中（需多次 MCP 调用，但有缓存） |
| **可控性** | 差（无法动态调整） | 好（按查询意图动态筛选） |
| **适用场景** | 小型数据库（< 50 表） | 大型数据库（数百张表） |

**Schema 缓存策略：**

为避免频繁调用 `describe_table` 造成性能瓶颈，MCP Server 层对 Schema 信息进行 TTL 缓存（默认 300 秒）：

```python
# MCP Server 端实现
class SchemaCache:
    """Schema 信息 TTL 缓存"""
    
    def __init__(self, default_ttl: int = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._default_ttl = default_ttl
    
    def get(self, key: str) -> Any | None:
        """获取缓存值，过期则返回 None"""
        if key not in self._cache:
            return None
        value, expiry_ts = self._cache[key]
        if time.time() >= expiry_ts:
            del self._cache[key]
            return None
        return value
    
    def set(self, key: str, value: Any):
        """设置缓存值"""
        expiry_ts = time.time() + self._default_ttl
        self._cache[key] = (value, expiry_ts)
```

**缓存 Key 设计：**
- `list_tables_{db_id}` → 表名清单
- `describe_table_{db_id}:{table_name}` → 单表字段详情

#### 3.2.4 结果自然语言化

数据库返回的原始数据需转化为管理者可读的格式：

```python
# 原始返回：[(1500000, "iPhone 15")]
# 格式化后："查询结果：产品名称为 iPhone 15，总销售额为 1,500,000 元。"
```

### 3.3 与现有 Agent 流程的集成

Database Tool 不是独立运行的，需要与现有 Agentic RAG 完整流程深度集成：

#### 3.3.1 完整调用流程

```
用户提问
    ↓
[阶段1] 查询预处理（语言检测 + 繁简转换）
    ↓
[阶段2] 查询优化（复杂度分析 + 策略选择 + 重写）
    ↓
[阶段3] 意图路由（判断走 Database / RAG / 混合）  ← 新增路由节点
    ↓
┌───────────────┬──────────────────┬──────────────────┐
│  Database     │  RAG             │  混合             │
│  Tool         │  Tool            │  模式             │
│  ↓            │  ↓               │  ↓                │
│  模板匹配     │  混合检索         │  RAG 先找实体     │
│  / NL-to-SQL  │  (BM25+向量)     │  → DB 再查数据    │
│  ↓            │  ↓               │  ↓                │
│  SQL 安全检查 │  Rerank          │  结果融合          │
│  ↓            │  ↓               │                   │
│  执行查询     │  Grader 评估     │                   │
│  ↓            │  ↓               │                   │
│  结果格式化   │  Prompt 组装     │                   │
└───────────────┴──────────────────┴──────────────────┘
    ↓
[阶段8] LLM 生成答案
    ↓
[阶段9] 幻觉检测（防止 LLM 篡改数据库数字）
    ↓
[阶段10] 可观测性记录
    ↓
返回答案
```

#### 3.3.2 与各组件的集成点

| 现有组件 | 集成方式 | 说明 |
|---------|---------|------|
| **QueryRewriter** | 查询预处理 | 数据库查询同样经过繁简转换和查询重写，确保 NL-to-SQL 输入质量 |
| **意图路由（Router）** | 新增路由节点 | 根据查询复杂度分类结果 + 关键词特征，判断走 Database / RAG / 混合 |
| **Grader** | 结果质量评估 | 对 NL-to-SQL 生成的 SQL 进行语义校验（生成的 SQL 是否符合用户意图） |
| **RetryController** | SQL 自愈重试 | SQL 执行失败时触发重试，最多 2 次，与现有重试机制共享 token 预算 |
| **HallucinationDetector** | 数字忠实度检测 | 防止 LLM 在总结数据库结果时篡改数字（如把"150万"说成"1500万"） |
| **CircuitBreaker** | 数据库连接熔断 | 数据库连接失败时快速失败，避免阻塞整个查询流程 |
| **Metrics** | 指标采集 | 新增 `rag_db_query_total`、`rag_db_query_latency_seconds` 等指标 |
| **StructuredLogger** | 审计日志 | JSON 格式记录：用户 → 问题 → 生成 SQL → 执行结果 → 耗时 |

#### 3.3.3 Database / RAG 路由策略

路由判断遵循 **"关键词特征 + 复杂度分类"** 双重策略：

| 判断条件 | 路由目标 | 示例 |
|---------|---------|------|
| 含数字/统计/聚合词（"多少"、"总计"、"平均"） | Database Tool | "Q3 华东区总产量是多少？" |
| 含精确实体/ID（SKU-xxx、订单号） | Database Tool | "SKU-1001 当前库存是多少？" |
| 模糊/概念/解释类（"原因"、"如何"、"为什么"） | RAG Tool | "生产瓶颈的原因是什么？" |
| 混合（实体+概念） | 混合模式 | "高端机型中利润最高的那款" → RAG 找"高端机型"定义 → DB 查利润排序 |
| 闲聊/问候 | 直接回复 | "你好"、"谢谢" |

#### 3.3.4 SQL 自愈重试流程（V2.0 - 失败自愈 + 重新探查）

与现有 RetryController 协调，SQL 执行失败时的处理（V2.0 增强版）：

```
SQL 执行失败
    ↓
┌─ 语法错误 → LLM 自动修正（最多 2 次），携带错误信息重新生成
├─ 表/字段不存在 → 【V2.0 新增】重新探查 Schema → 重新生成 SQL → 再次执行
│   ├─ 调用 list_tables_{db_id} 获取最新表清单
│   ├─ 调用 describe_table_{db_id} 获取准确字段信息
│   ├─ 携带错误信息 + 新 Schema 重新生成 SQL
│   └─ 最多重试 2 次，仍失败则返回明确错误提示
├─ 权限不足 → 返回权限错误（不重试）
├─ 查询超时 → 自动终止 + 提示用户简化查询条件
└─ 连接失败 → 触发熔断器，快速失败，不影响其他查询
```

**探查频次限制：**

为防止 Agent 陷入无限循环或过度拉取 Schema，单轮对话中对同一数据库的探查次数有上限约束：

| 探查操作 | 单轮对话上限 | 说明 |
|---------|------------|------|
| `list_tables_{db_id}` | 最多 3 次 | 防止无限循环获取表清单 |
| `describe_table_{db_id}` | 最多 3 次 | 防止过度拉取表结构 |

**代码实现：**

```python
class ExploreCounter:
    """探查频次计数器"""
    
    def __init__(self, max_list_tables: int = 3, max_describe_table: int = 3):
        self._counters: Dict[str, Dict[str, int]] = {}
        self._max_list_tables = max_list_tables
        self._max_describe_table = max_describe_table
    
    def can_explore(self, db_id: str, operation: str) -> bool:
        """检查是否可以继续探查"""
        if db_id not in self._counters:
            self._counters[db_id] = {"list_tables": 0, "describe_table": 0}
        
        current = self._counters[db_id][operation]
        max_limit = self._max_list_tables if operation == "list_tables" else self._max_describe_table
        
        return current < max_limit
    
    def increment(self, db_id: str, operation: str):
        """增加探查计数"""
        if db_id not in self._counters:
            self._counters[db_id] = {"list_tables": 0, "describe_table": 0}
        self._counters[db_id][operation] += 1
    
    def reset(self, db_id: str):
        """重置计数器（新一轮对话时调用）"""
        if db_id in self._counters:
            del self._counters[db_id]
```

**失败自愈流程示例：**

```
用户提问："Q3 A 产线的产量是多少？"
    ↓
[第 1 次尝试]
  1. list_tables_erp_prod → [production_order, output_record, ...]
  2. 筛选相关表 → output_record
  3. describe_table_erp_prod("output_record") → [factory_code, quantity, ...]
  4. 生成 SQL: SELECT SUM(quantity) FROM output_record WHERE factory_code = 'A'
  5. 执行失败：错误 "column 'factory_code' not found"
    ↓
[第 2 次尝试 - 自愈]
  1. 重新探查 Schema（探查计数器 +1）
  2. list_tables_erp_prod → [production_order, output_record, ...]
  3. 筛选相关表 → output_record
  4. describe_table_erp_prod("output_record") → [factory_id, prod_quantity, ...]  ← 发现字段名不同
  5. 携带错误信息重新生成 SQL: SELECT SUM(prod_quantity) FROM output_record WHERE factory_id = 'A'
  6. 执行成功 → 返回结果
```

#### 3.3.5 数字忠实度保护

数据库查询结果进入 LLM 总结前，需经过幻觉检测的数字忠实度校验：

```python
# 数据库返回: [{"revenue": 1500000, "product": "iPhone 15"}]
# LLM 总结后: "iPhone 15 的营收为 150 万元"

# 幻觉检测器会校验：
# 1. 提取 LLM 输出中的数字: 150万 = 1,500,000
# 2. 对比数据库原始值: 1,500,000
# 3. 匹配 → 通过
# 若不匹配（如 LLM 说成"1500万元"）→ 触发重新生成或过滤
```

### 3.4 技术选型对比

| 方案 | 优点 | 缺点 | 推荐度 |
| :--- | :--- | :--- | :--- |
| **基于现有 ToolBase 自研** | 与现有 Agent 框架无缝集成、完全可控、可深度定制安全策略和混合模式 | 需自行实现 Text-to-SQL Prompt 工程 | ⭐⭐⭐⭐⭐ **首选** |
| **MCP Toolbox for Databases** | 标准化协议、支持声明式 YAML 配置、连接池管理 | 需额外部署 MCP Server，增加架构复杂度；与现有 ToolBase 集成需适配层 | ⭐⭐⭐⭐ 备选（Phase 3 可评估） |
| **LangChain SQLDatabaseToolkit** | 生态成熟、开箱即用 | 引入额外依赖、与现有框架不兼容、增加维护成本 | ⭐⭐ 不推荐 |


### 3.5 混合模式详细设计

Database Tool 采用**预定义模板 + NL-to-SQL** 的混合模式，根据查询特征自动选择最优路径：

#### 3.5.1 混合模式流程

```
用户自然语言提问
       ↓
  意图识别 + 模板匹配
       ↓
  ┌────┴────┐
  │         │
高频场景    复杂场景
  │         │
预定义模板   NL-to-SQL
  │         │
  └────┬────┘
       ↓
  SQL 安全检查（白名单校验、注入检测）
       ↓
  执行查询（只读、超时控制、行数限制）
       ↓
  结果处理（脱敏、格式化）
       ↓
  LLM 总结 / 直接返回
```

#### 3.5.2 预定义查询模板

针对高频业务场景，提供预定义 SQL 模板，用户只需填充参数：

```yaml
# conf/query_templates.yaml
templates:
  - name: "query_inventory"
    description: "查询指定 SKU 的当前库存"
    keywords: ["库存", "存量", "stock", "inventory"]
    sql: |
      SELECT sku_id, product_name, stock_quantity, warehouse_name, last_update_time
      FROM inventory_current
      WHERE sku_id = :sku_id
    parameters:
      - name: "sku_id"
        type: "string"
        description: "SKU 编号"
        required: true

  - name: "query_production_output"
    description: "查询指定时间范围的产量"
    keywords: ["产量", "产出", "output", "production"]
    sql: |
      SELECT factory_code, product_name, SUM(quantity) as total_output
      FROM output_record
      WHERE record_date BETWEEN :start_date AND :end_date
      GROUP BY factory_code, product_name
      ORDER BY total_output DESC
    parameters:
      - name: "start_date"
        type: "date"
        description: "开始日期"
        required: true
      - name: "end_date"
        type: "date"
        description: "结束日期"
        required: true

  - name: "query_yield_rate"
    description: "查询指定时间范围的良率"
    keywords: ["良率", "良品率", "yield", "quality"]
    sql: |
      SELECT factory_code, product_name,
             SUM(passed_quantity) * 100.0 / SUM(total_quantity) as yield_rate
      FROM iqc_inspection
      WHERE inspection_date BETWEEN :start_date AND :end_date
      GROUP BY factory_code, product_name
    parameters:
      - name: "start_date"
        type: "date"
        required: true
      - name: "end_date"
        type: "date"
        required: true
```

#### 3.5.3 模板匹配逻辑

```python
class TemplateMatcher:
    """预定义查询模板匹配器"""
    
    def match(self, query: str, database: str) -> Optional[TemplateMatch]:
        """
        匹配预定义模板
        
        匹配策略：
        1. 关键词匹配：查询中包含模板定义的关键词
        2. 参数提取：从查询中提取模板所需的参数
        3. 置信度判断：匹配置信度 > 0.8 才使用模板
        
        Returns:
            TemplateMatch 对象（包含 sql 和 params），或 None（未匹配）
        """
        for template in self.templates:
            # 1. 关键词匹配
            if any(kw in query for kw in template.keywords):
                # 2. 参数提取
                params = self.extract_params(query, template.parameters)
                if params:
                    # 3. 置信度判断（可选：使用 LLM 判断是否真的需要该模板）
                    confidence = self.calculate_confidence(query, template)
                    if confidence > 0.8:
                        return TemplateMatch(sql=template.sql, params=params)
        return None
```

### 3.6 静态 Schema 配置格式

完整的 Schema 配置文件示例：

```yaml
# conf/db_schema.yaml
databases:
  # SAP 生产数据库
  sap_production:
    type: "postgresql"
    host: "${SAP_DB_HOST}"
    port: 5432
    database: "sap_production"
    user: "${SAP_DB_USER}"
    password: "${SAP_DB_PASSWORD}"
    
    allowed_tables:
      - name: "production_order"
        description: "生产工单表"
        description_zh_TW: "生產工單表"
        columns:
          - name: "order_id"
            type: "VARCHAR(50)"
            description: "工单编号"
            description_zh_TW: "工單編號"
          - name: "product_name"
            type: "VARCHAR(200)"
            description: "产品名称"
            description_zh_TW: "產品名稱"
          - name: "planned_quantity"
            type: "INTEGER"
            description: "计划产量"
            description_zh_TW: "計劃產量"
          - name: "actual_quantity"
            type: "INTEGER"
            description: "实际产量"
            description_zh_TW: "實際產量"
          - name: "factory_code"
            type: "VARCHAR(20)"
            description: "厂区代码（如：SZ-深圳, KS-昆山）"
            description_zh_TW: "廠區代碼（如：SZ-深圳, KS-昆山）"
          - name: "order_date"
            type: "DATE"
            description: "工单日期"
            description_zh_TW: "工單日期"
          - name: "status"
            type: "VARCHAR(20)"
            description: "工单状态（pending/in_progress/completed/cancelled）"
            description_zh_TW: "工單狀態（pending/in_progress/completed/cancelled）"

      - name: "inventory_current"
        description: "当前库存表"
        description_zh_TW: "當前庫存表"
        columns:
          - name: "sku_id"
            type: "VARCHAR(50)"
            description: "SKU 编号"
            description_zh_TW: "SKU 編號"
          - name: "product_name"
            type: "VARCHAR(200)"
            description: "产品名称"
            description_zh_TW: "產品名稱"
          - name: "stock_quantity"
            type: "INTEGER"
            description: "库存数量"
            description_zh_TW: "庫存數量"
          - name: "warehouse_name"
            type: "VARCHAR(100)"
            description: "仓库名称"
            description_zh_TW: "倉庫名稱"
          - name: "last_update_time"
            type: "TIMESTAMP"
            description: "最后更新时间"
            description_zh_TW: "最後更新時間"

    forbidden_tables:
      - "user_credentials"
      - "system_config"
      - "audit_log"

  # MES 制造执行系统数据库
  mes_system:
    type: "postgresql"
    host: "${MES_DB_HOST}"
    port: 5432
    database: "mes_system"
    user: "${MES_DB_USER}"
    password: "${MES_DB_PASSWORD}"
    
    allowed_tables:
      - name: "equipment_status"
        description: "设备状态表"
        columns:
          - name: "equipment_id"
            type: "VARCHAR(50)"
            description: "设备编号"
          - name: "equipment_name"
            type: "VARCHAR(200)"
            description: "设备名称"
          - name: "status"
            type: "VARCHAR(20)"
            description: "设备状态（running/idle/maintenance/breakdown）"
          - name: "oee_value"
            type: "DECIMAL(5,2)"
            description: "OEE 值（0-100）"
          - name: "record_time"
            type: "TIMESTAMP"
            description: "记录时间"
```

### 3.7 可观测性指标

与现有 `metrics.py` 对齐，新增数据库查询相关指标：

```python
# api/utils/metrics.py 新增指标

# 查询次数统计
rag_db_query_total = Counter(
    "rag_db_query_total",
    "Total database queries",
    ["database", "query_type", "status"]  # query_type: template/nl_to_sql
)

# 查询延迟分布
rag_db_query_latency = Histogram(
    "rag_db_query_latency_seconds",
    "Database query latency in seconds",
    ["database", "query_type"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

# 返回行数分布
rag_db_query_rows = Histogram(
    "rag_db_query_rows",
    "Number of rows returned by database query",
    ["database"],
    buckets=[0, 1, 5, 10, 50, 100, 500, 1000]
)

# SQL 生成延迟
rag_db_sql_generation_latency = Histogram(
    "rag_db_sql_generation_latency_seconds",
    "Text-to-SQL generation time in seconds",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0]
)

# SQL 执行失败率
rag_db_query_failure_rate = Gauge(
    "rag_db_query_failure_rate",
    "Database query failure rate (sliding window)"
)

# 模板匹配命中率
rag_db_template_match_rate = Gauge(
    "rag_db_template_match_rate",
    "Template match rate for database queries"
)
```

### 3.8 错误处理与自愈重试

#### 3.8.1 错误分类与处理策略

| 错误类型 | 错误码 | 处理策略 | 是否重试 |
|---------|-------|---------|---------|
| SQL 语法错误 | `SQL_SYNTAX_ERROR` | LLM 携带错误信息重新生成 SQL | 是（最多 2 次） |
| 表不存在 | `TABLE_NOT_FOUND` | 返回明确错误提示，不重试 | 否 |
| 字段不存在 | `COLUMN_NOT_FOUND` | 返回明确错误提示，不重试 | 否 |
| 权限不足 | `PERMISSION_DENIED` | 返回权限错误，不重试 | 否 |
| 查询超时 | `QUERY_TIMEOUT` | 自动终止 + 提示用户简化查询 | 否 |
| 连接失败 | `CONNECTION_FAILED` | 触发熔断器，快速失败 | 否 |
| 结果为空 | `EMPTY_RESULT` | 返回"未查询到数据"，不视为错误 | 否 |

#### 3.8.2 SQL 自愈重试流程

```python
class SQLSelfHealingRetry:
    """SQL 自愈重试控制器"""
    
    async def execute_with_retry(self, query: str, schema: dict, max_retries: int = 2):
        """
        带自愈重试的 SQL 执行
        
        重试策略：
        1. 首次生成 SQL 并执行
        2. 如果执行失败且为语法错误，携带错误信息让 LLM 重新生成
        3. 最多重试 max_retries 次
        4. 与现有 RetryController 共享 token 预算
        """
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                # 生成 SQL（首次或携带错误信息重新生成）
                if attempt == 0:
                    sql = await self.sql_generator.generate(query, schema)
                else:
                    sql = await self.sql_generator.generate_with_error_context(
                        query, schema, last_error
                    )
                
                # 安全检查
                self.sql_executor.validate(sql)
                
                # 执行查询
                result = self.sql_executor.execute(sql)
                return result
                
            except SQLSyntaxError as e:
                last_error = e
                logging.warning(f"[DatabaseTool] SQL syntax error (attempt {attempt + 1}): {e}")
                continue
                
            except (TableNotFoundError, ColumnNotFoundError) as e:
                # 表/字段不存在，不重试
                raise QueryError(f"查询失败：{e}")
                
            except PermissionDeniedError as e:
                # 权限不足，不重试
                raise QueryError(f"权限不足：{e}")
                
            except QueryTimeoutError as e:
                # 查询超时，不重试
                raise QueryError(f"查询超时，请简化查询条件")
                
            except ConnectionError as e:
                # 连接失败，触发熔断器
                self.circuit_breaker.record_failure()
                raise QueryError(f"数据库连接失败：{e}")
        
        # 达到最大重试次数
        raise QueryError(f"SQL 生成失败，已重试 {max_retries} 次，最后错误：{last_error}")
```

### 3.9 多语言支持

与现有繁简转换模块集成，支持繁体中文、简体中文、英文三种语言：

#### 3.9.1 查询语言处理

```python
# 1. 语言检测（复用现有 lang_detect.py）
query_lang = detect_language(query)

# 2. 繁简转换（复用现有 t2s.py）
if query_lang == "zh_TW":
    query_simplified = traditional_to_simplified(query)
else:
    query_simplified = query

# 3. NL-to-SQL 生成时使用简体查询
sql = await sql_generator.generate(query_simplified, schema)

# 4. 结果输出时根据原始语言返回对应语言
if query_lang == "zh_TW":
    result_language = "zh_TW"
elif query_lang == "zh_CN":
    result_language = "zh_CN"
else:
    result_language = "en"
```

#### 3.10.2 Schema 多语言支持

Schema 配置支持多语言字段描述（见 3.6 节示例），NL-to-SQL Prompt 中注入对应语言的字段说明。


## 四、实施计划

### 4.1 分阶段交付

| 阶段 | 周期 | 交付内容 | 验收标准 |
| :--- | :--- | :--- | :--- |
| **Phase 1：POC 验证** | 2 周 | 连接测试数据库，实现基础 Text-to-SQL + 静态 Schema 配置 | 能对单表执行简单查询（如"查询某产品产量"） |
| **Phase 2：安全与路由** | 2 周 | 安全控制层 + Database/RAG 路由机制 + 预定义模板 | 危险 SQL 被拦截；路由判断准确率 ≥ 80% |
| **Phase 3：生产接入** | 3 周 | 接入 SAP HANA / MES 生产数据库（只读副本）+ 数据脱敏 + 可观测性 | 能对真实业务数据执行多表联合查询；敏感字段自动脱敏 |
| **Phase 4：评估与优化** | 1 周 | 构建测试集 + 准确率评估 + 性能调优 + 多语言支持 | Text-to-SQL 准确率 ≥ 85%；P95 延迟 < 10s；支持繁简英三语 |

### 4.2 依赖与前置条件

| 依赖项 | 负责方 | 状态 |
| :--- | :--- | :--- |
| 生产数据库只读账户（SAP HANA / PostgreSQL） | IT 基础设施团队 | 待申请 |
| 数据库 Schema 文档（表结构说明） | 各业务系统负责人 | 待收集 |
| 测试数据集（50-100 个"问题-标准 SQL-预期答案"三元组） | 项目组 + 业务方 | 待构建 |
| 数据库网络连通性（Agent 服务 → 数据库） | 网络团队 | 待确认 |
| 敏感字段清单（手机号、身份证、银行账号等） | 信息安全团队 | 待确认 |
| 预定义查询模板清单（高频业务场景） | 业务方 + 项目组 | 待梳理 |


## 五、风险与应对

| 风险 | 影响 | 应对措施 |
| :--- | :--- | :--- |
| **Text-to-SQL 准确率不足** | 用户得到错误数据，信任度下降 | ① 构建高质量测试集持续迭代 ② 引入 SQL 自愈重试机制 ③ 对低置信度查询触发人工确认 ④ 高频场景优先使用预定义模板 |
| **SQL 注入/数据泄露** | 严重安全事故 | ① 只读账户（从源头禁止写操作） ② 关键词黑名单 ③ 行级权限隔离 ④ 全链路审计日志 ⑤ 敏感字段自动脱敏 |
| **数据库查询延迟过高** | 用户体验差 | ① 查询自动加 LIMIT ② 对高频查询使用 Redis 缓存 ③ 考虑使用只读从库分流 |
| **Schema 过大撑爆上下文** | Agent 无法正常工作 | 采用静态白名单 + 按需加载（仅注入相关表的 Schema） |
| **LLM 篡改数据库数字** | 用户得到错误数据 | 幻觉检测器进行数字忠实度校验，防止 LLM 在总结时篡改数字 |
| **数据库连接不稳定** | 查询失败率高 | 熔断器机制快速失败，避免阻塞整个查询流程 |


## 六、成功标准

1. **功能**：管理者能用自然语言查询产量、良率、库存、设备状态等核心 KPI
2. **安全**：所有查询均为只读，无任何数据篡改风险；敏感字段自动脱敏
3. **准确率**：Text-to-SQL 生成准确率 ≥ 85%（基于测试集）
4. **性能**：简单查询 P95 延迟 < 3s，复杂查询 P95 延迟 < 10s
5. **协同**：Database Tool 与 RAG Tool 能根据意图自动路由或串行协作
6. **多语言**：支持繁体中文、简体中文、英文三种语言的查询输入与结果输出
7. **可观测**：提供完整的 Prometheus 指标和 JSON 结构化审计日志


## 七、需求追溯表

本节建立 Agent/RAG Tool 端需求与文档实现章节的追溯关系，确保所有需求均已覆盖。

### 7.1 多 Tool 调度能力需求追溯

| 需求编号 | 需求描述 | 文档实现章节 | 实现状态 |
|---------|---------|------------|---------|
| **AGT-REQ-01** | 工具列表获取：Agent 启动时从 MCP 服务获取所有可用 DB Tool 的列表及其 name 和 description | 3.2.1 DatabaseQueryTool.initialize() | ✅ 已实现 |
| **AGT-REQ-02** | 按意图路由 Tool：Agent 必须根据用户问题和各 Tool 的 description，自主决策调用哪个 DB Tool | 3.2.1 DatabaseQueryTool._route_by_intent() | ✅ 已实现 |
| **AGT-REQ-03** | 禁止使用单一万能工具：Agent 的 System Prompt 中应约束其仅使用细粒度的业务域工具 | 3.1 整体架构（设计原则） | ✅ 已实现 |

### 7.2 按需分步注入 Schema 需求追溯

| 需求编号 | 需求描述 | 文档实现章节 | 实现状态 |
|---------|---------|------------|---------|
| **AGT-REQ-04** | 先探查，再写 SQL：Agent 在使用 query_{db_id} 执行查询前，必须先调用 list_tables_{db_id} 获取表名清单，再调用 describe_table_{db_id} 获取相关表的字段结构 | 3.2.3 渐进式 Schema 发现（三步递进流程） | ✅ 已实现 |
| **AGT-REQ-05** | 按关键词筛选表：Agent 在调用 list_tables_{db_id} 后，应根据用户问题中的关键词，从表名清单中筛选出最相关的 2-3 张表 | 3.2.1 DatabaseQueryTool._filter_relevant_tables() | ✅ 已实现 |
| **AGT-REQ-06** | 失败自愈：如果 Agent 第一次生成的 SQL 执行失败，Agent 应分析错误信息，重新调用 list_tables 或 describe_table 获取更准确的信息，生成修正后的 SQL 再次执行 | 3.3.4 SQL 自愈重试流程（V2.0 - 失败自愈 + 重新探查） | ✅ 已实现 |
| **AGT-REQ-07** | 限制探查频次：Agent 在单轮对话中，对同一数据库调用 list_tables 和 describe_table 的次数应有上限约束（如最多各调用 3 次） | 3.3.4 探查频次限制（ExploreCounter 类） | ✅ 已实现 |

### 7.3 需求覆盖度统计

| 需求类别 | 需求总数 | 已实现 | 未实现 | 覆盖率 |
|---------|---------|-------|-------|-------|
| 多 Tool 调度能力 | 3 | 3 | 0 | 100% |
| 按需分步注入 Schema | 4 | 4 | 0 | 100% |
| **总计** | **7** | **7** | **0** | **100%** |

### 7.4 与 MCP Server 端需求的协同关系

| Agent 端需求 | 依赖的 MCP 端需求 | 协同说明 |
|------------|----------------|---------|
| AGT-REQ-01（工具列表获取） | MCP-REQ-01（按业务域拆分 Tool）、MCP-REQ-03（动态 Tool 生成） | Agent 通过 MCP 协议的 list_tools 方法获取动态生成的 Tool 列表 |
| AGT-REQ-02（按意图路由 Tool） | MCP-REQ-02（Tool 描述仅包含意图触发词） | Agent 基于 Tool 的 description 进行意图匹配，description 不含物理存储细节 |
| AGT-REQ-04（先探查，再写 SQL） | MCP-REQ-05（list_tables 探查工具）、MCP-REQ-06（describe_table 探查工具）、MCP-REQ-08（Schema 信息不主动注入） | Agent 主动调用探查工具，MCP Server 被动响应，禁止主动推送 |
| AGT-REQ-05（按关键词筛选表） | MCP-REQ-05（list_tables 返回表名清单） | list_tables 返回的表名清单占用极少 Token，便于 Agent 筛选 |
| AGT-REQ-06（失败自愈） | MCP-REQ-07（Schema 信息缓存） | MCP Server 对 Schema 信息进行 TTL 缓存，避免重复查询数据库 |
| AGT-REQ-07（限制探查频次） | MCP-REQ-07（Schema 信息缓存） | 探查频次限制 + Schema 缓存双重保护，降低数据库压力 |

### 7.5 核心设计原则总结

1. **MCP 端**：负责暴露**细粒度、语义明确**的多个 Tool，并提供 **`list_tables` + `describe_table`** 的探查能力，但**绝不主动推送 Schema**。
2. **Agent 端**：负责**按用户意图路由 Tool**，并遵循 **"先探查表清单 → 再按需取表结构 → 最后生成 SQL"** 的三步递进流程，实现 Schema 的**按需、分步注入**。
3. **最终效果**：让模型始终在**最小的上下文窗口内**获得**最精准的决策信息**，既降本又增效。

---

*本文档 V2.0 版本基于"分多个 Tool + 按需分步注入 Schema"两大设计原则，完整覆盖了 Agent/RAG Tool 端的 7 项核心需求，并与 MCP Server 端设计文档形成协同。*


## 八、附录：优先接入的数据表清单

结合富士康实际业务，建议首批接入以下数据表：

| 业务领域 | 推荐接入的表 | 典型查询场景 |
| :--- | :--- | :--- |
| **生产管理** | `production_order`、`output_record`、`wip_tracking` | "Q3 各厂区产量是多少？" |
| **品质管理** | `iqc_inspection`、`ipqc_check`、`fqc_result` | "本月良率是多少？相比上月变化如何？" |
| **设备管理** | `equipment_status`、`oee_record`、`maintenance_log` | "A 产线设备 OEE 是多少？" |
| **库存管理** | `inventory_current`、`material_movement` | "SKU-1001 当前库存是多少？" |
| **供应链** | `purchase_order`、`supplier_score` | "主要供应商的交货准时率是多少？" |

---

*本需求文档对应之前讨论的 Agentic RAG 完整流程中"工具调用与混合检索"阶段的 Database Tool 接入部分。如有任何疑问或需要进一步细化某个模块，请随时沟通。*
