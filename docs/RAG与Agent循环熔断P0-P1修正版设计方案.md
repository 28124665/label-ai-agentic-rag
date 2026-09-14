# Top-K 动态截断与 Agent 循环控制 P0/P1 修正版设计方案

> 版本：v2.0
>
> 状态：基于已评审方案的修订版，设计方案，未实施代码修改
>
> 适用范围：LangGraph 主流程、RAG 检索、Planner 工具执行、ReAct 子图
>
> 修订基线：保留“候选 Top-20 + 保底数量 + 动态截断”和“最大迭代次数 + 连续重复动作/持续降分提前终止”的已评审方案；本版本仅修正其在分数口径、状态归属、跨层路由和误杀控制方面的 P0/P1 问题。

## 1. 背景与目标

此前评审通过的核心方案包括：

- 检索阶段固定获取 Top-20 候选，Rerank 后依据分数动态截断，同时保留最少结果数；
- Agent 设置 `max_iterations=8`（可配置为 10）；
- 连续 3 步调用同一工具且实际参数不变时提前终止；
- Rerank 分数连续两次下降时提前终止；
- 在 State 中维护步数计数器和连续原地踏步检测器；
- 触发后停止继续行动，必要时输出“当前信息不足以回答，建议人工介入”。

结合当前代码评估后，原方案不能直接落地，主要原因是：

- RAGTool、结果聚合器和 Planner 路径对文档分数的使用口径不一致；
- ReAct 子图的终止结果没有完整接入主图；
- 主图没有统一的 Agent 全局迭代预算和终止语义；
- 相同工具重复调用和 Rerank 分数下降没有可靠的跨轮状态；
- 直接 RAG、Planner RAG、ReAct RAG 的结果元数据没有完全统一。

本方案的目标是：

1. 统一检索候选、Rerank、动态截断和质量评估的数据契约；
2. 统一主图与 ReAct 子图的预算和终止语义；
3. 在不误杀正常多轮检索的前提下，识别无效重复行动和无收益检索；
4. 保留已有证据时优先生成保守答案；证据不足时输出统一兜底提示；
5. 支持灰度发布、指标观测和快速回滚。

## 2. 当前实现与问题定位

### 2.1 相关代码边界

| 模块 | 当前职责 | 修正版要求 |
| --- | --- | --- |
| `RAGTool` | 召回、重写、Rerank、质量评估、文档格式化 | 统一分数解析，固定候选上限，动态截断并输出检索元数据 |
| `ResultAggregator` | 聚合 Planner 多步骤工具结果 | 使用同一分数函数排序、去重、截断并透传 `score_source` |
| `ToolDispatcher` | Planner 的 RAG/DB/Web 工具分发 | 补齐 RAG 质量元数据和实际检索参数 |
| `ReactSubgraph` | ReAct Think-Act-Observe 循环 | 在子图内执行动作签名、停滞检测和趋势检测 |
| `react_subgraph_node` | 将 ReAct 结果写回主图 | 把终止信息转换成主图可消费的状态字段 |
| `quality_check` | 根据证据质量决定通过、重试或降级 | 继续负责质量决策，不负责还原动作历史 |
| `graph.py` | LangGraph 主流程和条件边 | 增加统一终止路由和兜底节点 |

### 2.2 P0 问题

#### P0-1：分数口径不一致

`RAGTool._evaluate_quality()` 当前优先读取：

```text
rerank_score → similarity → score
```

但 `ResultAggregator` 当前固定按照 `score` 排序并计算 `rag_top_score`。由于 `_format_docs()` 会把不同阶段的分数写入输出字段，直接 RAG 和 Planner RAG 可能产生不同的排序和质量判断。

**风险：** 同一批文档在不同入口得到不同质量结果，导致错误重试、错误截断或错误 Fallback。

**修正：** 建立唯一的文档相关性分数函数，所有排序、截断、质量评估和趋势检测均使用规范化后的 `relevance_score`。

