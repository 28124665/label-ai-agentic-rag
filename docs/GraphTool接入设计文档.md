# GraphTool 接入 data-knowledge-api 设计文档

> 版本：v1.0
> 日期：2026-09-03
> 主题：在现有 Agentic RAG（LangGraph）主干上接入 GraphTool，引入 GraphRAG（知识图谱问答）能力
> 契约第一优先级：`docs/GraphTool上游对接文档.md`
> 下游参考：`docs/GraphTool接入设计方案.md`（旧方案，仅采纳合理部分）、`ontology/ontology-other/ontology_v2_formal`（下游 GraphQAPipeline 代码）

---

## 1. 概述与背景

### 1.1 目标

在 data-knowledge-api 的 LangGraph Agentic RAG 主图中新增一个 **`graph_tool` 节点**，通过独立的 HTTP 包装服务（方案 A）调用下游 `ontology_v2_formal` 的 `GraphQAPipeline`（Planner → Schema 自省 → Cypher 生成 → Neo4j 执行 → repair 重试 → 答案合成），为系统补充「关系图谱 / 实体关联 / 多跳查询」类问题的能力，并与既有 RAG / DB / Web / Rest 工具在同一套证据融合、质量检查、幻觉检测机制下统一运行。

### 1.2 接入方式：HTTP 包装服务（方案 A）

- 下游 `GraphQAPipeline` 是独立 Python 代码（`ontology_v2_formal`），通过一个薄 HTTP 包装服务对外暴露 `POST /graph/ask`。
- data-knowledge-api 侧新增 `GraphTool`（适配器），只依赖 HTTP 契约，不直接 import 下游代码（防腐层，与 `RestTool` / `DataRpc` 同思路）。
- 该方案与现有 `RestTool` 架构完全对齐，隔离下游变更风险。

### 1.3 现有主干流程与插入点

当前主图（`agent/langgraph/graph.py`）节点与条件边如下（关键部分）：

```
question_input → intent_router ──(条件路由)──> rag_tool / db_tool / React 子图 / plan_executor / rest_tool / ...
                            └──> 各工具节点 ──> evidence_fusion ──> reflection ──> quality_check
                                                                                          └─(不够好)─> retry 回对应工具
evidence_fusion → prompt_assembly → llm_generate → hallucination → answer_renderer → observability → answer_output
```

GraphTool 的插入点完全对称于 `rest_tool`：

```
intent_router ──(route_target == "graph")──> graph_tool ──> evidence_fusion ──> ... ──> quality_check
                                                                                        └─(retry_graph)─> graph_tool
```

下游 Graph 结果（`GraphQAResult`）进入 evidence_fusion 后，被标准化为 `source_type="graph"` 的 `Evidence`，从而自动复用现有 Claim 级幻觉检测、质量检查、Token 预算压缩链路。

---

## 2. 接口契约（第一优先级摘要）

### 2.1 配置：`tools_config.tools` 新增 `"graph"`

`graph_config` 参数表：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `source_type` | str | `"Meeting"` | 图源标签，影响 Schema 自省范围 |
| `max_rows` | int | `20` | 最大返回行数 |
| `max_result_chars` | int | `12000` | 结果最大字符数（Token 预算压缩依据） |
| `enable_hybrid_retrieval` | bool | `false` | 是否启用向量 + 图谱混合召回 |
| `enable_pg` | bool | `false` | 是否启用 PostgreSQL 结构化回退 |
| `timeout_ms` | int | `30000` | 下游超时 |

### 2.2 路由机制（四层）

按 confidence 从高到低：

1. **精确指令 `@graph`**：confidence 1.0，直接定向，不进入后续 LLM 路由。
2. **规则关键词**：`关系图谱 / 知识图谱 / 实体关联 / 图查询 / 多跳`。
3. **LLM 语义路由**：query 语义与「图谱/实体关系」意图匹配。
4. **Planner 子步骤**：复杂任务规划时，`PlanStep.tool="graph"` 作为子步骤之一。

### 2.3 响应引用

