# SQL Agent 渐进式披露（业界标准流程）落地设计

> **设计目标**：DB 查询能力从「单一 DatabaseTool 内部流水线」全面改造为业界标准的 **Agent 驱动 + 细粒度工具** 模式（LangChain/LangGraph SQL Agent 范式）
> **前置共识**：以查询质量为最高优先级，改造成本不设硬约束
> **替代关系**：本文档 v2.0 全量替代 v1.0（单工具内闭环 LLM 化方案）
> **版本**：v2.0

---

## 1. 背景与目标

### 1.1 业界标准流程（对齐目标）

以 LangChain / LangGraph 官方 SQL Agent 教程为范式的渐进式披露：

```text
第 1 步：Agent 接收任务，System Prompt 只含极简工具描述（低 Token）
第 2 步：Agent 调用 list_tables → 获得精简表名清单
第 3 步：Agent 结合问题判断相关表，调用 describe_table(table_name) → 仅返回该表 schema
第 4 步：Agent 基于已披露 schema 生成 SQL，调用 query_executor 执行
第 5 步：执行报错 → Agent 可再次 describe_table 确认字段 → 修正 SQL 重试（自愈）
```

本质特征：**披露节奏由 Agent LLM 按推理需要自主决定**，工具只提供原子能力，不预设流程。

### 1.2 现状与差距

当前 `agent/langgraph/tools/database_tool.py` 是**单一粗粒度工具**：一次 `invoke()` 内部完成 选库→筛表→写SQL→执行→自愈 全流程，上游只看到一个黑盒结果。差距：

| 维度 | 现状 | 业界标准 |
|------|------|---------|
| 流程编排 | 代码写死的流水线 | Agent LLM 自主决策 |
| 披露粒度 | 工具内一次性筛 Top-N | 按需逐表披露 |
| 探索自由度 | 预设决策点（换表/补表需代码支持） | 任意回溯、交错、补充探查 |
| 自愈方式 | 限次改 SQL | Agent 自主分析错误、重探 schema、修正重试 |

### 1.3 目标

```text
DB 查询 = 一个有界 SQL Agent 子图（LangGraph StateGraph）
Agent 持 4 个细粒度工具，自主完成渐进式披露与查询
子图对外仍输出标准 db_result（契约不变），对内全链路可审计、有预算硬约束、有安全护栏
```

### 1.4 非目标

- 不改动 DataSkill 结构化查询路径（`tools/database/`，受治理确定性路径，优先级仍最高）
- 不改动 MCP Server 协议（`list_tables_{db}` / `describe_table_{db}` / `query_{db}` 不变）
- 不改变 RAG / Web 工具架构（本期仅 DB 域）
- 不删除 TemplateMatcher 模板短路（确定性最快路径保留为前置检查）

---

## 2. 总体架构

### 2.1 分层结构

```text
┌─────────────────────────────────────────────────────────────┐
│ LangGraph 主干 / Plan Executor / 受限 ReAct 子图             │
│   （调用方零感知：仍是一次"DB 查询"动作，返回 db_result）      │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ SQLAgentSubgraph（有界 ReAct 循环，LangGraph StateGraph）     │
│                                                             │
│   agent_llm_node ──有工具调用──▶ tool_node ──观察回传──▶ agent_llm_node │
│        │                                                    │
│        └─无工具调用（结束/give_up）──▶ finalize_node          │
│                                                             │
│   预算护栏：BudgetGuard（每一步前置检查，超限强制 finalize）    │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 4 个细粒度工具（薄封装，各自带独立护栏）                        │
│   DbListDatabasesTool    列出可见库（db_id + description）    │
│   DbListTablesTool       列出指定库的表白名单（表名+注释）      │
│   DbDescribeTableTool    查看指定表 schema（字段+类型+注释）    │
│   DbExecuteSqlTool       执行只读 SQL（AST 校验+行数限制）      │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ DbRuntime（共享原子层，从现有 DatabaseTool 抽取）              │
│   MCP 会话管理 / 租户库配置加载 / SQL 校验 / 结果格式化        │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
                    MCP DB Server（不变）
```