#### P0-2：Planner 路径元数据丢失

`ToolDispatcher._execute_rag()` 和技能 RAG 路径目前主要透传文档、质量分、相关文档数和最高分，但未完整透传 `score_source`、动态截断结果及查询签名等元数据。

**风险：** Planner 聚合后无法判断分数来源，也无法准确解释结果是如何截断和生成的。

**修正：** 扩展统一 `RAGResultMetadata` 契约，并要求直接 RAG、Planner RAG、技能 RAG、ReAct RAG 均返回同一组字段。

#### P0-3：终止状态没有完整接入主图

当前主图 `quality_check` 只路由到 `prompt_assembly`、重试工具或 `web_tool`，没有 `force_terminate` 或统一终止节点。ReAct 返回 `budget_exhausted` 等结果后，也没有在主图中形成明确的终止路由。

**风险：** 子图已经停止，但主图仍可能继续质量重试或重新行动，形成跨层循环。

**修正：** 增加统一 `termination_router`（或等价的终止节点），所有主动终止信号先经过该节点，再根据已有证据量选择“保守生成”或“直接兜底”。

#### P0-4：没有可靠的跨轮 Rerank 历史

当前没有在 `AgentState` 或 ReAct 状态中保存每轮检索的最高分、质量分、证据签名和查询签名，因此无法可靠判断“Rerank 分数连续两次下降”。

**风险：** 使用当前轮局部字段判断趋势，容易把一次低分结果误判为连续下降。

**修正：** 每次实际 RAG 工具执行完成后，统一追加一条 `RetrievalObservation`，趋势检测只基于同一请求内按时间顺序排列的有效观测。

#### P0-5：动作重复检测没有规范化签名

ReAct 的 `action_history` 保存 LLM 原始参数，未必等于工具实际执行时经过默认值补全、参数覆盖和规范化后的参数。

**风险：** 同义参数顺序不同会漏检；原始参数相同但实际查询上下文不同又可能误检。

**修正：** 在工具执行前生成“实际动作签名”，至少包含工具类型、规范化参数、知识库/数据库范围和查询版本；只对实际执行成功或明确被策略拒绝的动作计入重复检测。

### 2.3 P1 问题

- 主图已有 `retry_count`、`regenerate_count` 和 ReAct `max_steps`，但没有统一的 Agent 全局迭代计数。
- ReAct `max_steps=8` 已存在，不应再额外创建一套互相独立的 `max_iterations`；应将其纳入统一预算模型。
- ReAct LLM 调用当前未稳定传入真实 Token 使用量，Token 预算只能作为近似保护。
- ReAct 的预算快照可能在多轮 Prompt 中过期，模型看不到最新剩余预算。
- `rag_top_score` 在不同路径可能表示原始分、Rerank 分或格式化后的分，字段语义不稳定。
- 固定绝对阈值对不同 Rerank 模型分布不一定适用。
- “分数连续两次下降”单独触发容易误杀，应增加最小下降幅度、证据无增量或绝对质量下限等约束。
- 预算耗尽、动作停滞、检索下降、基础设施失败需要区分原因，但共享统一的后续处理协议。

## 3. 修正版总体架构

### 3.1 方案边界与核心决策

本版本对已评审方案做以下明确修订：

| 已评审规则 | 修订后的落地规则 | 修订原因 |
| --- | --- | --- |
| 固定 Top-20 | `candidate_top_k=20` 只约束候选上限；Rerank 和动态截断后才确定最终结果数 | 避免把候选数误当最终返回数 |
| 保底数量 | 默认 `min_keep=2`，仅在候选存在时补齐，不伪造结果 | 保证质量评估可识别“补齐但不相关” |
| 连续 3 步同工具同参数 | 基于实际规范化 Action Signature，且要求无证据增量 | 避免 LLM 参数格式差异和正常重复查询误判 |
| Rerank 连续两次下降 | 至少 3 次同上下文有效检索，连续两次下降且超过最小下降幅度、无证据增量 | 避免一次查询变化造成误杀 |
| `max_iterations=8/10` | 与已有 ReAct `max_steps` 合并为请求级预算；默认 8，可统一配置为 10 | 避免主图、ReAct 各自计数导致预算失控 |
| 强制终止后 Fallback | 先按已有证据分为“保守生成”和“直接兜底” | 停止行动不等于没有可用证据 |

