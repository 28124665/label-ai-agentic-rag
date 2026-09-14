# ReAct 子图接入主图改造设计文档

> 版本: v1.0 | 日期: 2026-08-12 | 作者: Agent

---

## 1. 背景与目标

### 1.1 现状

当前主图（[graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py)）的拓扑如下：

```
question_input → intent_router → [clarification|rag_tool|db_tool|plan_executor|prompt_assembly]
  ├── rag_tool → [db_tool (hybrid串行) | evidence_fusion]
  ├── db_tool → evidence_fusion
  ├── plan_executor → evidence_fusion
  └── prompt_assembly → ...
evidence_fusion → reflection → quality_check → [prompt_assembly|retry_rag|retry_db|web_tool|fallback]
  → prompt_assembly → llm_generate → hallucination → [answer_renderer|observability|prompt_assembly]
  → observability → answer_output → END
```

**核心问题**：意图路由是一次性决策，选定了 `db_tool` 就一条路走到底，无法在 DB 查询发现异常后动态切换到 RAG 检索补充信息。`plan_executor` 虽然支持 DAG 并行编排 DB+RAG，但它是"上游静态规划"，无法根据中间结果动态调整策略。

### 1.2 目标

将已实现的 ReAct 子图（[react/graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/react/graph.py)）接入主图，实现以下能力：

1. **DB → RAG 动态切换**：DB 查询发现异常后，LLM 自主决定是否调用 RAG 检索补充
2. **多轮交替推理**：支持 `rag_search ↔ db_query ↔ web_search` 任意顺序交替
3. **安全约束**：Policy Guard 校验每步工具调用，Budget Controller 限制步数/时间/Token
4. **能力开关**：通过 `agent_config.react.enabled` 控制是否启用，与会话级开关对齐
5. **向后兼容**：不破坏现有 `plan_executor` 路径，ReAct 作为可选增强路径

### 1.3 典型场景

| 用户问题 | 当前行为 | 改造后行为 |
|---------|---------|-----------|
| "查上个月订单总数" | intent_router → db_tool → 一次 SQL → answer | 不变（简单查询不触发 ReAct） |
| "分析近三年营收趋势，找出异常并解释原因" | intent_router → plan_executor → DAG 并行查 DB+RAG → answer | intent_router → react_subgraph → 多轮交替查询 → answer |
| "产品线 A 退货率为什么这么高" | intent_router → db_tool → 一次 SQL → answer（缺少根因分析） | intent_router → react_subgraph → SQL 查退货数据 → RAG 搜投诉 → SQL 验证 → 结论 |

---

## 2. 现有代码资产盘点