- `chunks[*].source_type = "graph"`
- `chunks[*].source_uri = "graph://neo4j/<Label>/<Key>"`
- `chunks[*].score = 0.85`

### 2.4 错误处理与降级

| 场景 | 处理 |
|------|------|
| 服务不可达 | `quality_score = 0.0` |
| Cypher 生成/执行失败 | `repair` 重试，最多 3 次 |
| 空结果（row_count=0） | `quality_score = 0.0`，降级 RAG |
| 重试耗尽 | 降级 RAG / Web |
| `quality_score < 0.7` | 触发 `retry_graph` |

---

## 3. 四大重点方向变更清单

> 以下每个变更点均标注对应源文件。行号基于当前主干实现，仅供定位参考，实施时以实际代码为准。

---

### 3.1 意图路由

#### 3.1.1 `agent/langgraph/routers/models.py`

1. `RouteDecision.target` 的 `Literal["rag","database","web","rest","hybrid","chitchat"]` **增加 `"graph"`**（L25 附近）。
2. `StepArgs` 增加 graph 专用可选字段（L47-60 附近）：

   ```python
   source_type: str = "Meeting"          # 图源标签，默认与 graph_config 一致
   enable_pg: bool = False               # 是否启用 PG 结构化回退
   max_rows: int = 20
   max_result_chars: int = 12000
   enable_hybrid_retrieval: bool = False
   timeout_ms: int = 30000
   ```

3. `StepArgs.from_dict()` 的 `known_fields` 白名单同步新增上述字段（L75-81 附近），否则 Planner 子步骤里的 graph 参数会被静默丢弃。
4. `PlanStep.tool` 的 `Literal["rag","database","web","rest","report"]` **增加 `"graph"`**（L99 附近）。
5. `ClarificationRequest.options` 的 `list[Literal["rag","database","web","rest","hybrid","chitchat"]]` **增加 `"graph"`**（L153 附近）。

#### 3.1.2 `agent/langgraph/routers/pre_filter.py`

`DIRECTIVE_PATTERNS`（L92-97 附近）新增精确指令模式：

```python
(re.compile(r"^@(?:graph|图谱|知识图谱)\s*", re.IGNORECASE), "graph"),
```

对应的指令说明里补充 `@graph`，使 `directive=True` 时直接定向到 graph，跳过 Skill 路由与 LLM 语义路由（与 `@database` / `@rag` 同机制）。

#### 3.1.3 `agent/langgraph/routers/rule_router.py`

1. `STRONG_PATTERNS`（L31-46 附近）增加 graph Tier1 强模式（confidence 建议 0.80，与既有强模式一致）：

   ```python
   # 图查询强模式
   (re.compile(r"(关系图谱|知识图谱|实体关联|多跳查询|图查询|多跳)"), "graph", 0.80),
   ```

2. 增加 Tier2 弱模式 `graph_bias`（如单次出现「图谱」「实体」等，confidence 0.3~0.4，供 LLM 语义路由合并打分），避免伤及普通全文检索问法。
3. 综合打分逻辑中对 `target == "graph"` 增加 `route_target` 产出路径，并保证 `graph` 是合法目标之一。

#### 3.1.4 `agent/langgraph/nodes/intent_router.py`

1. `route_decision()`（L630-699 附近）新增 `graph` 分支：

   ```python
   if rd.target == "graph":
       return "graph_tool"
   ```

   （放在 `database → db_tool` 分支之后、`hybrid → rag_tool` / `else → prompt_assembly` 之前。）

2. **遗留不一致修复（建议一并处理）**：当前 `route_decision()` 缺少 `web_tool` / `rest_tool` 的目标映射分支（docstring 写「Chitchat/Web → prompt_assembly」，web/rest 都落入 `else → prompt_assembly`），但 `graph.py` 的 intent_router 条件边映射却包含 `rest_tool` 而无 `web_tool`。说明路由函数与条件边已存在漏配。接入 graph 时必须：
   - 在 `route_decision()` 补齐 `web → web_tool`、`rest → rest_tool`、`graph → graph_tool` 三个分支；
   - 让 `graph.py` 条件边与 `route_decision()` 返回值严格一一对应，避免 graph_tool 出现「条件边有、函数不返回」的断裂。