### 3.2 检索链路

```text
原始查询
  → 查询重写/子查询拆分
  → 候选召回（candidate_top_k=20）
  → 去重与统一字段映射
  → Rerank
  → 相关性分数规范化
  → 保底数量 + 动态截断
  → 质量评估
  → EvidenceSnapshot / Evidence Fusion
```

### 3.3 Agent 链路

```text
主图工具节点 / Planner / ReAct 工具执行
  → 记录实际动作签名
  → 记录 RetrievalObservation
  → 更新 loop_guard
  → 子图或主图判断是否继续
  → 正常完成：Evidence Fusion → 生成答案
  → 主动终止：termination_router
       ├── 有足够证据：保守生成 → 幻觉检测 → 输出
       └── 证据不足：fallback_node → 输出兜底
```

### 3.4 设计原则

1. **评分与决策分离**：RAGTool 负责计算和输出标准化评分，`quality_check` 负责主流程决策。
2. **实际执行优先**：循环检测基于最终执行参数，不基于 LLM 的自然语言描述。
3. **单一预算来源**：主图和 ReAct 使用同一个请求级预算快照，局部预算只能收紧，不能突破全局预算。
4. **终止语义明确**：停止行动不等于拒绝回答；应根据已有证据决定保守生成还是直接兜底。
5. **同请求状态隔离**：动作历史、分数历史和证据签名只在单次请求内有效，不能跨请求复用。

## 4. P0 修正设计

### 4.1 统一文档分数函数

新增统一函数，建议放在 RAG 结果契约或 RAG 工具公共模块中：

```python
def get_document_relevance_score(doc: dict) -> tuple[float, str]:
    """返回规范化相关性分数及其来源。"""
```

建议优先级：

```text
rerank_score → similarity → score → 0.0
```

返回：

```text
(relevance_score, raw_score_source)
```

其中：

- `relevance_score`：用于排序、截断、相关文档统计和趋势判断；
- `raw_score_source`：用于观测，取值如 `rerank_score`、`similarity`、`score`；
- `score_source`：用于质量决策，取值统一为 `base` 或 `grader_merged`。

不得再由调用方自行使用 `doc.get("score")` 进行排序或计算 `rag_top_score`。

### 4.2 分数归一化规则

不同来源分数必须先归一化到 `[0, 1]` 再比较。建议：

1. Rerank 模型已声明输出 `[0, 1]` 时直接使用；
2. 相似度分数按当前向量检索约定转换到 `[0, 1]`；
3. 未声明范围的分数不得直接与其他来源混排，应标记为 `unknown` 并使用保守默认处理；
4. 记录 `score_calibration_version`，便于模型切换后的观测和回滚。

本期不建议在运行时根据单批数据做 Min-Max 归一化，因为会破坏跨轮趋势比较：同一真实分数在不同批次可能被映射到完全不同的值。

### 4.3 Top-20 与动态截断

#### 4.3.1 参数定义

| 参数 | 默认值 | 说明 |
| --- | ---: | --- |
| `candidate_top_k` | 20 | 召回和 Rerank 的最大候选数 |
| `min_keep` | 2 | 动态截断后的最少保留条数 |
| `max_keep` | 20 | 单次最多输出条数 |
| `relevance_threshold` | 0.50 | 绝对相关性阈值 |
| `relative_score_ratio` | 0.60 | 相对 Top-1 的最低比例 |
| `score_drop_threshold` | 0.15 | 相邻结果允许的最大分数断崖 |