### 2.2 关键设计决策

| 决策 | 结论 | 理由 |
|------|------|------|
| Agent 循环放哪 | 独立 LangGraph 子图（`SQLAgentSubgraph`），非主干节点内手写 while 循环 | 复用 LangGraph 状态管理/checkpoint/流式能力；与主干同构，可观测体系一致 |
| 对外契约 | 子图整体仍是一次 DB 调用，输出标准 `db_result` | `db_tool_node` / `ToolDispatcher` / ReAct executor **零契约改动**，只换内部实现 |
| 模板路径 | 进入子图前先跑 TemplateMatcher，命中则跳过 Agent（路径分类见 §2.3） | 高频确定性查询零 LLM 开销，质量/延迟双优 |
| 启发式探索规则流水线（旧） | 标记 deprecated，保留一个版本作为 fallback 与 A/B 基线（路径分类见 §2.3） | 应急回滚能力 + 质量对比基线 |
| 选库 | 新增 `DbListDatabasesTool`，Agent 自主选库（不再关键词路由） | 完全对齐业界模式；`db_id` 已由调用方指定时该工具不注册（跳过选库） |

### 2.3 路径分类与决策总序

#### 术语定义：两类"规则"必须区分

本设计中被"废弃"和被"保留"的机制同为规则形态，性质却根本不同。处置依据是其与 SQL Agent 的关系——**竞争则废弃，互补则保留**：

| 分类 | 定义 | 包含组件 | 与 SQL Agent 关系 | 处置 |
|------|------|---------|------------------|------|
| **确定性知识路径** | 答案预置，经人工验证（TemplateMatcher）或治理审批（DataSkill），命中即绕过探索 | DataSkill 结构化 query_template、TemplateMatcher 问法模板 | **互补**：不做探索，直接短路 | 保留，优先级高于 Agent |
| **启发式探索规则** | 用关键词/正则近似 Agent 的探索决策职能 | `_route_by_intent`（关键词选库）、`_filter_relevant_tables`（关键词筛表）、`_generate_rule_sql`（规则拼 SQL） | **竞争**：与 Agent 干同一件事但质量更差 | deprecated，一个版本后删除 |

> 一句话：**废弃的不是"规则"，而是"用规则模拟 Agent 探索"的部分；保留的不是"规则"，而是"用预置知识免除探索"的部分。** 本文后述"旧规则流水线"均特指启发式探索规则流水线。

#### 决策总序（全链路唯一权威定义）

```text
请求进入 DB 域
 │
 ├─ ① DataSkill 结构化路径（ToolDispatcher 层判断，最高优先级）
 │    条件：data_skill_id + query_template_id 齐全
 │    未命中模板 → 按 DataSkill.fallback_mode：sql_agent（注入 hints）/ reject
 │
 ├─ ② TemplateMatcher 短路（SqlAgentRunner 内部，Agent 前最后一道确定性闸门）
 │    命中 → 渲染模板 SQL 直接执行（source=template）
 │
 ├─ ③ SQLAgentSubgraph（默认探索路径）
 │
 └─ ④ legacy 启发式规则流水线（仅 exploration_mode=legacy 应急回滚 / A/B 基线）
```

| 优先级 | 路径 | 判断发生层 | 说明 |
|--------|------|-----------|------|
| ① | DataSkill 结构化 | ToolDispatcher | 治理等级最高；`db_tool_node` 直连路径无此判断 |
| ② | TemplateMatcher | SqlAgentRunner 入口 | 仅在无 ① 判断的路径（如 `db_tool_node` 直连）上才是"第一道闸" |
| ③ | SQL Agent | SqlAgentRunner | 默认探索路径 |
| ④ | legacy | SqlAgentRunner（配置切换） | 不参与正常优先级，仅应急回滚 |

### 2.4 一次完整执行的时序