3. `_finalize_route()`（L317-429 附近）无需为 graph 增加 Skill 解析重写逻辑（graph 属数据检索类，不参与 report/rag 的 `plan_with_skills` 改写），但需确认 `chitchat` / `directive` 短路之后 graph 目标不被误改写。

#### 3.1.5 `agent/langgraph/state.py`

1. `AgentState.route_target` 的 `Literal[...]` **增加 `"graph"`**（L125 附近）。

---

### 3.2 主图流程

#### 3.2.1 `agent/langgraph/graph.py`

1. 新增 import：

   ```python
   from agent.langgraph.nodes.graph_tool_node import graph_tool_node
   ```

2. 注册节点：

   ```python
   workflow.add_node("graph_tool", graph_tool_node)
   ```

3. `intent_router` 条件边映射（L141-153 附近）增加 `"graph_tool"`：

   ```python
   {"clarification", "rag_tool", "db_tool", "react_subgraph", "plan_executor",
    "rest_tool", "graph_tool", "prompt_assembly"}
   ```

4. 新增边：

   ```python
   workflow.add_edge("graph_tool", "evidence_fusion")
   ```

5. `quality_check` 条件边映射（L199-210 附近）增加 `"graph_tool"`（`retry_graph`）：

   ```python
   {"prompt_assembly", "rag_tool", "db_tool", "web_tool", "rest_tool",
    "graph_tool", "fallback"}
   ```

#### 3.2.2 `agent/langgraph/state.py`

新增 graph 工具输出字段（与 rest 字段对齐，L151-173 附近）：

- `ToolResult` TypedDict（L27-71 附近）新增：

  ```python
  graph_result: dict      # GraphQAResult 原始结果（或经 GraphTool 包装后的结构化结果）
  graph_quality_score: float
  graph_score_source: str  # "graph"
  ```

- `AgentState` 工具输出区新增：

  ```python
  graph_result: dict
  graph_quality_score: float
  graph_score_source: str
  graph_has_result: bool
  graph_row_count: int
  ```

#### 3.2.3 `agent/langgraph/nodes/quality_check.py`

质量检查需要识别 graph 结果并支持 `retry_graph`。

1. 新增常量：

   ```python
   GRAPH_PASS_THRESHOLD = 0.7  # 与旧方案一致，后续可调
   ```

2. `quality_check_node()`（L57-192 附近）：`route_target == "graph"` 分支读取 `graph_quality_score` / `graph_has_result` / `graph_row_count`，复用其 `verdict` 决策骨架，产出 graph 专属质量结论（pass / retry_graph / fallback_web）。在 `row_count==0` 或 `quality_score < 0.7` 时判定为不通过。判断逻辑建议：

   ```python
   if state.get("termination_reason"):   # 循环熔断等硬终止
       route = "fallback"
   elif graph_row_count == 0 or graph_quality_score < GRAPH_PASS_THRESHOLD:
       route = "retry_graph"
   elif graph_quality_score >= QUALITY_PASS_THRESHOLD (=0.7):
       route = "pass"
   # 中间档按现有 retry_count 递增策略处理
   ```

3. `_make_decision()`（L225-279 附近）返回类型增加 `retry_graph`；`quality_check_decision()`（L282-315 附近）增加：

   ```python
   if decision == "retry_graph":
       return "graph_tool"
   ```

   重试计数沿用现有 `retry_count` 递增机制，避免无限重试（配合 graph 自身 repair 最多 3 次 + 降级 RAG/Web）。

#### 3.2.4 `agent/langgraph/nodes/evidence_fusion.py`

1. `DEFAULT_SOURCE_QUOTA`（L51-56 附近）增加 graph 配额：

   ```python
   DEFAULT_SOURCE_QUOTA = {"rag": 10, "db": 10, "web": 5, "report": 5, "graph": 8}
   ```

   （graph 是结构化结果但 answer 文本较长，建议配额略低于 db，具体与 `max_result_chars` 协调。）