`candidate_top_k` 是候选上限，不等于最终返回条数。最终结果数量由动态截断决定。

#### 4.3.2 动态截断算法

对 Rerank 后按 `relevance_score` 降序排列的结果执行：

1. 去除无法识别文档 ID 的重复文档；
2. 保留满足以下条件的文档：
   - `score >= relevance_threshold`；
   - `score >= top_score * relative_score_ratio`；
   - 与上一条相比没有超过 `score_drop_threshold` 的明显断崖；
3. 结果不足 `min_keep` 条时，补齐排序最高的前 `min_keep` 条；
4. 结果最多保留 `max_keep` 条；
5. 没有候选结果时返回空列表，不伪造保底文档。

伪代码：

```python
ranked = sort_by_relevance_score(deduplicated_docs)
selected = []
top_score = ranked[0].relevance_score if ranked else 0.0

for index, doc in enumerate(ranked):
    score = doc.relevance_score
    relative_ok = score >= top_score * relative_score_ratio
    absolute_ok = score >= relevance_threshold
    cliff_ok = index == 0 or previous_score - score <= score_drop_threshold

    if (absolute_ok and relative_ok and cliff_ok) or len(selected) < min_keep:
        selected.append(doc)

    if len(selected) >= max_keep:
        break
```

`min_keep` 只保证输出数量，不代表补齐的文档一定相关。因此质量评估必须在动态截断之后重新执行，并同时输出 `relevant_count`。

#### 4.3.3 质量评估时机

质量评估顺序固定为：

```text
Rerank → 动态截断 → _evaluate_quality()
```

不能在截断前评估、截断后直接复用旧结果，否则 `quality_score`、`relevant_count` 和最终 Evidence 数量会不一致。

### 4.4 统一 RAG 结果契约

直接 RAG、Planner RAG、技能 RAG 和 ReAct RAG 均应返回：

```python
{
    "docs": [...],
    "quality_score": 0.0,
    "has_relevant": False,
    "relevant_count": 0,
    "top_score": 0.0,
    "avg_score": 0.0,
    "result_count": 0,
    "score_source": "base",
    "raw_score_source": "rerank_score",
    "candidate_top_k": 20,
    "query_signature": "...",
    "evidence_signature": "...",
    "retrieval_mode_used": "...",
    "retrieval_error_code": "",
}
```

字段语义：

- `top_score`：动态截断后结果的最高规范化相关性分数；
- `avg_score`：动态截断后全部结果的平均规范化相关性分数；
- `quality_score`：质量评估分数，若经过 Grader 融合则 `score_source=grader_merged`；
- `result_count`：动态截断后的结果数；
- `candidate_top_k`：候选上限，不表示最终结果数；
- `query_signature`：规范化查询和检索范围的哈希；
- `evidence_signature`：结果内容和文档标识的稳定哈希。

### 4.5 Planner 聚合修正

`ResultAggregator` 应按以下顺序聚合 RAG 结果：

1. 合并成功步骤的文档；
2. 使用统一文档 ID 去重；
3. 使用 `get_document_relevance_score()` 计算规范化分数；
4. 按规范化分数降序排序；
5. 重新执行全局 `max_keep` 截断；
6. 基于最终结果重新计算 `top_score`、`avg_score`、`relevant_count`；
7. 仅当所有参与聚合的结果 `score_source` 一致时，才沿用该来源；否则标记为 `mixed`，由质量决策使用保守阈值；
8. 透传所有查询和证据签名，或生成 Planner 聚合级签名。

不同查询、不同知识库的分数是否可直接比较，必须由同一分数校准版本保证。无法保证校准一致时，不应简单取全局最高分作为质量结论，而应使用各步骤质量分的保守聚合，例如最小值或加权平均，并在观测中标记 `score_source=mixed`。

