# 实现受限 ReAct 子图 Spec

## Why
当前 LangGraph 主干为预定义 DAG（question_input → intent_router → rag/db/hybrid → reflection → quality_check → prompt_assembly → llm_generate → hallucination → final_answer），对需要多轮工具调用、根据中间结果决定下一步行动的复杂业务分析、归因推理、报表生成等生产级场景支持不足。
本设计在保留 LangGraph 主干稳定治理（租户、权限、预算、可观测性、错误降级）的前提下，新增**受限 ReAct 子图**，让 ReAct 只处理复杂任务，所有工具调用受策略、预算和权限约束。

## What Changes
- **新增** ReAct 子图模块（state、graph、planner、policy、budget、executor、normalizer、models）
- **新增** Evidence 标准化模块（models、fusion、answerability、verification）
- **新增** Policy Guard 组件，在 ReAct 子图执行工具前进行策略校验
- **新增** Budget Controller，控制 ReAct 子图的多维度预算（步骤、调用、token、耗时）
- **扩展** AgentState，增加 react_* 和 evidence 相关字段
- **扩展** intent_router，复杂任务路由到 react_subgraph
- **修改** graph.py 主干图，新增 react_subgraph 节点
- **新增** ReAct Prompt 模板（react_step_v1.md）
- **不破坏** 现有简单流程（chitchat / 单 RAG / 单 DB / Plan Executor），通过配置开关控制是否启用

## Impact
- Affected specs:
  - `reflect-and-clarify`（已完成）— reflection 节点保持不变，可作为 ReAct 子图内部的 step 使用
  - `implement-plan-executor`（已完成）— Plan Executor 仍处理"有明确依赖的并行任务"，ReAct 子图处理"需根据中间结果动态决策的任务"，两者互补
- Affected code:
  - `agent/langgraph/state.py` — 新增 react_* / evidence 字段
  - `agent/langgraph/graph.py` — 新增 react_subgraph 节点和边
  - `agent/langgraph/nodes/intent_router.py` — route_decision 增加 react_subgraph 分支
  - `agent/langgraph/react/` — 新增目录
  - `agent/langgraph/evidence/` — 新增目录
  - `api/utils/react.py` — 新增共享工具
  - `rag/prompts/react_step_v1.md` — 新增 Prompt 模板

## ADDED Requirements

### Requirement: 复杂任务路由到 ReAct 子图
The system SHALL 在 intent_router 判断任务复杂度为 `complex` 且需要多步动态推理时，路由到 `react_subgraph` 节点，而不是直接走简单 RAG/DB 路径。

#### Scenario: 简单任务不进入 ReAct
- **WHEN** 用户问题为"XX 是什么意思？"或单表统计
- **THEN** intent_router 路由到 `rag_tool` / `db_tool` / `plan_executor`，不进入 ReAct 子图

#### Scenario: 复杂任务进入 ReAct
- **WHEN** LLM 路由判断 `complexity=complex` 且 Planner 标记 `route_target=react`
- **THEN** intent_router 路由到 `react_subgraph`，子图开始多步推理

#### Scenario: Planner 标识为 react 时进入 ReAct
- **WHEN** intent_router 第三层 Planner 输出 `route_decision.source="react_planner"`
- **THEN** 路由进入 `react_subgraph`

### Requirement: ReAct 子图受限执行
The system SHALL 在 ReAct 子图内部强制实施多维度预算控制和工具白名单，超出预算时立即终止并返回部分结果或保守结论。

#### Scenario: 超过最大步骤数
- **WHEN** ReAct 推理达到 `max_steps`（默认 8）
- **THEN** 子图立即终止，生成部分结果返回主干

#### Scenario: 超过工具调用次数
- **WHEN** 工具累计调用次数达到 `max_tool_calls`（默认 6）
- **THEN** 子图立即终止

#### Scenario: 超过最大耗时
- **WHEN** 子图执行耗时达到 `max_latency_ms`（默认 30000ms）
- **THEN** 子图立即终止并返回已有 Evidence

### Requirement: 所有工具调用经过 Policy Guard
The system SHALL 在 ReAct 子图每次执行工具前，调用 Policy Guard 校验工具是否在白名单、参数是否安全、用户是否有权限。

#### Scenario: 工具在白名单且用户有权限
- **WHEN** LLM 决定调用 `db_query` 且 `db_query` 在 `allowed_tools` 中
- **THEN** Policy Guard 返回 `allow`，执行工具

#### Scenario: 工具不在白名单
- **WHEN** LLM 决定调用未在 `allowed_tools` 的工具
- **THEN** Policy Guard 返回 `deny`，记录拒绝原因并继续下一步

#### Scenario: DB 越权访问
- **WHEN** LLM 生成的 SQL 包含未授权表
- **THEN** Policy Guard 返回 `deny`，记录 `INVALID_SQL` 失败