```text
调用方 invoke(query, db_id?, tenant_id, ...)
  │
  ├─ P0 TemplateMatcher 命中？ → 直接执行模板 SQL → 返回（source=template）
  │
  └─ 进入 SQLAgentSubgraph
       │
       │  [System Prompt：角色 + 工具极简描述 + 业务域提示（可注入 DataSkill hints）]
       │
       ├─ Agent → db_list_databases（db_id 未指定时）→ 观察库清单 → 选定 db_id
       ├─ Agent → db_list_tables(db_id) → 观察表清单（含注释）→ 判断相关表
       ├─ Agent → db_describe_table(db_id, table)×N → 观察 schema
       ├─ Agent → db_execute_sql(db_id, sql) → 观察结果
       │     ├─ 报错 → Agent 分析错误 → 重探 schema / 修正 SQL → 重试（受预算限制）
       │     ├─ 空结果 → Agent 判断：放宽条件 / 换表 / 确认无数据
       │     └─ 有结果 → Agent 判断数据足以回答 → finalize
       │
       └─ finalize：汇总最终 SQL/结果/轨迹 → 标准 db_result（source=sql_agent）
```

---

## 3. 组件详设

### 3.1 DbRuntime（共享原子层）

从现有 `DatabaseTool` 抽取，**保持被 `DatabaseToolStructuredBackend` 依赖的方法签名不变**：

```python
class DbRuntime:
    def init_session(self, tenant_id: str, mcp_server_name: str) -> None
        # 原 _init_mcp_session，签名不变
    def list_databases(self) -> list[dict]   # [{db_id, description}]，来自租户配置
    def list_tables(self, db_id: str) -> list[dict]
        # 原 _list_tables，[{table_name, comment}]，仅返回 allowedTables 白名单
    def describe_table(self, db_id: str, table_name: str) -> dict
        # 原 _describe_table，{columns: [{name, type, comment}]}
    def execute_sql(self, db_id: str, sql: str) -> list[dict]
        # 原 _execute_sql，经校验的只读执行
    def validate_sql(self, sql: str) -> tuple[bool, str]
        # 升级：现有黑名单校验 + sqlglot AST 解析（对齐《受限ReAct子图落地设计》§6.2）
    @staticmethod
    def format_rows(rows: list[dict], sql: str, source: str) -> str
        # 原 format_rows 静态方法，不变
```

会话内缓存：`list_tables` 结果按 db_id 缓存（同请求内重复调用直接命中缓存，仍计工具调用次数但不重复打 MCP）。

### 3.2 四个细粒度工具

每个工具 = 薄封装 + 独立护栏。输入输出均为 JSON 字符串（LangGraph Tool 标准）。

| 工具 | 输入 | 输出（截断后） | 护栏 |
|------|------|---------------|------|
| `db_list_databases` | `{}` | `[{db_id, description}]` | 仅返回租户配置的库；`db_id` 已指定时不注册此工具 |
| `db_list_tables` | `{db_id}` | `[{table_name, comment}]`（≤200 条，超出截断并提示） | db_id ∈ 租户库集合；仅白名单表 |
| `db_describe_table` | `{db_id, table_name}` | `{columns: [...], comment}` | table_name ∈ list_tables 已返回集合（防幻觉表名）；缓存命中直接返回 |
| `db_execute_sql` | `{db_id, sql}` | `{rows, row_count, truncated}` 或 `{error, error_code}` | ① AST 解析仅允许 SELECT；② 黑名单关键字；③ 表名 ∈ 白名单；④ 强制 LIMIT（默认 500）；⑤ 单语句超时；⑥ 只读账号（DB 侧） |

工具错误**不抛异常**，返回结构化错误对象作为 Observation，让 Agent 基于错误自愈（业界模式的核心：错误信息本身就是自愈的输入）。

### 3.3 SQLAgentSubgraph

#### 子图状态

