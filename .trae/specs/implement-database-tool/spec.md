# Database Tool 实现 Spec（Phase 1 POC）

## Why
当前 Agentic RAG 系统无法查询企业结构化数据库中的业务数据（产量、良率、库存等）。需要新增 Database Tool，让 Agent 能通过自然语言生成 SQL 并查询数据库，与现有 RAG Tool 协同实现混合问答。

## What Changes
- 新增 `DatabaseQueryTool`（`agent/tools/database_query.py`），继承 `ToolBase`，实现 NL-to-SQL + MCP Server 调用
- 新增 `DatabaseQueryToolParam` 参数定义类
- 新增数据库配置 YAML（`conf/db_schema.yaml`），定义可访问的数据库和表白名单
- 新增查询模板配置（`conf/query_templates.yaml`），支持高频场景模板匹配
- 复用现有 `MCPToolCallSession` 调用 MCP Server 执行 SQL
- 复用现有 `MCPServerService` 获取 MCP Server 连接信息
- 在 `agent/component/__init__.py` 注册新组件

## Impact
- Affected specs: 无（全新功能）
- Affected code:
  - `agent/tools/database_query.py`（新增）
  - `agent/component/__init__.py`（注册组件）
  - `conf/db_schema.yaml`（新增配置）
  - `conf/query_templates.yaml`（新增配置）

## ADDED Requirements

### Requirement: DatabaseQueryTool 组件
系统 SHALL 提供一个 `DatabaseQueryTool`，继承 `ToolBase`，接收用户自然语言查询，生成 SQL 并通过 MCP Server 执行。

#### Scenario: 成功执行简单查询
- **WHEN** 用户提问 "Q3 各厂区产量是多少？"
- **THEN** Tool 通过 MCP Server 调用 `query_{db_id}` 执行生成的 SQL，返回 Markdown 格式结果

#### Scenario: MCP Server 不可用
- **WHEN** MCP Server 连接失败
- **THEN** Tool 返回明确错误信息，不阻塞 Agent 其他流程

### Requirement: 意图路由到目标数据库
系统 SHALL 根据用户问题中的关键词，匹配目标数据库（通过 MCP Server Tool 的 description）。

#### Scenario: 关键词匹配成功
- **WHEN** 用户提问包含 "产量"，且 `query_mes_prod` 的 description 包含 "生产"
- **THEN** 路由到 `mes-prod` 数据库

#### Scenario: 无匹配数据库
- **WHEN** 用户问题无法匹配到任何数据库
- **THEN** 返回错误提示 "未找到匹配的数据库"

### Requirement: 渐进式 Schema 发现
系统 SHALL 在生成 SQL 前，先调用 `list_tables_{db_id}` 获取表名清单，再调用 `describe_table_{db_id}` 获取相关表字段结构。

#### Scenario: 三步递进获取 Schema
- **WHEN** 需要为 `mes-prod` 生成 SQL
- **THEN** 先调用 `list_tables_mes_prod` 获取表名 → 按关键词筛选 2-3 张表 → 调用 `describe_table_mes_prod` 获取字段详情

### Requirement: SQL 安全校验
系统 SHALL 在调用 MCP Server 前对生成的 SQL 进行只读校验。

#### Scenario: 拦截危险 SQL
- **WHEN** 生成的 SQL 包含 DROP/DELETE/UPDATE 等关键词
- **THEN** 拒绝执行，返回安全错误

### Requirement: 结果格式化
系统 SHALL 将数据库返回的原始数据转换为 Markdown 表格格式输出。

#### Scenario: 格式化查询结果
- **WHEN** MCP Server 返回查询结果 JSON
- **THEN** 转换为 Markdown 表格，设置到 `formalized_content` 输出

### Requirement: 组件注册
系统 SHALL 在 `agent/component/__init__.py` 中注册 `DatabaseQuery` 组件，使其可被 Agent 画布使用。

#### Scenario: 组件可被发现
- **WHEN** Agent 画布加载组件列表
- **THEN** 能找到 `DatabaseQuery` 组件并使用