### Requirement: 工具结果标准化为 Evidence
The system SHALL 将 RAG/DB/Web 工具的原始结果统一转换为 `Evidence` 数据结构，附带 source_type、confidence、source_uri 等元数据。

#### Scenario: RAG 结果标准化
- **WHEN** RAG Tool 返回检索结果
- **THEN** 系统将其转换为 `source_type="rag"` 的 Evidence，包含 `kb_id`、`chunk_id`、`source_uri`

#### Scenario: DB 结果标准化
- **WHEN** DB Tool 返回查询结果
- **THEN** 系统将其转换为 `source_type="db"` 的 Evidence，包含 `sql`、`tables`、`row_count` 等 metadata

#### Scenario: Web 结果标准化
- **WHEN** Web Tool 返回搜索结果
- **THEN** 系统将其转换为 `source_type="web"` 的 Evidence，包含 `url`、`title`、`fetched_at`

### Requirement: ReAct Step 输出结构化 JSON
The system SHALL 强制 ReAct 每一步 LLM 输出 JSON 格式（含 `thought_summary`、`action`、`stop`），禁止自由文本动作。

#### Scenario: 正常 step
- **WHEN** ReAct 进入 reason step
- **THEN** LLM 输出 `{"thought_summary": "...", "action": {...}, "stop": false}`，解析后执行

#### Scenario: 解析失败降级
- **WHEN** LLM 输出不符合 JSON 协议
- **THEN** 记录失败并视为 `finish`，子图退出

### Requirement: ReAct 子图不直接返回最终答案
The system SHALL 强制 ReAct 子图只输出 `ReactExecutionResult`（含 evidence 列表、步骤摘要、建议），由 LangGraph 主干的 prompt_assembly + llm_generate 生成最终答案。

#### Scenario: 子图结束后主干接管
- **WHEN** ReAct 子图 finish
- **THEN** 主干读取 `react_execution_result` 中的 evidence，进入 evidence_fusion → answerability_check → prompt_assembly

### Requirement: 主干优雅降级
The System SHALL 当 ReAct 子图不可用或被禁用时，intent_router 退化为直接走 RAG/DB 路径，不影响现有简单流程。

#### Scenario: 关闭 ReAct 子图
- **WHEN** 配置中 `react.enabled=false`
- **THEN** intent_router 不路由到 `react_subgraph`，行为与之前完全一致

#### Scenario: ReAct 子图执行失败
- **WHEN** ReAct 子图因异常中断
- **THEN** 主干捕获异常，路由到现有 RAG/DB fallback，不阻塞用户

## MODIFIED Requirements

### Requirement: AgentState 扩展
现有 AgentState 需新增以下字段：
- `react_state: Optional[dict]` — ReAct 子图内部状态（预算、action_history、observations）
- `react_execution_result: Optional[dict]` — ReAct 子图最终输出
- `evidence: list[dict]` — 标准化后的证据列表
- `evidence_fusion_result: Optional[dict]` — 证据融合结果
- `answerability_result: Optional[dict]` — 答案充分性判断结果

保持现有所有字段不变。

### Requirement: intent_router 扩展
现有 `intent_router_node` 保持不变（已支持 complex 任务路由到 plan_executor），但 `route_decision` 需新增：
- 当 `route_decision.source == "react_planner"` 时，路由到 `react_subgraph`

### Requirement: graph.py 扩展
现有 graph.py 新增：
- 节点 `react_subgraph`（封装 ReAct 子图为单一节点）
- 边：`react_subgraph` → `evidence_fusion` → `quality_check`
- 边：`intent_router` → `react_subgraph`（条件路由）

## REMOVED Requirements
无删除的现有功能。

## 配置项

```yaml
agent:
  react:
    enabled: true              # 是否启用 ReAct 子图
    max_steps: 8
    max_tool_calls: 6
    max_db_queries: 3
    max_llm_calls: 5
    max_latency_ms: 30000
    token_budget: 12000
    allowed_tools:
      - rag_search
      - db_query
      - web_search
    policy:
      db_query:
        readonly: true
        max_rows: 500
        timeout_seconds: 10
        require_ast_validation: true
      web_search:
        max_results: 5
        allow_private_ip: false
```

## 关键约束（来自设计文档第 16 节）

1. ReAct 不作为默认路径，只处理复杂任务
2. ReAct 不直接返回最终答案，只返回证据、步骤和建议
3. 所有工具调用必须经过 Policy Guard
4. ReAct 必须有最大步骤和预算
5. 每一步必须可观测、可审计
6. 工具 Observation 必须截断和脱敏
7. 所有证据必须标准化为 Evidence
8. 报表生成（阶段四）不在本次实现范围内