## 5. Agent 循环熔断修正设计

### 5.1 统一请求级 LoopGuard

新增请求级 `LoopGuard`，由主图初始化，ReAct 子图通过上下文引用或输入快照使用。建议状态结构：

```python
class LoopGuardState(TypedDict, total=False):
    iteration_count: int
    max_iterations: int
    action_signature: str
    same_action_count: int
    rerank_score_history: list[float]
    rerank_drop_count: int
    retrieval_observations: list[dict]
    termination_reason: str
    termination_source: str
    fallback_message: str
```

推荐默认值：

```text
max_iterations = 8
same_action_limit = 3
rerank_drop_limit = 2
min_rerank_drop = 0.05
min_evidence_gain = 1
```

`max_iterations=8` 与当前 ReAct `max_steps=8` 对齐，不新增互相独立的第二个硬上限。若业务需要设置为 10，应同时修改统一预算配置，不能只修改 ReAct 局部常量。

### 5.2 实际动作签名

动作签名在工具执行前生成，至少包含：

```text
action_type
normalized_arguments
kb_ids / db_id / mcp_server_name
query_signature
```

规范化要求：

- 字典按 key 排序；
- 列表按业务语义排序，不能无条件排序会改变查询语义的列表；
- 删除无效默认字段和非业务字段；
- 查询文本统一空白、大小写和语言规范化；
- 参数中不得包含密钥、Token 等敏感值；
- 采用稳定 JSON 序列化后计算 SHA-256。

连续相同动作的定义：

- 工具类型相同；
- 规范化参数签名相同；
- 检索范围相同；
- 中间没有产生有效新证据或查询上下文没有变化。

只要查询或知识库范围发生变化，即使工具类型相同，也不计为同一动作。

### 5.3 “连续三步原地踏步”检测

在实际工具执行完成后更新，而不是在 LLM 生成 action 后立即更新。每轮记录：

```python
{
    "iteration": 3,
    "action_signature": "sha256:...",
    "tool_name": "rag_search",
    "query_signature": "sha256:...",
    "evidence_signature": "sha256:...",
    "evidence_gain": 0,
    "rerank_top_score": 0.42,
}
```

更新逻辑：

- 当前签名与上一条相同且 `evidence_gain < min_evidence_gain`，`same_action_count += 1`；
- 否则重置 `same_action_count = 1`；
- `same_action_count >= 3` 时设置：
  - `termination_reason = "same_action_loop"`；
  - `termination_source = "loop_guard"`；
  - `fallback_message = "当前信息不足以回答，建议人工介入"`。

策略拒绝的动作可以计入安全审计，但不应与实际成功执行的动作混合统计；否则某个被拒绝的危险动作可能错误触发检索原地踏步熔断。

### 5.4 Rerank 分数下降检测

每次有效 RAG 执行完成后，追加动态截断结果的 `top_score` 到 `rerank_score_history`。

触发条件不是简单的“最近两次数值下降”，而是同时满足：

1. 至少存在连续三次有效 RAG 观测；
2. 最近两次相邻变化均低于前一次；
3. 每次下降幅度不小于 `min_rerank_drop`；
4. 最近一轮没有新增有效证据；或当前质量分低于 `absolute_quality_floor`；
5. 当前动作没有改变查询、知识库或检索策略。

满足后设置：

```text
termination_reason = rerank_declining
termination_source = loop_guard
```

如果查询已经变化或证据有明显增量，即使分数短暂下降，也不触发熔断，避免误杀探索型检索。

### 5.5 全局迭代预算

一次请求的 `iteration_count` 由以下动作统一递增：

- 主图质量重试；
- Planner 工具步骤；
- ReAct 工具步骤；
- 其他会触发外部工具或 LLM 决策的行动步骤。

`hallucination` 的答案重生成建议保留独立的 `regenerate_count`，但必须同时受请求级总预算约束。局部预算不能突破全局预算。