2. `_normalize_current_tool_results()`（L413-469 附近）增加 `graph_result` 分支，调用 `normalize_graph_evidence()`（见 3.3.1）。

---

### 3.3 幻觉检测

> 核心结论：**无需改造 PairVerifier / PolicyEngine / EnforcementMode 链路**。graph 证据只要在 evidence_fusion 阶段被正确标准化为 `source_type="graph"` 的 `Evidence` dict，即可复用现有 Claim 级可追溯验证（三层验证 Rule + NLI + LLM）。

#### 3.3.1 `agent/langgraph/evidence/models.py`

1. `Evidence` TypedDict 的 `source_type` `Literal["rag","db","web","rest","report"]` **增加 `"graph"`**（L39-72 附近）。
2. `TOOL_SOURCE_TYPE_MAP`（L76-86 附近）增加：

   ```python
   "graph": "graph",
   "graph_tool": "graph",
   ```

3. `DEFAULT_AUTHORITY_SCORE`（L89-95 附近）增加 graph 档位。建议 **0.90**（对齐 rest：结构化、可追溯；略低于 db 的 0.95，因为 answer 部分是 LLM 从图行合成，存在一定二次加工）：

   ```python
   DEFAULT_AUTHORITY_SCORE = {
       "db": 0.95, "rest": 0.90, "graph": 0.90,
       "report": 0.85, "rag": 0.80, "web": 0.50,
   }
   ```

4. 新增 `normalize_graph_evidence()`（以 `normalize_rest_evidence` L512-574 为模板）：

   - 输入：`graph_result`（GraphQAResult 结构，见第 5 节）。
   - **content 构造**：优先取 `answer` 文本；若为空则用 `rows` 逐行拼接（`Source URI 头 + 每行 key=value`），保证 content 可被 claim 验证；`rows` 可能是 dict 时 `json.dumps(..., ensure_ascii=False)`。
   - `source_uri`：`graph://neo4j/<Label>/<Key>`（从 rows 首行提取 Label/Key；缺省 `graph://neo4j/graph`）。
   - `score`：0.85（契约值）或 `quality_score` 映射。
   - `authority`：`DEFAULT_AUTHORITY_SCORE["graph"]`。
   - `freshness_score`：1.0（与 rest 一致）。
   - 处理 `row_count == 0` / `error != ""` 时跳过（不产出 Evidence，交由质量检查降级）。
   - 复用 `_truncate_content()`（2000 字符）与 `_scrub_sensitive()` 脱敏。

5. `normalize_tool_result()`（L577-612 附近）分派处新增 graph 分支：

   ```python
   elif source_type in ("graph", "graph_tool"):
       return normalize_graph_evidence(result, ...)
   ```

#### 3.3.2 `agent/langgraph/config.py`

- `resolve_run_mode()`：确认 non-chitchat 默认落在 FACTUAL 模式即可。graph 目标应属于 factual 证据型回答，走换向验证（claim 提取 + 证据比对）。**无需新增枚举值**，但建议在 run_mode 解析处注释 graph 属 factual，避免后续被误判。
- 确认 `graph_config` 从 `tools_config` 正确挂载到运行时配置，供 `GraphTool` 与 `graph_tool_node` 读取。

#### 3.3.3 效果说明

graph evidence 进入 `evidence` / `evidence_snapshot`（不可变，`snapshot_id` 标识）后：

- 参与 `PairVerifier` 的 claim 级验证（graph answer 文本会被切分为 claim，与 graph evidence 比对）；
- 权威性 0.90 会在 verdict 证据强度加权中体现；
- 若 `answer` 与 `rows` 不一致，可通过 repair/claim 验证机制被标记，最终由 `policy_action` 决定 reject / 标注 citation。

---

### 3.4 Skill 体系

> graph 能力以「证据来源扩展」方式接入 Skill，而非引入独立 Skill 类型。这样最小化改动，同时让既有 report / data skill 能声明 graph 证据需求。

#### 3.4.1 `agent/langgraph/skills/evidence_adapter.py`

