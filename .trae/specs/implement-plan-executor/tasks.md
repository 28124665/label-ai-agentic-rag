# Tasks

- [x] Task 1: 扩展数据结构模型
  - [x] 1.1 在 `agent/langgraph/routers/models.py` 中新增 `StepArgs` 模型（query, kb_ids, db_id, mcp_server_name, search_engine, extra）
  - [x] 1.2 修改 `PlanStep`，将 `args: dict` 改为 `args: StepArgs`，新增 `description` 字段
  - [x] 1.3 在 `agent/langgraph/state.py` 中新增 `ToolResult` TypedDict
  - [x] 1.4 在 `AgentState` 中新增 `execution_plan`、`tool_results`、`plan_execution_status` 字段

- [x] Task 2: 实现 DAG 调度器
  - [x] 2.1 创建 `agent/langgraph/executor/__init__.py`
  - [x] 2.2 创建 `agent/langgraph/executor/dag_scheduler.py`，实现 `DAGScheduler` 类
  - [x] 2.3 实现 `_topological_layers()` 拓扑分层算法（Kahn 算法变体）
  - [x] 2.4 实现 `execute()` 方法，同层 `asyncio.gather` 并行执行
  - [x] 2.5 实现循环依赖检测，抛出 `ValueError`

- [x] Task 3: 实现工具分发器
  - [x] 3.1 创建 `agent/langgraph/executor/tool_dispatcher.py`，实现 `ToolDispatcher` 类
  - [x] 3.2 实现 `_execute_rag()`，复用 `get_rag_tool()`，步骤参数优先于 state
  - [x] 3.3 实现 `_execute_database()`，复用 `get_database_tool()`，支持 `step.args.db_id` 指定数据库实例
  - [x] 3.4 实现 `_execute_web()`，复用 `get_web_tool()`
  - [x] 3.5 实现异常捕获，失败返回 `success=False` 的 `ToolResult`

- [x] Task 4: 实现结果聚合器
  - [x] 4.1 创建 `agent/langgraph/executor/result_aggregator.py`，实现 `ResultAggregator` 类
  - [x] 4.2 实现 RAG 结果合并（按 score 降序）
  - [x] 4.3 实现 DB 结果合并（多库 rows 合并，标注 `_source_db`）
  - [x] 4.4 实现 Web 结果合并
  - [x] 4.5 实现质量评分聚合（取最高分）
  - [x] 4.6 实现失败步骤跳过逻辑

- [x] Task 5: 实现计划执行器节点
  - [x] 5.1 创建 `agent/langgraph/nodes/plan_executor.py`，实现 `plan_executor_node`
  - [x] 5.2 从 `route_decision.metadata["plan"]` 读取执行计划
  - [x] 5.3 调用 `DAGScheduler.execute()` 执行计划
  - [x] 5.4 调用 `ResultAggregator.aggregate()` 聚合结果
  - [x] 5.5 同步写入兼容字段（rag_docs, db_result, web_docs 等）
  - [x] 5.6 实现降级处理：plan 不存在时返回空结果，不阻塞流程

- [x] Task 6: 修改路由分发和图结构
  - [x] 6.1 修改 `agent/langgraph/nodes/intent_router.py` 的 `route_decision()`，新增 `"plan_executor"` 分支
  - [x] 6.2 修改 `agent/langgraph/graph.py`，注册 `plan_executor` 节点
  - [x] 6.3 在 `graph.py` 中添加 `plan_executor → quality_check` 边
  - [x] 6.4 在 `graph.py` 的条件路由中新增 `"plan_executor"` 映射

- [x] Task 7: 修改 Prompt 组装节点
  - [x] 7.1 修改 `agent/langgraph/nodes/prompt_assembly.py`，检测 `tool_results` 中是否有聚合的 DB 结果
  - [x] 7.2 修改 `_format_db_context()`，当 `is_aggregated=True` 时为每行标注来源数据库

- [x] Task 8: 更新 Planner Prompt 模板
  - [x] 8.1 更新 `config/prompts/planner_v1.txt`，输出规范化 `args`（含 db_id、kb_ids）
  - [x] 8.2 更新 `agent/langgraph/routers/planner.py`，解析新版 args 为 `StepArgs` 对象

- [x] Task 9: 编写单元测试
  - [x] 9.1 创建 `test/test_plan_executor.py`
  - [x] 9.2 测试 DAG 调度器：拓扑分层、并行执行、循环依赖检测
  - [x] 9.3 测试工具分发器：多 DB 并行、步骤参数优先、异常处理
  - [x] 9.4 测试结果聚合器：多源合并、失败步骤隔离
  - [x] 9.5 测试计划执行器节点：正常执行、降级处理

# Task Dependencies
- Task 2, 3, 4 依赖 Task 1（需要 StepArgs 和 ToolResult 数据结构）
- Task 5 依赖 Task 2, 3, 4（需要调度器、分发器、聚合器）
- Task 6 依赖 Task 5（需要 plan_executor_node 存在才能注册）
- Task 7 依赖 Task 1（需要 ToolResult 定义）
- Task 8 依赖 Task 1（需要 StepArgs 定义）
- Task 9 依赖 Task 1-8（需要所有功能实现完成）