建议预算优先级：

```text
请求级 max_iterations
  ≥ 主图 retry_count + Planner step_count + ReAct step_count + regenerate_count
```

若当前架构无法在同一对象中共享可变预算，则由主图在进入子图前传入剩余预算，子图返回实际消耗量，主图回写并重新校验。不能只传递初始化快照。

## 6. 主图终止路由

### 6.1 统一终止决策

新增 `termination_router` 或等价的条件路由函数，输入：

- `termination_reason`；
- `termination_source`；
- `evidence` / `merged_context`；
- `rag_relevant_count`；
- `retry_count` 和全局预算；
- `retrieval_error_code`。

输出两类终态：

1. `conservative_generation`：停止继续行动，但已有证据达到最低回答条件，进入 `prompt_assembly`，提示词必须明确要求仅基于已有证据回答并声明不确定性；
2. `fallback`：证据不足、基础设施失败或循环熔断后没有可用证据，进入统一 `fallback_node`。

### 6.2 终止原因处理表

| 终止原因 | 有足够证据 | 处理 |
| --- | --- | --- |
| `same_action_loop` | 是 | 停止行动，保守生成 |
| `same_action_loop` | 否 | 统一兜底 |
| `rerank_declining` | 是 | 停止行动，保守生成 |
| `rerank_declining` | 否 | 统一兜底 |
| `budget_exhausted` | 是 | 保守生成，并记录预算耗尽 |
| `budget_exhausted` | 否 | 统一兜底 |
| `retrieval_infra_error` | 任意 | 按已有证据决定；默认兜底 |
| `policy_denied` | 任意 | 默认兜底，不自动重试同一动作 |
| `error` | 是 | 保守生成并记录错误 |
| `error` | 否 | 统一兜底 |

### 6.3 兜底回复

兜底回复固定为：

> 当前信息不足以回答，建议人工介入。

可以在外层附加可观测的原因码，但不应将内部堆栈、工具参数、模型提示词或敏感信息返回给用户。

`fallback_node` 应写入：

```text
final_answer
termination_reason
termination_source
fallback_message
```

并直接进入 `observability → answer_output`，不再回到工具节点，避免兜底路径重新触发循环。

### 6.4 ReAct 子图结果契约

扩展 `ReactExecutionResult`：

```python
{
    "success": False,
    "evidence": [...],
    "step_count": 3,
    "finish_reason": "same_action_loop",
    "termination_reason": "same_action_loop",
    "termination_source": "loop_guard",
    "last_action_signature": "sha256:...",
    "same_action_count": 3,
    "rerank_score_history": [0.72, 0.61, 0.54],
    "rerank_drop_count": 2,
    "budget_snapshot": {...},
    "can_continue": False,
    "summary": "...",
}
```

`react_subgraph_node` 写回主图时必须：

1. 合并 Evidence；
2. 回写实际步数和预算消耗；
3. 回写终止原因和来源；
4. 将 ReAct 的检索观测合并到请求级历史；
5. 不因 `success=False` 直接丢弃已有 Evidence。

## 7. AgentState 建议变更

建议新增以下字段，具体类型可在实现阶段根据现有 TypedDict 约束调整：

```python
# 请求级循环控制
agent_iteration_count: int
agent_max_iterations: int
loop_guard: dict
termination_reason: str
termination_source: str
fallback_message: str

# 检索观测
rerank_score_history: list[float]
rerank_drop_count: int
retrieval_observations: list[dict]

# RAG 统一元数据
rag_avg_score: float
rag_result_count: int
rag_raw_score_source: str
rag_query_signature: str
rag_evidence_signature: str
rag_candidate_top_k: int
```

建议保留现有 `retry_count`、`retry_token_used` 和 `regenerate_count`，用于兼容现有节点和观测；但这些字段必须受新增请求级预算约束。