`_source_type_matches()`（L150-160 附近）别名映射新增：

```python
"graph": {"graph", "graph_rows"},
"graph_rows": {"graph", "graph_rows", "graph_result"},
```

（与 `db` / `db_rows`、`rag` / `rag_docs` 的别名机制一致。）

#### 3.4.2 `agent/langgraph/skills/card.py`

`_derive_capabilities()`（能力推导）新增：当一个 Skill 的 `evidence_requirements` / `required_evidence_types` 含 `graph` 时，向 capabilities 追加 `"graph"`（类比现有：有 DataSkill 追加 `"database"`、有 RetrievalSkill 追加 `"rag"`）。

#### 3.4.3 `agent/langgraph/skills/resolver.py`

`_runtime_capability_ok()`（L348-383 附近）目前仅检查 `db_tool.enabled`。新增：

```python
if "graph" in required_capabilities and not graph_tool.enabled:
    return False  # 运行时 graph 未启用，该 skill 不满足
```

#### 3.4.4 `agent/langgraph/skills/planner_adapter.py`

`SkillPlannerAdapter.build_plan()` 新增 `_build_graph_steps()`，当 skill 声明了 graph 证据需求时生成 `PlanStep(tool="graph", ...)`，并填入 `source_type / max_rows / enable_pg` 等 `StepArgs` 字段（与 `_build_database_steps()` / `_build_rag_steps()` 并列）。

#### 3.4.5 `agent/langgraph/skills/models.py`

- 短期**无需新增 `GraphSkill` 类型**。graph 作为可选证据来源，挂到现有 `ReportSkill.required_evidence_types` / `DataSkill` 的 evidence 需求下即可。
- 若后续出现「特定 report 类型必须走图谱」的强需求，再扩展 `required_evidence_types` 语义或新增独立 Skill 类型（留作扩展点，本期不做）。

---

## 4. 新增文件

### 4.1 `agent/langgraph/tools/graph_tool.py`

以 `agent/langgraph/tools/rest_tool.py` 为模板实现 `GraphTool`（HTTP 适配器）：

- 输入 TypedDict `GraphToolInput`：

  ```python
  class GraphToolInput(TypedDict):
      query: str
      source_type: str            # "Meeting" 等
      max_rows: int
      max_result_chars: int
      enable_hybrid_retrieval: bool
      enable_pg: bool
      timeout_ms: int
  ```

- 输出 TypedDict `GraphToolOutput`：映射下游 `GraphQAResult`（见第 5 节）＋ `quality_score`。
- 职责：拼装 HTTP 请求 → 调用 `POST /graph/ask` → 解析响应 → `_evaluate_quality()` 计算 `quality_score` → 返回结构化结果。
- 参考 `rest_tool.py` 的 SHA-256 请求签名 + 单例 `get_graph_tool()`。

### 4.2 `agent/langgraph/nodes/graph_tool_node.py`

以 `agent/langgraph/nodes/rest_tool_node.py`（126 行，最新 Tool 节点模式）为模板：

- 优先从 `state["execution_plan"]` 提取 `PlanStep(tool="graph")` 的 `StepArgs`；
- 否则从 `route_decision.metadata` 提取 graph 参数；
- 调用 `GraphTool` 后返回：

  ```python
  {
      "graph_result": output,
      "graph_quality_score": output["quality_score"],
      "graph_score_source": "graph",
      "graph_has_result": bool(output.get("row_count", 0) > 0),
      "graph_row_count": output.get("row_count", 0),
      "agent_iteration_count": state["agent_iteration_count"] + 1,
      "node_timings": {...},
  }
  ```

> 说明：Planner 子步骤场景下，graph 会作为 `PlanStep` 之一由 `plan_executor` / React 子图触发，`graph_tool_node` 需要能从 `execution_plan` 正确取参，与 `rest_tool_node` 保持同一取参约定。

---

## 5. 数据契约附录

### 5.1 下游 `GraphQAResult`（`graph_qa/state.py`）→ `GraphToolOutput` 映射

