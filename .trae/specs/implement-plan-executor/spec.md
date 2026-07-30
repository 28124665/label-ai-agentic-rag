# 计划执行器与并行调度 Spec

## Why

当前意图路由的第3层 Planner 已能生成带并行标记的 DAG 执行计划（`ExecutionPlan`），但存在两个断层：
1. **计划未被执行**：Planner 生成的计划仅存储在 `state["route_decision"].metadata["plan"]` 中，后续节点并未读取和使用
2. **不支持多工具并行**：现有 `db_tool_node` / `rag_tool_node` 是单实例调用，且 `AgentState` 中 `db_result`、`rag_docs` 都是单值字段，无法承载多个并行查询结果

需要实现一个计划执行器，支持并行调度多个工具（如多个不同数据库 + RAG 并行），并将结果聚合后融入现有流程。

## What Changes

- **新增** `StepArgs` 结构化参数模型，支持指定 `db_id`、`kb_ids` 等工具实例参数
- **新增** `ToolResult` TypedDict，存储单个工具执行结果
- **扩展** `AgentState`，新增 `execution_plan`、`tool_results`、`plan_execution_status` 字段
- **扩展** `PlanStep`，`args` 从 `dict` 升级为结构化 `StepArgs`
- **新增** `agent/langgraph/executor/` 目录，包含 DAG 调度器、工具分发器、结果聚合器
- **新增** `plan_executor_node` 节点，作为复杂任务的统一执行入口
- **修改** `route_decision()`，新增 `"complex"` → `"plan_executor"` 分支
- **修改** `graph.py`，注册 `plan_executor` 节点和边
- **修改** `prompt_assembly_node`，支持从 `tool_results` 读取多源结果并标注来源
- **更新** `planner_v1.txt` Prompt 模板，输出规范化 `args`

## Impact

- Affected specs: `upgrade-intent-router`（意图路由升级）
- Affected code:
  - `agent/langgraph/routers/models.py`（扩展 PlanStep）
  - `agent/langgraph/state.py`（扩展 AgentState）
  - `agent/langgraph/executor/`（新增目录）
  - `agent/langgraph/nodes/plan_executor.py`（新增节点）
  - `agent/langgraph/nodes/intent_router.py`（修改 route_decision）
  - `agent/langgraph/nodes/prompt_assembly.py`（支持多源结果）
  - `agent/langgraph/graph.py`（注册新节点）
  - `config/prompts/planner_v1.txt`（Prompt 升级）

## ADDED Requirements

### Requirement: DAG 调度器
系统 SHALL 提供基于拓扑排序的 DAG 调度器，将执行计划按依赖关系分层，同层步骤并行执行，不同层串行执行。

#### Scenario: 全并行场景
- **WHEN** 所有步骤的 `depends_on` 为空
- **THEN** 所有步骤在同一层，通过 `asyncio.gather` 并行执行

#### Scenario: 串行依赖场景
- **WHEN** step2 依赖 step1
- **THEN** step1 在 Layer 0 执行完成后，step2 才在 Layer 1 执行

#### Scenario: 循环依赖检测
- **WHEN** 步骤间存在循环依赖
- **THEN** 抛出 `ValueError`，避免死锁

### Requirement: 工具分发器
系统 SHALL 提供工具分发器，根据 `PlanStep.tool` 调用对应工具，复用现有 `RAGTool/DatabaseTool/WebTool` 的 `invoke()` 方法。

#### Scenario: 多数据库并行查询
- **WHEN** step1.args.db_id="hr_system"，step2.args.db_id="supply_chain"
- **THEN** 两个步骤分别查询不同的数据库实例，结果各自独立

#### Scenario: 步骤参数优先
- **WHEN** step.args.query 非空
- **THEN** 使用 step.args.query 作为查询语句，而非 state.user_question

### Requirement: 结果聚合器
系统 SHALL 提供结果聚合器，将多个工具执行结果合并为统一格式，兼容现有 `quality_check` 和 `prompt_assembly` 的字段读取方式。

#### Scenario: 多 DB 结果合并
- **WHEN** 两个 DB 步骤都返回 rows
- **THEN** 合并所有 rows 到 `db_result.rows`，每行标注 `_source_db` 字段

#### Scenario: 失败步骤隔离
- **WHEN** 部分步骤 success=False
- **THEN** 跳过失败步骤，只聚合成功步骤的结果

### Requirement: 计划执行器节点
系统 SHALL 新增 `plan_executor` 节点，只有 `source="planner"` 且 `complexity="complex"` 的任务才进入此节点。

#### Scenario: 正常执行
- **WHEN** route_decision.metadata 中存在 plan
- **THEN** 执行 DAG 调度，聚合结果，同步写入兼容字段

#### Scenario: 降级处理
- **WHEN** plan 不存在或解析失败
- **THEN** 降级为简单路由，不阻塞流程

## MODIFIED Requirements

### Requirement: 路由分发
`route_decision()` 函数新增 `"complex"` 分支判断：当 `route_decision.source == "planner"` 且 `route_decision.complexity == "complex"` 时，路由到 `plan_executor` 节点。

### Requirement: Prompt 组装
`prompt_assembly_node` 新增对 `tool_results` 的读取支持，当检测到多 DB 结果时，为每行标注来源数据库。

## REMOVED Requirements

无删除项。