## 8. 配置设计

建议在 `conf/rag_enhancement.yaml` 增加：

```yaml
retrieval:
  candidate_top_k: 20
  min_keep: 2
  max_keep: 20
  relevance_threshold: 0.50
  relative_score_ratio: 0.60
  score_drop_threshold: 0.15
  score_calibration_version: v1

loop_guard:
  enabled: true
  max_iterations: 8
  same_action_limit: 3
  rerank_drop_limit: 2
  min_rerank_drop: 0.05
  min_evidence_gain: 1
  absolute_quality_floor: 0.30
  fallback_message: 当前信息不足以回答，建议人工介入

termination:
  conservative_generation_min_relevant_docs: 1
  direct_fallback_on_infra_error: true
```

配置约束：

- `candidate_top_k` 不允许被单次 Planner 参数扩大到全局上限以上；
- `min_keep <= max_keep <= candidate_top_k`；
- `same_action_limit`、`max_iterations` 必须为正整数；
- 环境变量覆盖必须沿用现有 `RAG_ENHANCEMENT__` 机制；
- 灰度期间保留旧行为开关，确认稳定后再移除兼容分支。

## 9. 实施顺序

### Phase 1：先修 P0 数据契约

1. 增加统一文档分数解析和归一化函数；
2. RAGTool 固定候选上限为 20；
3. 实现动态截断，并在截断后重新质量评估；
4. 扩展直接 RAG、Planner RAG、技能 RAG 的结果元数据；
5. 修改 `ResultAggregator` 使用统一分数函数、去重和全局截断；
6. 补齐 `rag_score_source` 等字段透传。

### Phase 2：修 ReAct 内部熔断

1. 增加规范化 Action Signature；
2. 在实际工具执行后更新连续动作计数；
3. 增加检索观测和 Rerank 历史；
4. 将行为熔断结果写入 `ReactExecutionResult`；
5. 同步更新 Prompt 中的剩余预算快照。

### Phase 3：接入主图终止路由

1. 增加主图统一终止状态字段；
2. 增加 `termination_router` 和 `fallback_node`；
3. 将 ReAct 终止结果映射到主图；
4. 将质量重试和其他局部循环纳入请求级预算；
5. 校验兜底路径不会回到工具节点。

### Phase 4：灰度与清理

1. 先以 shadow 模式记录新旧排序、截断和终止结果差异；
2. 按租户或百分比逐步启用新行为；
3. 稳定后删除旧的 `score` 直接排序逻辑；
4. 稳定后统一旧字段和新字段的观测命名。

## 10. 测试方案

### 10.1 RAG 单元测试

- 候选超过 20 条时只处理前 20 条；
- Rerank 分数存在时不会回退使用 `score` 排序；
- 只有 `similarity` 或 `score` 时能正确回退；
- 分数归一化结果始终处于 `[0, 1]`；
- 动态截断会按绝对阈值、相对比例和分数断崖停止；
- 低于阈值时仍保留 `min_keep` 条；
- 空结果不会伪造文档；
- 截断后 `top_score`、`avg_score`、`relevant_count` 与最终文档一致；
- 文档重复时去重结果稳定。

### 10.2 Planner 聚合测试

- 多步骤 RAG 结果按统一相关性分数排序；
- 相同文档跨步骤只保留一条；
- `score_source` 一致时正确透传；
- `score_source` 不一致时标记 `mixed` 并使用保守策略；
- 聚合后重新计算最终统计字段；
- 失败步骤不污染质量统计。

### 10.3 ReAct 熔断测试

- 同一工具和同一实际参数连续三次且无新证据时触发 `same_action_loop`；
- 参数变化时重置连续计数；
- 仅 LLM 原始 JSON 的字段顺序变化不应绕过重复检测；
- 连续 Rerank 下降但查询变化时不误触发；
- 连续下降达到阈值且无证据增量时触发 `rerank_declining`；
- `max_iterations=8` 时第 8 次后不再执行第 9 次；
- 预算耗尽结果包含实际预算快照和终止原因。