```python
class SqlAgentState(TypedDict, total=False):
    messages: list          # LangGraph add_messages 归并，Agent 推理轨迹
    db_id: str              # 当前选定的库（可被 Agent 更新）
    query: str              # 原始问题（简体化后）
    query_lang: str
    step_count: int
    llm_call_count: int
    sql_exec_count: int
    describe_count: int
    final_sql: str
    final_rows: list[dict]
    exploration_verdict: str  # data_found / no_data_confirmed / exploration_failed / budget_exhausted
    give_up_reason: str
    step_trace: list[dict]  # [{step, tool, args_summary, result_summary, latency_ms}]
```

#### 节点与边

```text
__start__ → budget_guard → agent_llm → (has_tool_calls?) 
                                      ├─ yes → tool_exec → budget_guard（循环）
                                      └─ no  → finalize → __end__
```

- `budget_guard`：每一步前置检查，任一预算耗尽 → 强制跳转 `finalize`（verdict=budget_exhausted）
- `agent_llm`：ChatModel `bind_tools(4个工具)`，temperature=0.1
- `tool_exec`：分发到对应工具，结果以 ToolMessage 回传；同步更新计数器与 step_trace
- `finalize`：从轨迹提取最终 SQL 与结果，构造 verdict

#### 预算体系（硬约束）

| 预算项 | 默认值 | 说明 |
|--------|--------|------|
| `max_agent_steps` | 10 | Agent 循环总步数（含工具调用步） |
| `max_llm_calls` | 10 | LLM 调用次数 |
| `max_sql_executions` | 4 | SQL 执行次数（含失败重试） |
| `max_describe_calls` | 6 | describe 次数（缓存命中不计） |
| `max_wall_time_sec` | 90 | 墙钟超时 |
| `max_result_rows` | 500 | 单次执行返回行数 |

典型 happy path：选库 1 + list 1 + describe 1~2 + 生成执行 1 = 4~5 次 LLM 调用，远低于上限；复杂自愈场景收敛在 8~10 次。

#### 收敛与 verdict

| 终态 | 条件 | verdict |
|------|------|---------|
| Agent 认为结果充分，结束循环 | 最后一次 execute 有行 | `data_found` |
| Agent 判断库中无相关数据（探查后确认） | Agent 显式声明 give_up(reason=no_data) | `no_data_confirmed` |
| SQL 持续失败且 Agent 放弃 | Agent 显式声明 give_up(reason=error) | `exploration_failed` |
| 任一预算耗尽 | budget_guard 强制收敛 | `budget_exhausted` |

#### System Prompt 骨架

```text
你是数据查询专家。通过调用工具逐步探查数据库并回答数据问题。
规则：
1. 先用 db_list_tables 了解可用表，再用 db_describe_table 查看目标表结构，不要猜测表名和字段名
2. 只生成只读 SELECT 查询
3. 执行失败时分析错误信息，必要时重新确认表结构后修正重试
4. 结果为空时考虑：放宽过滤条件 / 更换关联表；确认无数据则明确说明
5. 得到足以回答问题的数据后停止调用工具，输出最终结论
[业务域提示注入点：DataSkill exploration_hints / 库 description / 指标口径]
```

### 3.4 模板前置短路（保留）

`TemplateMatcher.match(query)` 命中 → 走现有模板 SQL 渲染执行，`source=template` 直接返回，不进入子图。模板库随运营积累扩大。它是 **Agent 前的最后一道确定性短路**（全链路优先级 ②，见 §2.3），也是抑制 LLM 成本的关键闸门。

---

## 4. 对外契约与集成点

### 4.1 db_result 契约（只增不改）

```python
# 保留字段：success / sql / rows / row_count / source / db_id / tables /
#          formatted_result / quality_score / schema_discovery_log / error / error_code
# 新增字段：
exploration_verdict: str        # data_found / no_data_confirmed / exploration_failed / budget_exhausted
exploration_stats: dict         # {llm_calls, agent_steps, sql_execs, describe_calls, wall_time_ms}
step_trace: list[dict]          # Agent 步骤轨迹（供审计/前端时间线）
```

`source` 枚举扩展：`"template" | "sql_agent" | "nl_to_sql"(deprecated) `。

### 4.2 集成点改动