下游 `GraphQAResult` 字段与上游 `GraphToolOutput` 的对应关系：

| GraphQAResult 字段 | 类型 | GraphToolOutput 用途 |
|--------------------|------|----------------------|
| `question` | str | 原样透传 |
| `planner` | PlannerOutput | 调试日志 / 质量上下文 |
| `cypher_plan` | CypherPlan | 调试日志（cypher / params / rationale） |
| `rows` | list | 证据化的核心数据（转 content / source_uri） |
| `answer` | str | 证据化的答案文本（claim 验证对象） |
| `row_count` | int | 质量判断（>0 才有效） |
| `error` | str | 质量判断（非空 → quality_score=0.0） |
| `repair_trace` | list | 质量判断（非空 → -0.1） |
| `hybrid_context` / `hybrid_meta` | str/dict | enable_hybrid_retrieval 时附带 |
| `cypher_query_log` / `query_stats` | list/dict | 调试日志 |
| `sql_plan` / `pg_rows` / `pg_row_count` | ... | enable_pg 时附带（结构化回退） |
| `pg_error` / `pg_query_log` / `pg_query_stats` / `pg_repair_trace` | ... | PG 回退日志 |

### 5.2 quality_score 推导规则（采纳旧方案合理部分）

```python
score = 0.0
if output.get("error"):
    score = 0.0
elif output.get("row_count", 0) == 0:
    score = 0.0          # 空结果 → 降级 RAG
else:
    score = 0.85         # 契约分数
    if miss_rate > 0.5:
        score = 0.5
    if output.get("repair_trace"):
        score -= 0.1
score_source = "graph"
```

`GRAPH_PASS_THRESHOLD = 0.7`，`score < 0.7` 触发 `retry_graph`。

### 5.3 source_uri 格式

```
graph://neo4j/<Label>/<Key>
```

从 `rows` 首行提取 `Label`（节点/关系类型）与 `Key`（主键）；缺省 `graph://neo4j/graph`。

---

## 6. 实施顺序（采纳旧方案，结合当前架构调整）

1. **配置与状态底座**：`config.py` 挂载 `graph_config`；`state.py` / `routers/models.py` 增加 `graph` 目标与字段。
2. **路由接入**：`pre_filter.py`（@graph）→ `rule_router.py`（关键词）→ `intent_router.py`（route_decision 补 graph + 修复 web/rest 断裂）。
3. **工具与节点**：`tools/graph_tool.py` → `nodes/graph_tool_node.py`。
4. **主图接线**：`graph.py` 注册节点 + 条件边。
5. **证据与幻觉**：`evidence/models.py`（normalize_graph_evidence + authority）→ `evidence_fusion.py`（quota + 分支）。
6. **质量检查**：`quality_check.py`（GRAPH_PASS_THRESHOLD + retry_graph）。
7. **Skill 体系**：`skills/evidence_adapter.py` → `card.py` → `resolver.py` → `planner_adapter.py`。
8. **联调验证**：与下游 HTTP 包装服务 `POST /graph/ask` 联调，覆盖 error / 空结果 / repair / hybrid / pg 各分支及降级路径。

---

## 7. 风险与注意事项

1. **路由断裂风险**：`route_decision()` 与 `graph.py` 条件边必须严格一致（第 3.1.4 节），否则 graph 目标永不触发或触发后无处落地。
2. **证据权威性档位的校准**：graph 建议 0.90，但需在真实数据上验证其对 verdict / citation 的影响，避免高估 graph answer 的可信度。
3. **Token/长度控制**：graph `answer` 可能较长，需绑定 `max_result_chars` 与 `_truncate_content()`，并受 `DEFAULT_SOURCE_QUOTA` 约束，防止挤压其它证据源。
4. **降级闭环**：graph 失败/空结果必须能平滑降级到 RAG/Web，且重试次数受 `retry_count` + repair（≤3 次）双重限制，避免死循环。
5. **下游契约漂移**：`GraphQAResult` 结构以下游 `graph_qa/state.py` 为准，HTTP 契约以第一优先级文档为准；实现时前置字段校验与默认值兜底。