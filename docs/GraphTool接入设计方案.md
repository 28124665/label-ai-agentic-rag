# GraphTool 接入设计方案

> 将 `ontology_v2_formal` 的 `GraphQAPipeline`（图检索 + Cypher 生成 + 答案合成）作为新 tool 接入 `data-knowledge-api`（Agentic RAG 系统）

---

## 1. 背景与目标

### 1.1 源能力

`ontology_v2_formal` 项目中的 [GraphQAPipeline](file:///Users/renwk/workspace/ontology/ontology-other/ontology_v2_formal/graph_qa/pipeline.py) 提供了一条完整的图问答流水线：

```
用户问题 → Planner → Schema 自省 → Cypher 生成 → Neo4j 执行 → repair 重试循环 → 答案合成
                                                                     ↓ (可选)
                                                              PostgreSQL 双源分支
```

核心入口：`GraphQAPipeline.ask(question: str) → GraphQAResult`，输出已包含合成后的自然语言答案（`answer` 字段），**不同于 RAGTool 只出 docs**。

### 1.2 目标

- 在 `data-knowledge-api` 的 LangGraph 状态图中新增 `graph_tool` 节点
- 用户提问可被路由到 graph 能力，获取图数据库中的结构化知识
- 输出并入现有 `evidence_fusion → reflection → quality_check → prompt_assembly` 链路

### 1.3 服务边界

`ontology_v2_formal` 与 `data-knowledge-api` 是两个独立 Python 项目，graph 侧依赖 `cognitive_agent` + `kg_resolve_pipeline`，无法直接 import。**采用方案 A：独立 HTTP 包装服务**。

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│  ontology_v2_formal (新增)                                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  POST /graph/ask                                           │  │
│  │    Request:  { question, source_type, max_rows, ... }      │  │
│  │    Response: { answer, rows, planner, cypher, ... }        │  │
│  │  内部: GraphQAPipeline.ask(question) → GraphQAResult       │  │
│  └───────────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP (httpx)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  data-knowledge-api (新增/修改)                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  graph_tool.py          GraphTool 类 (async invoke)        │  │
│  │  graph_tool_node.py     LangGraph 节点 (读state→invoke→写)  │  │
│  │  state.py               +route_target/graph 输出字段        │  │
│  │  models.py              +target="graph" / +tool="graph"    │  │
│  │  intent_router.py       +route_decision 分支               │  │
│  │  graph.py               +add_node + add_edge               │  │
│  │  evidence_fusion.py     +graph 标准化                       │  │
│  │  quality_check.py       +retry_graph 分支                  │  │
│  │  pre_filter.py          +@graph 指令                       │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. GraphTool I/O 接口

### 3.1 `GraphToolInput`（用户侧 → Tool）

对齐 [RAGToolInput](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py) 的 `TypedDict` 规范，同时暴露 `GraphQAPipeline` 的核心配置参数：

```python
class GraphToolInput(TypedDict, total=False):
    # ── 核心查询 ──
    query: str                        # 必填，用户问题 / 子问题
    query_simplified: str             # 繁转简后的查询文本

    # ── 本体 Schema 配置 ──
    source_type: str                  # 本体子类型，默认 "Meeting"

    # ── 结果控制 ──
    max_rows: int                     # 最大返回行数，默认 20
    max_result_chars: int             # 最大结果字符数，默认 12000

    # ── 高级能力开关 ──
    enable_hybrid_retrieval: bool     # hybrid 向量上下文检索，默认 False
    enable_pg: bool                   # PostgreSQL 双源分支，默认 False

    # ── 租户与模型 ──
    tenant_id: str
    llm_id: str

    # ── 超时 ──
    timeout_ms: int                   # 默认 30000
```

### 3.2 `GraphToolOutput`（Tool → 主流程）

由 `GraphQAResult` 映射，对齐 [RAGToolOutput](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py) 的质量字段，同时保留图查询的丰富追溯信息：

```python
class GraphToolOutput(TypedDict, total=False):
    # ── 主输出（与 RAGTool/RestTool 对齐） ──
    answer: str                       # pipeline 合成答案（区别于 RAGTool 只出 docs）
    docs: list[dict]                  # rows 规范化后的 evidence 列表
                                      #   每项: {content, source_uri, source_type, score}

    # ── 质量字段（供 quality_check 复用阈值逻辑） ──
    quality_score: float              # 0.0~1.0，由 rows 命中率/错误率推导
    has_relevant: bool                # 是否存在相关结果
    relevant_count: int               # 相关结果数量

    # ── 错误 ──
    error: str                        # 空字符串表示成功
    error_code: str                   # RETRIEVAL_SERVICE_ERROR / RETRIEVAL_DATA_ERROR / ""

    # ── 可观测性 / 追溯（GraphQAResult 富字段直透传） ──
    planner: dict                     # PlannerOutput 序列化 {intent, sub_questions, ...}
    cypher_query: str                 # CypherPlan.cypher
    cypher_params: dict               # CypherPlan.params
    rows: list[dict]                  # 图查询原始结果行
    row_count: int
    repair_trace: list[str]           # Cypher 修复追踪
    query_stats: dict                 # {miss_rate, hit_rate, ...}

    # ── PostgreSQL 双源（enable_pg=True 时） ──
    pg_rows: list[dict]
    pg_row_count: int
    pg_query_log: list[dict]
    pg_query_stats: dict
    pg_repair_trace: list[str]

    # ── 耗时 ──
    latency_ms: int
```

### 3.3 `quality_score` 推导规则

GraphTool 不像 RAGTool 有 Rerank 分数，需从 GraphQAResult 的以下信号推导：

| 信号 | 质量分数贡献 |
|------|-------------|
| `error` 非空 | `quality_score = 0.0`，`has_relevant = False` |
| `row_count == 0` | `quality_score = 0.0`，`has_relevant = False` |
| `row_count > 0` 且 `error == ""` | `quality_score = 0.85`（Cypher 执行成功即为高质量） |
| `query_stats.miss_rate > 0.5` | `quality_score = 0.5`（大量标签缺失，降分） |
| `repair_trace` 非空 | `quality_score -= 0.1`（修复过，略微降分） |

`score_source` 固定为 `"graph"`，`quality_check` 需注册图形阈值（建议 `GRAPH_PASS_THRESHOLD = 0.7`）。

---

## 4. GraphTool 类接口

```python
class GraphTool:
    """Graph QA Tool —— 通过 HTTP 调用 ontology_v2_formal 的 GraphQAPipeline。"""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url
        self.timeout = timeout

    async def invoke(self, input_data: GraphToolInput) -> GraphToolOutput:
        """调用远程 graph QA 服务，映射结果为 TypedDict 输出。"""

    def _empty_result(self, start_time, *, error="", error_code="") -> GraphToolOutput:
        """构建空结果（网络错误 / 超时 / 服务异常）。"""


def get_graph_tool() -> GraphTool:
    """全局单例工厂，避免重复初始化 httpx 客户端。"""
```

关键设计点：

- 使用 `httpx.AsyncClient`（与 [HttpVerifierGateway](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/gateways/http_verifier.py) 一致）
- 无内层重试（重试交给主流程 [quality_check](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/quality_check.py)）
- 异常分类：`httpx.TimeoutException` / `HTTPStatusError` → `RETRIEVAL_SERVICE_ERROR`；`ConnectError` → `RETRIEVAL_SERVICE_ERROR`

---

## 5. Ontology 侧：HTTP 包装服务

### 5.1 API 契约

新建文件：`ontology-other/ontology_v2_formal/graph_qa_http/app.py`

```
POST /graph/ask
  Content-Type: application/json

  Request:
  {
    "question": "张三参加了哪些会议？",
    "source_type": "Meeting",
    "max_rows": 20,
    "max_result_chars": 12000,
    "enable_hybrid_retrieval": false,
    "enable_pg": false
  }

  Response 200:
  {
    "answer": "张三参加了以下会议：...",
    "rows": [{...}, {...}],
    "row_count": 5,
    "planner": {"intent": "...", "sub_questions": [...]},
    "cypher_query": "MATCH (p:Person {name:'张三'})-[:ATTENDED]->(m:Meeting) RETURN m",
    "cypher_params": {},
    "repair_trace": [],
    "query_stats": {"miss_rate": 0.0, "hit_rate": 1.0},
    "pg_rows": [],
    "pg_row_count": 0,
    "pg_query_log": [],
    "pg_query_stats": {},
    "pg_repair_trace": [],
    "error": "",
    "latency_ms": 1234
  }

  Response 500:
  {
    "error": "Neo4j 连接失败: ...",
    "answer": "",
    ...
  }
```

### 5.2 内部实现要点

- 复用 [run_graph_qa.py](file:///Users/renwk/workspace/ontology/ontology-other/ontology_v2_formal/run_graph_qa.py) 的初始化逻辑：`connect_neo4j_client()` + `build_qa_llm()`
- `GraphQAPipeline` 实例化在服务启动时完成（全局单例），避免每次请求重复初始化 Neo4j 连接和 LLM 客户端
- `GraphQAResult` → JSON 序列化：`dataclasses.asdict(result)`
- 使用 FastAPI + uvicorn 部署（与 [golden_eval_lab/app.py](file:///Users/renwk/workspace/ontology/ontology-other/ontology_v2_formal/golden_eval_lab/app.py) 技术栈一致）

---

## 6. Data-Knowledge-API 侧：路由接入点

### 6.1 改动清单总览

| # | 文件 | 改动 | 说明 |
|---|------|------|------|
| 1 | [state.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/state.py) | `route_target` Literal 增加 `"graph"` | L125 |
| 2 | [state.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/state.py) | 新增 Graph 输出字段群 | 在 Rest 输出区后 |
| 3 | [models.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/routers/models.py) | `RouteDecision.target` 增加 `"graph"` | L25 |
| 4 | [models.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/routers/models.py) | `PlanStep.tool` 增加 `"graph"` | L99 |
| 5 | [models.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/routers/models.py) | `ClarificationRequest.options` 增加 `"graph"` | L153 |
| 6 | [models.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/routers/models.py) | `StepArgs` 增加 graph 专用字段 | L47-60 区 |
| 7 | [pre_filter.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/routers/pre_filter.py) | `DIRECTIVE_PATTERNS` 增加 `@graph` | L92-97 |
| 8 | [intent_router.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/intent_router.py) | `route_decision()` 增加 `graph` 分支 | L686-699 |
| 9 | [intent_router.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/intent_router.py) | `_finalize_route()` 增加 graph 的 `route_target` 处理 | L317 附近 |
| 10 | [graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py) | import + `add_node("graph_tool", ...)` | L77 / L114 |
| 11 | [graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py) | 条件边映射增加 `"graph_tool"` | L141-153 |
| 12 | [graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py) | `add_edge("graph_tool","evidence_fusion")` | L188 后 |
| 13 | [quality_check.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/quality_check.py) | `quality_check_decision()` 增加 `retry_graph` → `"graph_tool"` | L282-315 |
| 14 | [evidence_fusion.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/evidence_fusion.py) | `_normalize_current_tool_results()` 增加 graph 标准化 | L413-469 |
| 15 | [evidence_fusion.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/evidence_fusion.py) | `DEFAULT_SOURCE_QUOTA` 增加 `"graph"` | L51-56 |
| 16 | **新增** `tools/graph_tool.py` | GraphTool 类 + `get_graph_tool()` | — |
| 17 | **新增** `nodes/graph_tool_node.py` | LangGraph 执行节点 | — |

### 6.2 逐接入点详解

#### 接入点 1-2：`state.py` — 路由目标 + 输出字段

```python
# AgentState.route_target (L125) 修改：
route_target: Literal["rag", "database", "hybrid", "web", "chitchat", "rest", "graph"]

# 新增 Graph 输出字段（在 Rest 输出区 L173 后）：
graph_result: dict       # {answer, rows, cypher_query, planner, query_stats, ...}
graph_quality_score: float  # 0.0 ~ 1.0
```

#### 接入点 3-5：`models.py` — 路由决策模型

```python
# RouteDecision.target (L25) 修改：
target: Literal["rag", "database", "web", "rest", "hybrid", "chitchat", "graph"]

# PlanStep.tool (L99) 修改：
tool: Literal["rag", "database", "web", "rest", "report", "graph"]

# ClarificationRequest.options (L153) 修改：
options: list[Literal["rag", "database", "web", "rest", "hybrid", "chitchat", "graph"]]

# StepArgs 新增 graph 专用字段（L47-60 区）：
source_type: str = ""       # 本体 Schema 子类型
enable_pg: bool = False     # 是否启用 PostgreSQL 双源
```

#### 接入点 7：`pre_filter.py` — 精确指令 `@graph`

```python
# DIRECTIVE_PATTERNS (L92-97) 增加：
(re.compile(r"^@graph\s+", re.IGNORECASE), "graph"),
```

效果：用户输入 `@graph 张三参加了哪些会议` 直接路由到 graph_tool。

#### 接入点 8：`intent_router.py` — `route_decision()` 分支

在 L686 的 `if/elif` 链中插入：

```python
    elif route_target == "graph":
        _record_route_metric("graph")
        return "graph_tool"
```

放在 `elif route_target == "hybrid"` 之后、`else` 之前。

#### 接入点 10-12：`graph.py` — 节点注册

```python
# import (L77 后)：
from agent.langgraph.nodes.graph_tool_node import graph_tool_node

# 节点注册 (L114 后)：
graph.add_node("graph_tool", graph_tool_node)

# 条件边映射 (L141-153) 增加：
"graph_tool": "graph_tool",

# 工具产出进融合 (L188 后)：
graph.add_edge("graph_tool", "evidence_fusion")
```

#### 接入点 13：`quality_check.py` — 重试路由

```python
# quality_check_decision() (L282-315) 增加：
elif decision == "retry_graph":
    return "graph_tool"
```

同时在 `quality_check_node()` 中增加 graph 质量判断逻辑：`graph_quality_score < GRAPH_PASS_THRESHOLD` 时输出 `retry_graph`。

#### 接入点 14：`evidence_fusion.py` — 标准化

```python
# _normalize_current_tool_results() (L413-469) 增加：
graph_result = state.get("graph_result") or {}
if graph_result:
    evidences.extend(
        normalize_tool_result(
            tool_name="graph_qa",
            result=graph_result,
            tenant_id=tenant_id,
            query=query,
        )
    )
```

#### 接入点 15：`evidence_fusion.py` — 来源配额

```python
# DEFAULT_SOURCE_QUOTA (L51-56) 增加：
DEFAULT_SOURCE_QUOTA = {
    "rag": 10,
    "db": 10,
    "web": 5,
    "report": 5,
    "graph": 8,   # 图证据上限 8 条
}
```

---

## 7. 新增文件设计

### 7.1 `tools/graph_tool.py` — GraphTool 类

```
职责：
  - 封装 httpx 异步 HTTP 调用到 ontology graph QA 服务
  - GraphQAResult → GraphToolOutput 映射
  - quality_score 推导（按 §3.3 规则）
  - 异常分类与 _empty_result 兜底
  - get_graph_tool() 全局单例工厂

依赖：
  - httpx（延迟导入，与 HttpVerifierGateway 一致）
  - 配置：base_url 从 agent_config 或环境变量注入
```

### 7.2 `nodes/graph_tool_node.py` — LangGraph 执行节点

```
职责：
  - 从 AgentState 提取 graph 调用参数
  - 调用 get_graph_tool().invoke()
  - 将结果写入 state（graph_result / graph_quality_score / node_timings）

参数提取策略（仿 rest_tool_node）：
  - 优先使用 PlanStep 参数（复杂任务场景）
  - 否则从 route_decision.metadata 中提取（简单路由场景）

输出写入（仿 rest_tool_node L113-126）：
  - graph_result: {answer, rows, cypher_query, planner, query_stats, ...}
  - graph_quality_score: 0.0~1.0
  - agent_iteration_count: +1
  - node_timings: {"graph_tool": elapsed_ms}
```

---

## 8. 完整状态流转图

```
question_input
    │
    ▼
intent_router ──────────┬── clarification ──→ intent_router (re-route)
    │                    │
    │ route_decision()   ├── rag_tool ──┬── db_tool (hybrid) ──┐
    │                    │              └── evidence_fusion ◄──┘
    │                    │
    │                    ├── db_tool ──→ evidence_fusion
    │                    │
    │                    ├── react_subgraph ──→ evidence_fusion
    │                    │
    │                    ├── rest_tool ──→ evidence_fusion
    │                    │
    │                    ├── graph_tool ──→ evidence_fusion    ★ 新增
    │                    │
    │                    ├── plan_executor ──→ evidence_fusion
    │                    │
    │                    └── prompt_assembly (chitchat)
    │
    ▼
evidence_fusion ──→ reflection ──→ quality_check
                                        │
                      ┌─────────────────┼──────────────────┐
                      ▼                 ▼                  ▼
               prompt_assembly    rag_tool/db_tool/    fallback
                  (pass)         web_tool/graph_tool
                                      (retry)
```

---

## 9. 路由策略

### 9.1 用户侧路由触发方式

| 优先级 | 触发方式 | 示例 |
|--------|---------|------|
| 最高 | `@graph` 精确指令 | `@graph 张三参加了哪些会议` |
| 高 | RuleRouter 关键词命中 | 用户问题含"关系图谱""知识图谱""实体关联""图查询"等 |
| 中 | LLMRouter 语义判断 | LLM 判断问题适合图查询（关系推理、多跳路径） |
| 低 | Planner 拆解 | 复杂任务拆分出 graph 子步骤 |

### 9.2 与现有 tool 的路由共处

- `graph` 与 `rag` 互斥：`route_target` 只能是一个值，不会同时走两个 tool
- `graph` 与 `hybrid` 互斥：hybrid 是 RAG+DB 串行，不涉及 graph
- `graph` 可与 `rag`/`db` 在 PlanExecutor 中并行：Planner 拆解为多步骤时，不同步骤可分配不同 tool

---

## 10. 文件改动汇总

```
data-knowledge-api/agent/langgraph/
├── state.py                      # 修改: +graph route_target +graph 输出字段
├── graph.py                      # 修改: +import +add_node +add_edge
├── routers/
│   ├── models.py                 # 修改: +Literal "graph" ×4
│   └── pre_filter.py             # 修改: +@graph directive
├── nodes/
│   ├── intent_router.py          # 修改: +route_decision 分支
│   ├── graph_tool_node.py        # ★ 新增
│   ├── evidence_fusion.py        # 修改: +graph 标准化 +graph 配额
│   └── quality_check.py          # 修改: +retry_graph 分支
└── tools/
    └── graph_tool.py             # ★ 新增

ontology-other/ontology_v2_formal/
└── graph_qa_http/
    └── app.py                    # ★ 新增: FastAPI HTTP 包装服务
```

---

## 11. 实施顺序建议

| 阶段 | 内容 | 依赖 |
|------|------|------|
| 1 | ontology 侧：`graph_qa_http/app.py` HTTP 包装服务 | — |
| 2 | api 侧：`tools/graph_tool.py` GraphTool 类 | 阶段 1（服务可用） |
| 3 | api 侧：`state.py` + `models.py` 数据模型变更 | 阶段 2 |
| 4 | api 侧：`nodes/graph_tool_node.py` 执行节点 | 阶段 3 |
| 5 | api 侧：`graph.py` 节点注册 + 边连接 | 阶段 4 |
| 6 | api 侧：`intent_router.py` + `pre_filter.py` 路由接入 | 阶段 5 |
| 7 | api 侧：`evidence_fusion.py` + `quality_check.py` 融合与质量 | 阶段 5 |
| 8 | 端到端集成测试 | 全部 |

阶段 2-7 可在 api 侧并行开发（mock graph 服务），阶段 8 需要 ontology 服务就绪。