| 集成点 | 改动 |
|--------|------|
| `nodes/db_tool_node.py` | 实现替换：调 `SqlAgentRunner.invoke()`（内部含模板短路+子图），节点接口不变 |
| `executor/tool_dispatcher.py::_execute_database` | 同上；结构化路径（data_skill_id + query_template_id）优先级不变 |
| `react/executor.py::_call_db_tool` | 同上。一次 ReAct `db_query` action = 一次完整 SQL Agent 运行；`exploration_stats.sql_execs` 回写累加 `react_state.db_query_count` |
| `DatabaseToolStructuredBackend` | 依赖的私有方法迁移到 DbRuntime 后保持签名，StructuredBackend 改为委托 DbRuntime（行为不变，测试回归保证） |

### 4.3 旧实现处置

| 组件 | 处置 |
|------|------|
| `DatabaseTool.invoke()` 启发式探索规则流水线 | `@deprecated`，经 `exploration_mode=legacy` 配置启用，保留 1 个版本后删除 |
| `_route_by_intent` / `_filter_relevant_tables` / `_generate_rule_sql` | 启发式探索规则本体，随 legacy 保留（A/B 基线），不再演进 |
| `TemplateMatcher` | **保留并前置**，是唯一跨新旧共用的组件 |
| 现有 `test_database_tool.py` 9 个测试 | 改为针对 legacy 模式的回归测试，继续通过 |

---

## 5. 影响范围分析

### 5.1 完整主流程

```text
question_input → intent_router → [rag | database | hybrid] → quality_check
→ prompt_assembly → llm_generate → hallucination → answer_output
```

| 环节 | 影响 |
|------|------|
| intent_router | 不变。database/hybrid 路由决策不受影响 |
| database 节点 | 内部实现替换，节点延迟上升（happy path +3~5 次 LLM，约 5~10s）。**需复核**：API 网关超时、前端 SSE 超时、`node_timings` 监控阈值 |
| hybrid 模式 | 不变（RAG 与 DB 并行/串行编排不受影响） |
| quality_check | **需适配 verdict 语义**（见 5.2） |
| prompt_assembly | 不变（消费 `db_result.formatted_result`） |
| hallucination | 不变（数值/事实一致性校验基于 rows）；sql_agent 来源的 claims 治理见 5.4 |

### 5.2 quality_check 适配

空结果语义分化，消除无意义重试：

| exploration_verdict | quality_decision |
|---------------------|------------------|
| `data_found` | pass |
| `no_data_confirmed` | pass（prompt 层生成"未查询到数据"话术），可配置降级 web |
| `exploration_failed` | retry_db（受 retry_count 限制） |
| `budget_exhausted` | fallback_web 或保守回答，不再重试 |

### 5.3 Skill 评估方案（skills/）变更

这是本次改造对治理体系的主要影响面：

| 点 | 变更 |
|----|------|
| SkillResolver 选择逻辑 | **不变**。DataSkill 命中 + query_template 匹配 → 结构化路径（最高优先级，确定性受治理） |
| DataSkill 新增可选字段 `exploration_hints` | `{preferred_tables, metric_bindings, table_aliases, business_glossary}`：当无匹配 query_template 且允许探索时，注入 SQL Agent System Prompt 业务域提示注入点。**这是 skill 体系与 SQL Agent 的正式协作接口**——把受治理的业务知识（指标口径、表别名）以提示形式赋能 Agent，提升选表与 SQL 质量 |
| DataSkill 新增 `fallback_mode` | `"sql_agent" | "reject"`（默认 `reject`）：有 DataSkill 但无匹配模板时，是否允许降级到 SQL Agent 探索 |
| 治理等级差异显性化 | 结构化路径 SQL 来自受治理模板（确定性）；SQL Agent SQL 是运行时生成（非确定性）。`db_result.source=sql_agent` 的证据进入治理差异化流程 |
| `config/report_governance.yaml` | 新增规则：source=sql_agent 的 DB 证据 → authority_score 降权（0.95→0.75）且默认 `needs_human_review=True`；source=template/structured 维持现状 |
| 权限 | 天然受限：Agent 只能看到 `db_list_tables` 白名单内的表，describe/execute 均二次校验，无法越权 |