### 2.1 已实现、可直接使用的模块

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| ReactSubgraph 主循环 | [react/graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/react/graph.py) | ✅ 已完成 | 完整的 Think→Act→Observe 循环，含 LoopGuard 熔断 |
| 主图节点入口 | [nodes/react_subgraph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/react_subgraph.py) | ✅ 已完成 | `react_subgraph_node()` 函数，含 LLM 解析、降级兜底 |
| Policy Guard | [react/policy.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/react/policy.py) | ✅ 已完成 | 工具白名单 + 安全校验（SQL 注入检测、URL 注入检测等） |
| Budget Controller | [react/budget.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/react/budget.py) | ✅ 已完成 | 多维度预算（步数/工具调用/Token/延迟） |
| Tool Executor | [react/executor.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/react/executor.py) | ✅ 已完成 | 调度 `rag_search` / `db_query` / `web_search` |
| LLM 推理步骤 | [react/step.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/react/step.py) | ✅ 已完成 | 提示词渲染 + JSON 解析 |
| 数据模型 | [react/models.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/react/models.py) | ✅ 已完成 | ReactState, ReactAction, ReactExecutionResult 等 |
| 能力开关 | [skills/hard_filter.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/skills/hard_filter.py#L301) | ✅ 已完成 | `react_enabled` 从 `agent_config.react.enabled` 读取 |

### 2.2 需要修改的文件

| 文件 | 改动量 | 改动类型 |
|------|--------|---------|
| [graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py) | ~30 行 | 新增节点 + 边 + 条件路由 |
| [nodes/intent_router.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/intent_router.py) | ~60 行 | route_decision 新增路由目标 + intent_router_node 新增 ReAct 判定逻辑 |
| [state.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/state.py) | ~5 行 | 新增 react_enabled 字段（若尚未定义） |

### 2.3 不需要修改的文件

- `react/graph.py`、`react/policy.py`、`react/budget.py`、`react/executor.py`、`react/step.py`、`react/models.py`：无需改动
- `nodes/react_subgraph.py`：无需改动，直接使用
- `nodes/evidence_fusion.py`、`nodes/reflection.py`、`nodes/quality_check.py`：无需改动，ReAct 子图独立产出 evidence，走现有融合链路

---

## 3. 详细设计

### 3.1 改造后的主图拓扑

```
question_input → intent_router → [clarification|rag_tool|db_tool|plan_executor|react_subgraph|prompt_assembly]
  ├── clarification → intent_router
  ├── rag_tool → [db_tool (hybrid) | evidence_fusion]
  ├── db_tool → evidence_fusion
  ├── plan_executor → evidence_fusion
  ├── react_subgraph → evidence_fusion          ← ★ 新增路径
  └── prompt_assembly → ...
evidence_fusion → reflection → quality_check → [prompt_assembly|retry_rag|retry_db|web_tool|fallback]
  → prompt_assembly → llm_generate → hallucination → [answer_renderer|observability|prompt_assembly]
  → fallback → observability
  → answer_renderer → observability → answer_output → END
```

**关键设计原则**：ReAct 子图作为 `plan_executor` 的替代路径，同样是"工具 → evidence_fusion → 后续流程"，完全复用现有的 evidence 融合、reflection、quality_check 链路。

### 3.2 路由决策逻辑

#### 3.2.1 触发条件

ReAct 子图在以下条件**同时满足**时触发：

| 条件 | 来源 | 说明 |
|------|------|------|
| `react_enabled == True` | `agent_config.react.enabled` | 会话级能力开关，默认关闭 |
| `complexity == "complex"` | LLM Router 判定 | 问题复杂度为"复杂" |
| `needs_multi_tool == True` | LLM Router 判定 | 需要多工具协同（DB+RAG 或 DB+Web） |
| `route_target in ("hybrid", "database")` | 意图路由 | 问题涉及数据库查询 |

**不触发 ReAct 的场景**：

- 简单寒暄（`route_target == "chitchat"`）→ 走 `prompt_assembly`
- 纯 RAG 查询（`route_target == "rag"`）→ 走 `rag_tool`
- 中等复杂度但无需多工具（`complexity == "medium"`）→ 走原有 `db_tool` 或 `rag_tool`
- `react_enabled == False` → 走原有 `plan_executor` 路径

#### 3.2.2 路由优先级

```python
# route_decision() 中的优先级（从上到下）：
1. needs_clarification → clarification     # 需要用户澄清
2. react_enabled + complex + needs_multi_tool → react_subgraph  # ★ 新增
3. planner + complex → plan_executor        # 原有 Planner 路径
4. route_target → rag_tool / db_tool / prompt_assembly  # 原有路径
```

**关键设计决策**：ReAct 优先级高于 `plan_executor`。当 ReAct 启用时，复杂多工具任务优先走 ReAct（动态推理），`plan_executor` 降级为 ReAct 不可用时的备选（静态 DAG 规划）。

#### 3.2.3 `needs_multi_tool` 判定逻辑

在意图路由 LLM 推理时，新增一个判定维度：

```
用户问题："分析近三年营收趋势，找出异常并解释原因"

LLM 推理输出：
{
  "route_target": "hybrid",
  "complexity": "complex",
  "needs_multi_tool": true,    ← ★ 新增字段
  "reasoning": "需要查数据库获取营收数据，同时需要检索知识库查找异常原因解释"
}
```

判定规则：
- `needs_multi_tool = True`：问题需要 2 种及以上工具类型协同（DB+RAG、DB+Web、RAG+Web）
- `needs_multi_tool = False`：单一工具即可完成

### 3.3 代码改动详情

#### 3.3.1 graph.py — 新增节点和边

```python
# 在 build_agent_graph() 中新增：

# ★ 导入 react_subgraph_node
from agent.langgraph.nodes.react_subgraph import react_subgraph_node

# ★ 新增 react_subgraph 节点
graph.add_node("react_subgraph", react_subgraph_node)

# ★ intent_router 条件路由新增 react_subgraph 目标
graph.add_conditional_edges(
    "intent_router",
    route_decision,
    {
        "clarification": "clarification",
        "rag_tool": "rag_tool",
        "db_tool": "db_tool",
        "plan_executor": "plan_executor",
        "react_subgraph": "react_subgraph",    # ★ 新增
        "prompt_assembly": "prompt_assembly",
    },
)

# ★ react_subgraph → evidence_fusion（与其他工具路径一致）
graph.add_edge("react_subgraph", "evidence_fusion")
```

#### 3.3.2 intent_router.py — route_decision 新增路由目标

```python
def route_decision(state: AgentState) -> str:
    # ... 现有逻辑 ...

    route_target = state.get("route_target", "chitchat")
    route_decision_data = state.get("route_decision")

    # 1. 优先检查是否需要用户澄清
    if route_decision_data and route_decision_data.metadata \
            and route_decision_data.metadata.get("needs_clarification"):
        return "clarification"

    # ★ 2. 新增：ReAct 子图路由（优先级高于 plan_executor）
    if state.get("react_enabled", False) \
            and route_decision_data \
            and route_decision_data.complexity == "complex" \
            and route_decision_data.metadata \
            and route_decision_data.metadata.get("needs_multi_tool") \
            and route_target in ("hybrid", "database"):
        _record_route_metric("react_subgraph")
        return "react_subgraph"

    # 3. Planner 复杂任务（ReAct 未启用时的备选路径）
    if route_decision_data and route_decision_data.source == "planner" \
            and route_decision_data.complexity == "complex":
        _record_route_metric("plan_executor")
        return "plan_executor"

    # 4. 原有路由逻辑
    if route_target == "rag":
        return "rag_tool"
    elif route_target == "database":
        return "db_tool"
    elif route_target == "hybrid":
        return "rag_tool"  # hybrid 时先走 rag_tool，再由 after_rag_tool 串行
    else:
        return "prompt_assembly"
```

#### 3.3.3 intent_router.py — intent_router_node 新增 needs_multi_tool 判定

在 LLM Router（第 2 层）的推理 prompt 中增加 `needs_multi_tool` 判定：

```python
# 在 LLM 推理的 system prompt 中新增：
"""
你需要输出以下 JSON 格式：
{
  "route_target": "rag|database|hybrid|chitchat",
  "complexity": "simple|medium|complex",
  "needs_multi_tool": true|false,   ← ★ 新增
  "confidence": 0.0-1.0,
  "reasoning": "..."
}

needs_multi_tool 判定规则：
- true：问题需要 2 种及以上工具类型协同工作
  例如："分析营收趋势并解释原因"（需要 DB + RAG）
  例如："查一下最近的订单，然后搜一下有没有相关投诉"（需要 DB + RAG）
- false：单一工具即可完成
  例如："查上个月订单总数"（仅 DB）
  例如："公司考勤制度是什么"（仅 RAG）
"""
```

#### 3.3.4 state.py — 新增 react_enabled 字段（如需要）

检查 `AgentState` 是否已有 `react_enabled` 字段。若没有，新增：

```python
# 在 AgentState TypedDict 中新增：
react_enabled: bool  # 是否启用 ReAct 子图（从 agent_config.react.enabled 读取）
```

#### 3.3.5 能力开关注入

在 `user_question_node` 或 `agent_config` 解析阶段，将 `react_enabled` 注入 state：

```python
# 在 user_question_node 中（或等效的初始化节点）：
react_config = agent_config.get("react", {}) or {}
react_enabled = react_config.get("enabled", False) if isinstance(react_config, dict) else False

return {
    "react_enabled": react_enabled,
    # ... 其他字段 ...
}
```

---

## 4. ReAct 子图与主图的交互协议

### 4.1 输入（主图 → ReAct 子图）

`react_subgraph_node` 从 `AgentState` 读取以下字段：

| 字段 | 来源 | 必填 | 说明 |
|------|------|------|------|
| `user_question` | state | ✅ | 用户原始问题 |
| `tenant_id` | state | ✅ | 租户 ID |
| `user_id` | state | ✅ | 用户 ID |
| `llm_id` | state | ✅ | LLM 模型 ID |
| `kb_ids` | state | ✅ | 可用知识库 ID 列表 |
| `db_id` | state | ✅ | 数据库 ID |
| `mcp_server_name` | state | ✅ | MCP 服务名 |
| `query_lang` | state | - | 查询语言（默认 zh_CN） |
| `agent_config` | state | ✅ | agent 完整配置（含 react 子配置） |
| `react_enabled` | state | ✅ | 能力开关（防御深度检查） |
| `skill_set` | state | - | 已解析的 Skill 集合 |

### 4.2 输出（ReAct 子图 → 主图）

`react_subgraph_node` 写回以下字段到 `AgentState`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `react_execution_result` | dict | 完整执行结果（success, evidence, step_count, finish_reason） |
| `evidence` | list[dict] | 合并后的 evidence 列表（已去重，合并了主图原有 evidence） |
| `termination_reason` | str | 终止原因（budget_exhausted / same_action_loop / rerank_declining / policy_denied / ""） |
| `termination_source` | str | 终止来源（budget / loop_guard / policy_guard / ""） |
| `loop_guard` | dict | LoopGuard 状态（供主图观测） |
| `rerank_score_history` | list[float] | Rerank 分数历史 |
| `rerank_drop_count` | int | Rerank 连续下降次数 |
| `retrieval_observations` | list[dict] | 检索观测记录 |
| `agent_iteration_count` | int | 实际执行步数 |
| `node_timings` | dict | 节点耗时 |

### 4.3 降级与兜底

ReAct 子图内部已实现完整的降级逻辑：

1. **`react_enabled == False`**：直接返回 `finish_reason="disabled"`，不阻塞主流程
2. **`user_question` 为空**：返回 `finish_reason="empty_question"`
3. **LLM 调用失败**：返回 `finish_reason="error"`，含错误摘要
4. **Budget 耗尽**：`finish_reason="budget_exhausted"`，基于已有 evidence 给出最佳答案
5. **LoopGuard 熔断**：`finish_reason="same_action_loop"` 或 `"rerank_declining"`
6. **Policy 连续拒绝**：`finish_reason="policy_denied"`

所有降级情况下，`react_subgraph_node` 都会返回 `evidence` 列表（可能为空），主图后续流程正常处理。

---

## 5. 与 quality_check retry 的协作

### 5.1 场景

ReAct 子图执行完成后，evidence 进入 `evidence_fusion → reflection → quality_check`。如果 quality_check 判定需要重试：

```
quality_check → retry_rag → rag_tool → evidence_fusion → reflection → quality_check
```

此时 `rag_tool` 是走主图的单次 RAG 检索，不会再次进入 ReAct 子图。这是合理的设计——ReAct 已经完成了多轮探索，retry 只是补充检索。

### 5.2 终止条件

如果 ReAct 子图返回了 `termination_reason` 非空，`quality_check_decision` 会优先路由到 `fallback`，不会进入重试循环：

```python
def quality_check_decision(state: AgentState) -> str:
    termination_reason = state.get("termination_reason", "")
    if termination_reason:
        return "fallback"  # 直接兜底，不重试
    # ... 原有逻辑 ...
```

---

## 6. ReAct 子图 vs Plan Executor 的决策边界

| 维度 | ReAct 子图 | Plan Executor |
|------|-----------|---------------|
| 触发条件 | `react_enabled` + `complex` + `needs_multi_tool` | `complex` + planner 来源 |
| 执行方式 | LLM 动态决策每步，循环迭代 | DAG 静态规划，并行/串行执行 |
| 工具切换 | 运行时根据中间结果动态切换 | 提前规划好所有步骤 |
| 优势 | 灵活性高，能根据中间结果调整策略 | 效率高，并行步骤可同时执行 |
| 劣势 | Token 消耗大，延迟高 | 无法根据中间结果动态调整 |
| 适用场景 | 探索性分析、根因分析、多步推理 | 确定性多任务、可并行查询 |

**决策规则**：优先 ReAct（灵活），备选 Plan Executor（高效）。用户可通过 `react_enabled` 开关控制。

---

## 7. 测试计划

### 7.1 单元测试

| 测试用例 | 验证点 |
|---------|--------|
| `test_react_route_complex_multi_tool` | `react_enabled=True` + `complex` + `needs_multi_tool` → 路由到 `react_subgraph` |
| `test_react_route_disabled` | `react_enabled=False` → 走 `plan_executor`（不回退到 ReAct） |
| `test_react_route_simple` | `complexity=simple` → 不走 ReAct |
| `test_react_route_rag_only` | `route_target=rag` → 不走 ReAct（即使 `needs_multi_tool=True`） |
| `test_react_subgraph_output` | ReAct 子图返回的 evidence 正确合并到主图 state |
| `test_react_subgraph_termination_fallback` | `termination_reason` 非空 → quality_check 路由到 `fallback` |

### 7.2 集成测试

| 测试用例 | 验证点 |
|---------|--------|
| `test_react_full_flow` | 完整流程：问题 → ReAct → evidence_fusion → answer |
| `test_react_rag_db_interleaving` | ReAct 循环内 rag_search 和 db_query 交替调用 |
| `test_react_budget_exhausted` | Budget 耗尽后优雅降级，不阻塞主流程 |
| `test_react_plan_executor_fallback` | ReAct 禁用时 plan_executor 正常工作 |

### 7.3 回归测试

确保现有路径不受影响：
- `test_rag_tool_flow` — 纯 RAG 查询
- `test_db_tool_flow` — 简单 DB 查询
- `test_hybrid_serial_flow` — rag_tool → db_tool 串行
- `test_plan_executor_flow` — plan_executor 路径

---

## 8. 实施步骤

| 步骤 | 文件 | 改动内容 | 预计工作量 |
|------|------|---------|-----------|
| 1 | `state.py` | 确认/新增 `react_enabled` 字段 | 5 分钟 |
| 2 | `nodes/intent_router.py` | 新增 `needs_multi_tool` 判定 + `route_decision` 新增 `react_subgraph` 路由 | 30 分钟 |
| 3 | `graph.py` | 新增 `react_subgraph` 节点 + 边 + 条件路由 | 15 分钟 |
| 4 | 能力开关注入 | 确保 `react_enabled` 正确初始化到 state | 15 分钟 |
| 5 | 单元测试 | 编写 6 个单元测试 | 30 分钟 |
| 6 | 集成测试 | 编写 4 个集成测试 | 30 分钟 |
| 7 | 回归测试 | 确保现有测试全部通过 | 15 分钟 |

---

## 9. 风险与注意事项

### 9.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| ReAct 子图 Token 消耗过大 | 成本增加 | Budget Controller 有 max_steps 上限（默认 8），Token 预算可配置 |
| ReAct 子图延迟过高 | 用户体验差 | Budget Controller 有 latency 上限（默认 60s），超时强制终止 |
| `needs_multi_tool` 判定不准 | 误触发或不触发 ReAct | 通过 LLM 推理 + 置信度阈值双重保障；后续可加入规则兜底 |
| 与 plan_executor 的边界模糊 | 两个路径抢任务 | 优先级明确：ReAct 优先，plan_executor 备选 |

### 9.2 注意事项

1. **`react_enabled` 默认值必须为 `False`**：ReAct 子图是可选增强能力，默认关闭保证向后兼容
2. **`after_rag_tool` 的 hybrid 串行路径不受影响**：当 `route_target == "hybrid"` 但 `needs_multi_tool == False` 时，仍走原有的 `rag_tool → db_tool` 串行
3. **ReAct 子图不直接生成最终答案**：子图只产出 evidence，最终答案由 `llm_generate` 根据 evidence 生成，保持与现有链路一致
4. **evidence 去重**：`react_subgraph_node` 已实现 evidence 合并去重，不会重复计算
5. **日志与观测**：ReAct 子图每步都有详细日志，`react_execution_result` 包含完整执行轨迹，便于调试和审计

---

## 10. 附录：ReAct 子图内部执行流程

```
┌─────────────────────────────────────────────────────────┐
│ ReactSubgraph.run()                                      │
│                                                          │
│ 初始化 ReactState + Budget + Policy Guard + LoopGuard    │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │ while True:                                       │   │
│  │                                                    │   │
│  │  1. Budget Check (can_proceed?)                   │   │
│  │     ├── step_count >= max_steps? → break          │   │
│  │     ├── tool_call_count >= max_tool_calls? → break│   │
│  │     ├── token_used >= max_tokens? → break         │   │
│  │     └── elapsed >= deadline? → break              │   │
│  │                                                    │   │
│  │  2. LoopGuard Check                               │   │
│  │     ├── same_action_count >= 3? → break           │   │
│  │     └── rerank_drop_count >= 2? → break           │   │
│  │                                                    │   │
│  │  3. LLM 推理 (Think)                              │   │
│  │     输入: user_question + action_history          │   │
│  │     输出: {thought, action_type, arguments}       │   │
│  │                                                    │   │
│  │  4. 终态判定                                      │   │
│  │     ├── action_type == "finish" → break           │   │
│  │     └── action_type == "ask_clarification" → break│   │
│  │                                                    │   │
│  │  5. Policy Guard 校验                             │   │
│  │     ├── DENY → 记录失败, continue (最多3次)      │   │
│  │     ├── NEEDS_APPROVAL → 降级为 DENY             │   │
│  │     └── ALLOW → 继续执行                          │   │
│  │                                                    │   │
│  │  6. Tool Executor 执行 (Act)                      │   │
│  │     ├── rag_search → RAG 检索                     │   │
│  │     ├── db_query → SQL Agent 子图                 │   │
│  │     └── web_search → Web 搜索                     │   │
│  │                                                    │   │
│  │  7. Observation + Evidence 累积 (Observe)         │   │
│  │     ├── 标准化 observation 写入 action_history    │   │
│  │     └── evidence 去重追加                         │   │
│  │                                                    │   │
│  │  8. LoopGuard 更新                                │   │
│  │     ├── 检查连续相同动作                          │   │
│  │     └── 检查 Rerank 下降趋势                      │   │
│  │                                                    │   │
│  │  9. budget.increment_step()                       │   │
│  │                                                    │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  产出 ReactExecutionResult                               │
│    ├── success: bool                                     │
│    ├── evidence: list[Evidence]                          │
│    ├── step_count: int                                   │
│    ├── finish_reason: str                                │
│    ├── termination_reason: str                           │
│    └── budget_snapshot: dict                             │
└─────────────────────────────────────────────────────────┘
```