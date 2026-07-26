# Database Tool 实现检查清单

## 配置文件检查
- [x] `conf/db_schema.yaml` 存在且格式正确
- [x] 配置了至少 2 个数据库（mes-prod、erp-prod）
- [x] 每个数据库配置了 3-5 张表及字段定义
- [x] `conf/query_templates.yaml` 存在且格式正确
- [x] 配置了至少 3 个查询模板（产量、库存、良率）

## 代码实现检查
- [x] `agent/tools/database_query.py` 文件存在
- [x] `DatabaseQueryToolParam` 类正确继承 `ToolParamBase`
- [x] `DatabaseQueryToolParam.meta` 定义了 name、description、parameters
- [x] `DatabaseQueryToolParam.check()` 方法校验必填参数
- [x] `DatabaseQueryTool` 类正确继承 `ToolBase`
- [x] `DatabaseQueryTool.__init__()` 初始化 MCP 会话和配置
- [x] `_load_db_config()` 正确加载数据库配置
- [x] `_load_query_templates()` 正确加载查询模板

## 核心功能检查
- [x] `_route_by_intent()` 能根据关键词匹配目标数据库
- [x] 从 MCP Server 获取 Tool 列表功能正常
- [x] `_progressive_schema_discovery()` 实现三步递进流程
- [x] `list_tables_{db_id}` 调用正确
- [x] `_filter_relevant_tables()` 按关键词筛选相关表
- [x] `describe_table_{db_id}` 调用正确
- [x] `_generate_sql()` 调用 LLM 生成 SQL
- [x] `_validate_sql()` 校验 SQL 只读性
- [x] 拦截 DROP/DELETE/UPDATE 等危险操作
- [x] `_execute_sql_via_mcp()` 创建 MCPToolCallSession
- [x] 调用 `query_{db_id}` Tool 执行 SQL
- [x] 处理超时和错误
- [x] `_format_result()` 将 JSON 转换为 Markdown 表格
- [x] 设置 `formalized_content` 输出

## 主流程检查
- [x] `_invoke()` 方法串联所有步骤
- [x] 流程：意图路由 → 模板匹配/Schema 发现 → SQL 生成 → 校验 → 执行 → 格式化
- [x] 异常处理和错误返回正确

## 组件注册检查
- [x] `agent/tools/__init__.py` 使用自动发现机制（`_import_submodules()`）
- [x] `DatabaseQuery` 和 `DatabaseQueryParam` 会被自动导入和注册
- [ ] 组件可被 Agent 画布发现和使用（需运行时验证）

## 测试检查
- [x] 单元测试覆盖意图路由功能
- [x] 单元测试覆盖 Schema 发现流程
- [x] 单元测试覆盖 SQL 校验逻辑
- [x] 单元测试覆盖结果格式化
- [ ] 集成测试通过（如有 MCP Server 环境）

## 文档检查
- [x] 代码注释完整（中文注释说明关键逻辑）
- [x] 关键方法有 docstring
- [x] 配置文件有示例和说明
