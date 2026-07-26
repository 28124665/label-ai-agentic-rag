# Database Tool 实现任务清单（Phase 1 POC）

## 任务 1：创建数据库配置文件
- [x] 1.1 创建 `conf/db_schema.yaml`，定义数据库连接配置和表白名单
- [x] 1.2 配置至少 2 个示例数据库（如 `mes-prod`、`erp-prod`）
- [x] 1.3 为每个数据库配置 3-5 张示例表及其字段定义

## 任务 2：创建查询模板配置文件
- [x] 2.1 创建 `conf/query_templates.yaml`，定义高频查询模板
- [x] 2.2 配置至少 3 个示例模板（产量查询、库存查询、良率查询）
- [x] 2.3 每个模板包含关键词、SQL 模板、参数定义

## 任务 3：实现 DatabaseQueryToolParam 类
- [x] 3.1 创建 `agent/tools/database_query.py`
- [x] 3.2 实现 `DatabaseQueryToolParam` 类，继承 `ToolParamBase`
- [x] 3.3 定义 meta 信息（name、description、parameters）
- [x] 3.4 实现 `check()` 方法校验必填参数

## 任务 4：实现 DatabaseQueryTool 核心类
- [x] 4.1 实现 `DatabaseQueryTool` 类，继承 `ToolBase`
- [x] 4.2 实现 `__init__()` 初始化 MCP 会话和配置加载器
- [x] 4.3 实现 `_load_db_config()` 加载数据库配置
- [x] 4.4 实现 `_load_query_templates()` 加载查询模板

## 任务 5：实现意图路由功能
- [x] 5.1 实现 `_route_by_intent()` 方法，根据关键词匹配目标数据库
- [x] 5.2 从 MCP Server 获取可用 Tool 列表
- [x] 5.3 匹配 Tool description 中的关键词
- [x] 5.4 返回目标数据库 ID

## 任务 6：实现渐进式 Schema 发现
- [x] 6.1 实现 `_progressive_schema_discovery()` 方法
- [x] 6.2 调用 `list_tables_{db_id}` 获取表名清单
- [x] 6.3 实现 `_filter_relevant_tables()` 按关键词筛选相关表
- [x] 6.4 调用 `describe_table_{db_id}` 获取字段详情

## 任务 7：实现 SQL 生成和校验
- [x] 7.1 实现 `_generate_sql()` 方法，调用 LLM 生成 SQL
- [x] 7.2 实现 `_validate_sql()` 方法，校验 SQL 只读性
- [x] 7.3 拦截 DROP/DELETE/UPDATE 等危险操作

## 任务 8：实现 MCP Server 调用
- [x] 8.1 实现 `_execute_sql_via_mcp()` 方法
- [x] 8.2 创建 `MCPToolCallSession` 连接 MCP Server
- [x] 8.3 调用 `query_{db_id}` Tool 执行 SQL
- [x] 8.4 处理超时和错误

## 任务 9：实现结果格式化
- [x] 9.1 实现 `_format_result()` 方法
- [x] 9.2 将 JSON 结果转换为 Markdown 表格
- [x] 9.3 设置 `formalized_content` 输出

## 任务 10：实现 _invoke 主流程
- [x] 10.1 实现 `_invoke()` 方法，串联所有步骤
- [x] 10.2 流程：意图路由 → 模板匹配/Schema 发现 → SQL 生成 → 校验 → 执行 → 格式化
- [x] 10.3 处理异常和错误返回

## 任务 3：注册组件
- [x] 3.1 验证 `agent/tools/__init__.py` 自动发现机制（已确认使用 `_import_submodules()` 自动导入）
- [x] 3.2 确认 `DatabaseQuery` 和 `DatabaseQueryParam` 会被自动注册（无需手动导入）

## 任务 12：编写单元测试
- [x] 12.1 测试意图路由功能
- [x] 12.2 测试 Schema 发现流程
- [x] 12.3 测试 SQL 校验逻辑
- [x] 12.4 测试结果格式化

## 任务 13：集成测试
- [ ] 13.1 启动本地 MCP Server（如果可用）
- [ ] 13.2 通过 Agent 画布调用 DatabaseQueryTool
- [ ] 13.3 验证端到端流程

## 依赖关系
- 任务 1、2 无依赖，可并行
- 任务 3 依赖任务 1
- 任务 4 依赖任务 3
- 任务 5、6、7、8、9 依赖任务 4，可并行开发
- 任务 10 依赖任务 5-9
- 任务 11 依赖任务 10
- 任务 12 依赖任务 10
- 任务 13 依赖任务 11、12