### 10.4 主图路由测试

- ReAct `same_action_loop` 能进入统一终止路由；
- 有证据时进入保守生成；
- 无证据时进入 fallback；
- `fallback_node` 不会重新进入 RAG、DB 或 Web 节点；
- 质量重试达到全局预算后不会继续重试；
- 基础设施错误按配置直接兜底；
- 终止原因和来源正确写入可观测性字段。

### 10.5 回归测试

- 纯 RAG、纯 DB、Hybrid、Web、Chitchat 主流程；
- Planner 多工具并行和串行路径；
- ReAct disabled、empty_question、正常 finish、tool error；
- 幻觉检测 regenerate 路径与终止路由互不形成新循环。

## 11. 可观测性与验收指标

每次请求至少记录：

- `candidate_top_k`、最终 `result_count`；
- `score_source`、`raw_score_source`、校准版本；
- `top_score`、`avg_score`、`quality_score`；
- `query_signature`、`evidence_signature`；
- `agent_iteration_count`、各局部计数；
- `termination_reason`、`termination_source`；
- `same_action_count`、`rerank_score_history`；
- 是否进入保守生成或直接兜底；
- 新旧策略的结果数量、答案成功率和人工介入率差异。

验收重点：

1. 不再出现同一路径同一批文档因字段不同而排序不一致；
2. 终止后主图不会继续调用工具；
3. 正常查询的有效证据召回率不因动态截断明显下降；
4. 重复动作和无收益检索可以被稳定识别；
5. 任何终止原因都能追踪到具体节点、动作和预算状态。

## 12. 兼容、灰度与回滚

### 12.1 兼容策略

- 保留现有 `score` 字段作为展示兼容字段，但所有内部逻辑改用 `relevance_score`；
- 缺失新元数据时按 `score_source=base` 和保守阈值处理；
- 旧的 `retry_count`、`max_retries` 和 `max_steps` 暂时保留，统一预算接入完成后再收敛；
- 不改变现有 `quality_check` 的质量决策职责，不在其中解析 Action History。

### 12.2 灰度策略

建议分三阶段：

1. **Shadow**：新旧排序、截断和终止判断并行计算，新策略不阻断主流程；
2. **小流量**：按租户白名单或百分比启用新策略，重点观测兜底率、人工介入率和答案引用覆盖率；
3. **全量**：确认指标稳定后启用新策略，保留配置开关用于快速回退。

### 12.3 回滚条件

出现以下任一情况时回滚新策略：

- 误触发循环熔断导致正常请求成功率明显下降；
- 动态截断导致有效引用数量或答案正确率明显下降；
- Planner 路径分数来源大量变为 `mixed` 且无法解释；
- 主图出现终止后仍调用工具；
- Token、LLM 调用量或延迟明显超出预算。

回滚只关闭新策略开关，不回退已产生的审计和观测字段，保证问题可定位。

## 13. 最终结论

本修正版不再把“Top-20”“连续三步”“分数连续下降”作为孤立规则直接叠加，而是通过统一结果契约、请求级 LoopGuard 和主图终止路由解决 P0/P1 问题：

- Top-20 是候选上限；动态截断发生在 Rerank 之后；质量评估基于最终结果；
- 所有入口统一使用规范化相关性分数和 `score_source`；
- Action Signature 必须基于实际执行参数；
- Rerank 趋势必须使用跨轮历史，并结合证据增量和查询变化进行抑制误杀；
- ReAct 的停止结果必须回写主图；
- 终止后根据已有证据选择保守生成或统一兜底；
- 主图、Planner、ReAct 和答案重生成共享请求级总预算。

该方案当前仅作为设计输出，下一步应在完成测试用例和字段契约评审后，再分阶段实施代码修改。