### 5.4 报告与 Evidence 链路

| 点 | 变更 |
|----|------|
| `tool_dispatcher._execute_report` DB evidence 构造 | confidence 计算：sql_agent 来源 ×0.8 系数（代码层落实降权） |
| `evidence/provenance.py` | provenance 附加 `step_trace` 摘要（选库/选表/SQL 演化链），血缘从"一条 SQL"升级为"完整探索轨迹"，可解释性增强 |
| `report/claims.py` / `verifier.py` | 逻辑不变；sql_agent 来源 claims 按 5.3 降权治理 |

### 5.5 ReAct 子图

| 点 | 变更 |
|----|------|
| 本期 | ReAct `db_query` action = 一次完整 SQL Agent 运行（黑盒升级为更聪明的黑盒），`exploration_stats` 回写预算 |
| 后续演进（不在本期） | 4 个细粒度工具可直接注册进 ReAct 工具集，由 ReAct LLM 亲自做探索（分层混合的终极形态）。届时 SQL Agent 子图退化为 DAG 主干专用。需在《受限ReAct子图落地设计》修订：PlanStep 白名单扩 3 个 db 原子类型、预算模型细化、INVALID_SQL 处理约定上移至 ReAct 推理层 |

### 5.6 状态（state.py）

不新增主干字段。`db_result` 内增 3 字段（4.1）；ReAct 场景 `react_state.db_query_count` 由 exploration_stats 回写。

### 5.7 配置

| 文件 | 变更 |
|------|------|
| `conf/sql_agent.yaml`（新增） | 预算参数、System Prompt 模板、模型选择（支持专用低成本 exploration 模型）、截断阈值 |
| `agent_config.database_config` | 新增 `exploration_mode: sql_agent | legacy`（默认 sql_agent）、预算覆盖入口 |
| `config/report_governance.yaml` | sql_agent 来源治理规则 |
| DataSkill schema | `exploration_hints` / `fallback_mode` 可选字段（validator 放宽，向后兼容） |

### 5.8 测试

| 类别 | 内容 |
|------|------|
| 保留 | legacy 模式 9 个测试（回归基线）；结构化路径全部测试（DbRuntime 委托后行为不变） |
| 新增 DbRuntime 测试 | 抽取后方法签名/语义回归（含 StructuredBackend 契约） |
| 新增工具级测试 | 4 个工具各自的护栏：幻觉表名拒绝、AST 拦截非 SELECT、LIMIT 注入、截断 |
| 新增子图测试（mock LLM） | happy path 轨迹；自愈轨迹（首次 SQL 报错→describe→修正成功）；空结果三分支；预算耗尽收敛；give_up 两分支；模板短路跳过子图 |
| 新增集成测试 | db_tool_node / tool_dispatcher 走 sql_agent 模式的端到端（mock MCP + mock LLM） |
| quality_check | 四种 verdict 分支测试 |
| 评估集 | 同一业务问题集跑 legacy vs sql_agent，对比 SQL 正确率/答案正确率/平均延迟/平均 LLM 成本，作为全量切换依据 |

### 5.9 前端与可观测

| 点 | 变更 |
|----|------|
| 前端 | `step_trace` 可渲染为「查询探索过程」时间线（复用 web-new tool-call-card），**非本期必须** |
| 指标 | `sql_agent_llm_calls` / `sql_agent_steps` / `sql_agent_sql_execs` / `sql_agent_verdict_total{verdict}` / `sql_agent_latency_p99` / `sql_agent_fallback_to_legacy_total` |
| 日志 | 每步 step_trace 落审计日志（含 args 摘要 + 结果摘要，脱敏后） |

---

## 6. 落地分期

### Phase 0：DbRuntime 抽取（纯重构，零行为变化）

- 从 `DatabaseTool` 抽取 DbRuntime；`DatabaseToolStructuredBackend` 改委托
- SQL 校验升级 sqlglot AST（对齐 ReAct 设计 §6.2）
- 验收：现有全部测试（legacy + structured）100% 通过

### Phase 1：SQLAgentSubgraph 主体

- 4 个细粒度工具 + 护栏
- 子图（agent_llm / tool_exec / budget_guard / finalize）+ 预算体系 + verdict
- 模板前置短路接入
- `db_tool_node` 背后切换（`exploration_mode=sql_agent` 灰度开关，默认 legacy）
- 验收：mock 子图测试全绿；灰度租户 happy path 成功率 ≥ legacy

### Phase 2：全链路切换 + quality_check 适配

- tool_dispatcher / react executor 切换到 SqlAgentRunner
- verdict 接入 quality_check 四种分支
- exploration_stats 回写 ReAct 预算
- 验收：集成测试全绿；空结果误重试率下降；预算耗尽 100% 收敛

### Phase 3：Skill 治理打通

- DataSkill `exploration_hints` 注入 + `fallback_mode`
- 报告治理降权规则（governance yaml + dispatcher confidence 系数）
- provenance step_trace 透出
- 验收：sql_agent 证据默认进入 human_review；hints 注入后选表准确率提升（评估集验证）

### Phase 4：评估与退役

- 评估集 A/B 报告（质量/延迟/成本）
- sql_agent 设为默认模式；legacy 标记删除计划（保留一个版本周期）
- （可选）前端 step_trace 时间线
- （后续独立立项）细粒度工具注册进 ReAct，修订《受限ReAct子图落地设计》

---

## 7. 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| Agent 不收敛/绕圈 | 高 | 五重预算硬约束 + budget_guard 强制 finalize；System Prompt 明确"够用即停" |
| 延迟上升 5~10s | 中 | 质量优先已确认；模板短路拦截高频查询；P99 监控 + 超时配置复核 |
| LLM 成本上升 | 中 | max_llm_calls=10 硬顶；支持配置低成本专用模型；模板路径零成本 |
| 幻觉表名/字段名 | 高 | describe/execute 双表白名单校验；Prompt 明示"不要猜，先 describe" |
| SQL 安全 | 高 | AST 仅 SELECT + 黑名单 + 强制 LIMIT + 只读账号 + 超时，五层防护 |
| 结果不稳定（同问不同轨） | 中 | step_trace 全审计；temperature=0.1；发布级报告走结构化路径或 human_review |
| 新旧切换回归 | 中 | legacy 模式保留一个版本；评估集 A/B 通过后才设默认 |
| ReAct 预算被探查耗尽 | 低 | exploration_stats 回写；后续细粒度暴露时设 db 探查独立子预算 |

---

## 8. 决策记录

| 决策 | 结论 |
|------|------|
| 架构范式 | 全面转向业界标准 Agent 驱动细粒度工具模式，不做单工具内闭环 |
| Agent 循环载体 | LangGraph 子图（非手写 while），与主干同构 |
| 对外契约 | 只增不改，调用方零契约改动 |
| 选库方式 | `db_list_databases` 工具由 Agent 自主选择，废弃关键词路由 |
| 路径分类 | 确定性知识路径（保留，优先级高于 Agent）与启发式探索规则（废弃）严格区分，详见 §2.3 |
| 决策总序 | structured ① > template ② > sql_agent ③，legacy 仅应急回滚，详见 §2.3 |
| 模板路径 | 保留并前置，作为 Agent 前最后一道确定性短路（优先级 ②） |
| 启发式探索规则流水线 | deprecated → 一个版本后删除；期间作 A/B 基线 |
| DataSkill 结构化路径 | 不动，优先级最高（①）；通过 exploration_hints/fallback_mode 与 SQL Agent 协作 |
| SQL 校验 | 升级为 sqlglot AST（对齐 ReAct 设计 §6.2 既有要求） |
| ReAct 细粒度暴露 | 后续独立立项，需同步修订《受限ReAct子图落地设计》PlanStep 白名单与预算模型 |
