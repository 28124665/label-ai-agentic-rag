# Agentic RAG 智能知识问答系统 — 二期需求规格说明书

> 基于一期 PRD 差距分析，提取"部分实现"与"未实现"需求，形成本项目二期开发计划
>
> 编制日期：2026-07-07

---

## 一、二期需求总览

### 1.1 背景

一期已基于 RAGFlow 完成了 Agentic RAG 系统的核心骨架搭建，包括：意图分析与路由、多工具调用、混合检索、Rerank 重排序、Web 搜索兜底、答案生成与引用、多轮对话记忆、安全认证与部署等能力。

二期聚焦于**闭环能力的补全与生产级增强**，重点解决"自我反思"、"自适应优化"和"可观测性"三大短板。

### 1.2 需求清单

| 编号 | 需求条目 | 一期状态 | 二期优先级 | 来源 |
|:-----|:---------|:---------|:----------|:-----|
| **P2-FR-01** | 检索结果相关性评估（Grader 组件化） | 🔶 部分实现 | P0 | FR-07 |
| **P2-FR-02** | 查询重写与分级重试机制 | 🔶 部分实现 | P0 | FR-08 |
| **P2-FR-03** | 幻觉检测与忠实度验证 | ❌ 未实现 | P0 | FR-10 |
| **P2-FR-04** | Agent 状态结构标准化 | 🔶 部分实现 | P2 | FR-01 |
| **P2-FR-05** | 检查点时间旅行（调试回溯） | 🔶 部分实现 | P1 | FR-12 |
| **P2-FR-06** | 全链路可观测性与告警 | 🔶 部分实现 | P1 | FR-14 |
| **P2-NFR-01** | 性能基准验证与优化 | 🔶 部分实现 | P1 | NFR-01 |
| **P2-NFR-02** | 优雅降级与熔断机制 | 🔶 部分实现 | P1 | NFR-02 |

### 1.3 二期目标流程

二期完成后，系统应能编排如下完整闭环：

```
用户输入 → 意图分析 → 路由 → RAG Tool / Web Search / Direct
                                  ↓
                          混合检索（动态权重）→ Rerank
                                  ↓
                        【Grader 相关性评估】 ← 新增组件
                                  ↓
                    ┌─────────────┼─────────────┐
                    ↓             ↓             ↓
              有相关文档    不相关&重试<3次   不相关&重试≥3次
                    ↓             ↓             ↓
              答案生成    【分级查询重写】    Web 搜索兜底
                    ↓      ├─ 同义词扩展
              【幻觉检测】  ├─ 子查询拆解     → 重新评估
                    ↓      └─ HyDE
            ┌───────┼───────┐
            ↓               ↓
      验证通过        验证失败&重试<2次
            ↓               ↓
      返回答案+引用    重新生成答案
```

---

## 二、功能需求详情

---

### P2-FR-01：检索结果相关性评估（Grader 组件化）

**一期现状**：
- Agent 工作流中可通过 LLM 组件 + Switch 组件组合实现评估逻辑，但没有独立的 Grader 组件
- 无显式的 `graded_docs` 状态字段，评估结果分散在 Agent 执行过程中
- 需在 Agent Canvas 中手动编排评估逻辑，不可复用

**二期目标**：新增专用的 Grader Agent 组件，封装相关性评估逻辑，使其成为可复用的标准组件。

#### 详细要求

**1. Grader 组件设计**

新增 `agent/component/grader.py`，继承 `ComponentBase`，具备以下能力：

- **输入**：用户查询（query）+ 候选文档列表（retrieved_docs）
- **处理**：对候选文档进行相关性评估
  - 默认采用**批量评估模式**，一次 Prompt 评估多个文档
  - 支持配置评估模型：LLM / Cross-Encoder / 本地轻量级 NLI 模型
  - 支持对每个文档进行二分类评估
    - `relevant`：文档内容与用户问题直接相关
    - `not relevant`：文档内容与用户问题不相关
- **输出**：评估后的文档列表（graded_docs），每个文档附带评估结果和相关性分数

**成本与性能约束**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `batch_size` | 5 | 单次评估文档数量，最大不超过 10 |
| `evaluator_model` | `default_llm` | 可选 `llm`、`cross_encoder`、`local_nli` |
| `max_eval_tokens` | 2000 | 单次评估调用最大 Token 预算，超限则拆分批次 |
| `timeout_seconds` | 10 | LLM 调用超时时间 |
| `fallback_on_failure` | `True` | 评估失败时是否降级为使用 Rerank 分数 |
| `max_retry_on_parse_error` | 1 | LLM 输出解析失败时的最大重试次数 |
| `backup_llm_model` | `None` | 主模型配额超限时切换的备用模型 |

**超时与失败兜底流程**：

```python
try:
    result = llm_client.evaluate_batch(prompt, timeout=timeout_seconds)
except TimeoutError:
    # 超时：使用 Rerank 分数作为 fallback
    graded_docs = fallback_by_rerank(retrieved_docs, reason="llm_timeout")
except LLMQuotaExceededError:
    # 配额超限：切换备用模型
    if backup_llm_model:
        result = llm_client.evaluate_batch(prompt, model=backup_llm_model)
    else:
        graded_docs = fallback_by_rerank(retrieved_docs, reason="llm_quota_exceeded")
except LLMResponseParseError:
    # 解析失败：重试 1 次
    result = retry_evaluate(prompt, max_attempts=max_retry_on_parse_error)
    if not result:
        graded_docs = fallback_by_rerank(retrieved_docs, reason="parse_error")
except LLMServiceUnavailableError:
    # 服务不可用：直接 fallback
    graded_docs = fallback_by_rerank(retrieved_docs, reason="service_unavailable")
```

**兜底策略选择**：

| 失败场景 | 兜底策略 | 说明 |
|---------|---------|------|
| 网络超时 | 使用 Rerank 分数 | 最稳妥，不引入额外成本 |
| 解析失败 | 重试 1 次，再失败用 Rerank | 可能是偶发格式问题 |
| 配额超限 | 切换备用模型 | 避免完全不可用 |
| 服务不可用 | 使用 Rerank 分数 | 快速恢复 |
| 所有 LLM 都失败 | 全部标记为 `relevant` | 保证流程继续，但记录告警 |

**兜底后的状态标记**：

```python
graded_docs = [
    {
        "content": doc["content"],
        "relevance": "relevant",  # 基于 Rerank 分数判断
        "score": doc["rerank_score"],
        "graded_by": "rerank_fallback",  # 标记评估来源
        "fallback_reason": "llm_timeout"  # 记录兜底原因
    }
]
```

- 所有 fallback 情况必须记录告警日志
- `fallback_reason` 写入 `graded_docs` 元数据，便于后续分析

**2. 评估 Prompt 规范**

LLM 批量评估 Prompt 示例：

```
你是一个文档相关性评估专家。
请判断以下每个文档是否与用户问题直接相关。

用户问题：{query}

待评估文档：
{documents}

请输出 JSON 数组，格式如下：
[
  {"index": 0, "relevance": "relevant", "score": 0.92, "reason": "..."},
  {"index": 1, "relevance": "not_relevant", "score": 0.15, "reason": "..."}
]
```

- Prompt 模板需支持自定义配置（通过组件参数传入）
- 支持配置评估的置信度阈值
- 批量评估时输出 JSON 数组，包含 `index`、`relevance`、`score`、`reason`

**长文档处理策略**：

| 场景 | 处理方式 |
|------|---------|
| 单条文档长度超过模型上下文限制 | 按语义段落截断，分别评估后取最高相关度 |
| 批量文档总长度超过 `max_eval_tokens` | 自动拆分为多个批次，降低 `batch_size` |
| 文档为表格/图片等非文本内容 | 使用文档描述文本（caption/summary）进行评估 |

**批量评估实现细则**：

| 配置项 | 建议值 | 说明 |
|--------|--------|------|
| 默认 `batch_size` | 5 | 平衡成本和延迟 |
| 最大 `batch_size` | 10 | 防止 Prompt 过长 |
| 输入格式 | `[index] 文档片段` | 每个文档前加序号 |
| 输出格式 | JSON 数组 | 包含 `index`、`relevance`、`score`、`reason` |
| 超限处理 | 自动降 `batch_size` | 当总 Token 超过 `max_eval_tokens` 时拆分 |

- 当 `evaluator_model` 为 `cross_encoder` 时，批量评估改为成对输入 `(query, doc)`
- 当 `evaluator_model` 为 `local_nli` 时，使用 premise（doc）+ hypothesis（claim）格式

**3. 状态字段定义**

Grader 组件执行后，需将以下字段写入 Agent 共享状态：

| 字段 | 类型 | 说明 |
|------|------|------|
| `graded_docs` | `List[dict]` | 评估后的文档列表 |
| `graded_docs[].content` | `str` | 文档内容 |
| `graded_docs[].relevance` | `str` | `relevant` / `not relevant` |
| `graded_docs[].score` | `float` | 相关性分数（0-1） |
| `has_relevant` | `bool` | 是否存在相关文档 |
| `relevant_count` | `int` | 相关文档数量 |

**4. 路由联动**

Grader 组件输出需与 Switch 组件联动，支持以下路由规则：

| 条件 | 路由目标 |
|------|---------|
| `has_relevant == True` | 进入答案生成节点 |
| `has_relevant == False` 且 `retry_count < max_retries` | 进入查询重写节点 |
| `has_relevant == False` 且 `retry_count >= max_retries` | 进入 Web 搜索兜底 或 直接拒答 |

#### 验收标准

- [ ] Grader 组件可独立注册到 Agent Canvas 组件面板
- [ ] 组件支持配置 LLM 模型和 Prompt 模板
- [ ] 评估结果正确写入 `graded_docs` 字段
- [ ] 与 Switch 组件联动路由正确
- [ ] 支持批量评估（一次评估多个文档）
- [ ] 评估延迟 < 2s（10 条文档）

#### 涉及文件

| 操作 | 文件路径 |
|------|---------|
| 新增 | `agent/component/grader.py` |
| 修改 | `agent/component/__init__.py`（注册组件） |
| 参考 | `agent/component/categorize.py`（LLM 二分类模式参考） |

---

### P2-FR-02：查询重写与分级重试机制

**一期现状**：
- 有 `loop.py`（循环）和 `iteration.py`（迭代）组件，可构建重试循环
- 有 `rag/nlp/synonym.py` 同义词处理
- **缺少**：子查询拆解、HyDE（假设性答案检索）、分级重试策略

**二期目标**：实现完整的"检索 → 评估 → 修正 → 再检索"闭环，支持分级重试策略。

#### 详细要求

**1. 分级重试策略**

重试策略由**查询复杂度分析器**动态决定，而非固定顺序。系统根据查询特征选择最适合的策略：

| 查询类型 | 推荐策略 | 触发条件 |
|---------|---------|---------|
| 简单事实查询 | 同义词扩展 + 表述改写 | 查询长度短、实体单一 |
| 多实体/多条件查询 | 子查询拆解 | 查询包含多个并列条件或比较 |
| 具体数值/指标查询 | HyDE（假设性文档检索） | 查询需要具体数据支撑 |

**策略选择逻辑**：

```python
complexity = analyze_query_complexity(query)
candidate_strategies = {
    "simple": ["synonym_rewrite"],
    "multi_aspect": ["sub_query_decompose"],
    "factual": ["hyde"],
    "complex": ["synonym_rewrite", "sub_query_decompose", "hyde"]
}
# 根据重试次数依次尝试候选策略
strategy = candidate_strategies[complexity][retry_count % len(candidate_strategies[complexity])]
```

**查询复杂度判断规则**：

| 查询特征 | 复杂度类型 | 策略 |
|---------|-----------|------|
| 长度 ≤ 15 字，实体 ≤ 1 个 | `simple` | synonym_rewrite |
| 包含"比较/区别/差异/优劣" | `multi_aspect` | sub_query_decompose |
| 包含具体数字/年份/指标 | `factual` | hyde（若开启） |
| 包含多个实体或复杂逻辑 | `complex` | 依次使用三种策略 |
| 是/否问题 | `boolean` | synonym_rewrite |

- 先规则判断，规则无法覆盖时再用轻量级 LLM 判断
- 复杂度判断结果写入状态字段 `query_complexity`

**成本与风险控制**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_retry_tokens` | 2000 | 单次请求重试阶段最大 Token 预算 |
| `max_retries` | 3 | 最大重试次数，可配置为 0-5 |
| `enable_hyde` | `False` | HyDE 默认关闭，需验证效果后开启 |
| `enable_sub_query` | `True` | 子查询拆解默认开启 |
| `sub_query_max_count` | 5 | 子查询最大数量 |

- 当重试阶段累计 Token 消耗超过 `max_retry_tokens` 时，立即停止重试，进入 Web 搜索兜底或直接拒答
- HyDE 生成的假设答案**仅用于检索**，不参与最终答案生成，且检索结果需经 Grader 二次过滤

**重试触发条件**：

触发重试必须同时满足：
1. `has_relevant == False`（Grader 判定没有相关文档），或 `relevant_count < min_relevant_docs`（默认最少 2 条相关文档）
2. `retry_count < max_retries`
3. `accumulated_retry_tokens < max_retry_tokens`
4. 当前查询非闲聊/问候类（由 Categorize 组件判断）

**不触发重试的情况**：
- 已经触发过 Web 搜索兜底
- 当前查询为简单问候/闲聊
- 用户明确要求"根据检索结果回答"

**2. 新增组件**

**2.1 查询重写组件** `agent/component/query_rewriter.py`

- **输入**：原始查询（query）+ 重试次数（retry_count）+ 失败原因
- **处理**：根据重试次数选择不同策略
- **输出**：重写后的查询（rewritten_query）

```python
# 策略选择逻辑（基于查询复杂度和重试次数）
complexity = analyze_query_complexity(query)
strategies = get_strategies_by_complexity(complexity)
strategy = strategies[retry_count % len(strategies)]

if retry_count > 0 and accumulated_retry_tokens > max_retry_tokens:
    # 成本超支，停止重试
    strategy = "fallback"
```

**2.2 子查询拆解组件** `agent/component/sub_query_decomposer.py`

- **输入**：复杂查询
- **处理**：LLM 将查询拆解为 2-5 个子查询
- **输出**：子查询列表

```
原始查询："RAGFlow 和 LangChain 在检索增强生成方面有什么区别？"
子查询：
  1. "RAGFlow 检索增强生成的实现方式"
  2. "LangChain 检索增强生成的实现方式"
  3. "RAGFlow 与 LangChain 功能对比"
```

- 子查询分别检索后合并去重

**子查询结果合并策略**：

| 步骤 | 说明 |
|------|------|
| 去重 | 基于语义相似度（embedding）去重，阈值 0.92 |
| 重排序 | 使用 Rerank 模型对所有子查询结果统一重排序 |
| 截断 | 保留 Top-K 结果（默认 10） |
| 来源标记 | 每个结果标注来自哪个子查询，便于引用展示 |

**合并算法（RRF + 语义去重）**：

```python
def merge_sub_query_results(results_per_query, k=60, top_k=10):
    scores = {}
    for results in results_per_query:
        for rank, doc in enumerate(results):
            doc_id = doc["id"]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank + 1)
    
    sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    # 语义去重
    merged = deduplicate_by_embedding(sorted_docs, threshold=0.92)
    return merged[:top_k]
```

- 去重阈值：embedding 余弦相似度 ≥ 0.92 视为重复
- 每个结果保留 `sub_query_sources` 字段，记录来自哪些子查询

**2.3 HyDE 组件** `agent/component/hyde.py`

- **输入**：用户查询
- **处理**：
  1. LLM 生成假设性答案（不基于检索，仅凭模型知识）
  2. 将假设性答案作为检索查询
- **输出**：假设性答案 + 用于检索的查询文本（假设答案不进入最终生成）

```
用户查询："公司 2024 年 Q3 的营收增长率是多少？"
HyDE 假设答案："公司 2024 年 Q3 营收增长率为 15.3%，主要得益于..."
→ 使用假设答案作为检索查询，匹配实际文档
→ 假设答案本身不展示给用户，也不参与最终答案生成
```

**HyDE 风险控制**：
- 默认关闭，通过 `enable_hyde=True` 开启
- HyDE 检索结果必须通过 Grader 过滤，阈值比普通检索更严格（如 score ≥ 0.8）
- 生成假设答案时使用低温度（temperature ≤ 0.3），降低幻觉风险
- 记录 HyDE 触发率和命中率，用于效果评估

**3. 重试状态管理**

| 字段 | 类型 | 说明 |
|------|------|------|
| `retry_count` | `int` | 当前重试次数（初始 0） |
| `max_retries` | `int` | 最大重试次数（默认 3，可配置） |
| `retry_history` | `List[dict]` | 重试历史记录 |
| `retry_history[].query` | `str` | 该次重试使用的查询 |
| `retry_history[].strategy` | `str` | 该次使用的策略 |
| `retry_history[].result_count` | `int` | 该次检索结果数量 |

**4. 完整闭环流程**

```
检索 → Rerank → Grader 评估
                    ↓
            全部不相关？
           ╱          ╲
         是             否 → 进入生成
         ↓
   retry_count < max_retries？
   ╱            ╲
  是              否 → Web 搜索兜底 / 拒答
  ↓
查询重写（分级策略）
  ↓
retry_count++
  ↓
重新检索 → Rerank → Grader 评估（循环）
```

#### 验收标准

- [ ] 查询重写组件可根据重试次数自动选择策略
- [ ] 子查询拆解能正确拆解复杂查询为 2-5 个子查询
- [ ] HyDE 能生成假设性答案并用于检索
- [ ] 重试计数和最大重试次数配置生效
- [ ] 重试历史记录完整，支持审计
- [ ] 重试后检索命中率较重试前有明显提升（目标 ≥ 20%）
- [ ] 整个闭环可在 Agent Canvas 中通过拖拽组件编排

#### 涉及文件

| 操作 | 文件路径 |
|------|---------|
| 新增 | `agent/component/query_rewriter.py` |
| 新增 | `agent/component/sub_query_decomposer.py` |
| 新增 | `agent/component/hyde.py` |
| 修改 | `agent/component/__init__.py`（注册组件） |
| 参考 | `agent/component/loop.py`（循环控制参考） |
| 参考 | `rag/nlp/synonym.py`（同义词扩展参考） |

---

### P2-FR-03：幻觉检测与忠实度验证

**一期现状**：
- 完全未实现
- 无幻觉检测组件、无忠实度评估逻辑、无重新生成闭环

**二期目标**：在答案生成后增加事实一致性检查，验证答案是否被检索文档支撑，形成"生成 → 检测 → 修正"闭环。

#### 详细要求

**1. 幻觉检测组件** `agent/component/hallucination_detector.py`

- **输入**：生成的答案（answer）+ 检索文档（retrieved_docs）+ 原始查询（query）
- **处理**：采用**多层检测架构**，降低 LLM 自评估的误判风险
  1. **规则层**：对数值、日期、比例、专有名词进行精确匹配/正则校验
  2. **模型层**：使用 NLI（自然语言推理）小模型判断 entailment / neutral / contradiction
  3. **LLM 层**：对复杂推理型论断进行语义忠实度判断
  4. **投票层**：综合多层结果，计算最终忠实度分数
- **输出**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `is_hallucination` | `bool` | 是否存在幻觉 |
| `faithfulness_score` | `float` | 忠实度分数（0-1） |
| `claims` | `List[dict]` | 论断列表 |
| `claims[].text` | `str` | 论断文本 |
| `claims[].supported` | `bool` | 是否有文档支撑 |
| `claims[].evidence` | `str` | 支撑证据（文档片段） |
| `hallucination_count` | `int` | 幻觉论断数量 |

**规则层：数值/日期/比例精确性检查**：

规则层优先于模型层和 LLM 层执行，用于快速识别可精确验证的事实。

**可提取实体类型**：

| 类型 | 提取规则 | 示例 |
|------|---------|------|
| 数值 | 整数、小数、千分位、货币符号 | `15.3%`、`¥1000`、`1,234.56` |
| 日期 | `YYYY-MM-DD`、`YYYY年MM月DD日`、`Q1-Q4` | `2024-09-30`、`2024年Q3` |
| 比例/百分比 | 数字 + `%` 或 "百分之" | `15.3%`、`百分之十五点三` |
| 范围 | `A~B`、`A-B`、`A到B` | `10~20`、`100到200` |
| 专有名词 | 公司名、产品名、人名 | `RAGFlow`、`张三` |

**匹配算法**：

```python
def rule_check_fact(claim, retrieved_docs):
    # 1. 从 claim 中提取数值/日期/比例实体
    claim_entities = extract_entities(claim)
    
    # 2. 在检索文档中查找相同实体
    doc_entities = []
    for doc in retrieved_docs:
        doc_entities.extend(extract_entities(doc["content"]))
    
    # 3. 判断是否存在支撑
    for ce in claim_entities:
        matched = find_match(ce, doc_entities)
        if not matched:
            return "NOT_SUPPORTED", ce
        if is_contradicted(ce, matched):
            return "CONTRADICTED", ce
    
    return "SUPPORTED", None
```

**实体归一化与容差规则**：

| 场景 | 容差规则 |
|------|---------|
| 百分比 | 允许 ±0.5% 的数值差异（四舍五入导致） |
| 大数值 | 允许千分位、单位换算差异（如 1.2 万 vs 12000） |
| 日期 | 允许同一天的多种格式（2024-09-30 vs 2024年9月30日） |
| 季度 | Q3 与 7-9 月视为等价 |
| 货币 | ¥1000、1000元、人民币1000元视为等价 |

**规则层输出格式**：

```json
{
    "claim": "2024年Q3营收增长15.3%",
    "rule_result": "SUPPORTED",
    "matched_evidence": "2024年第三季度营收同比增长15.3%",
    "extracted_entities": [
        {"type": "date", "value": "2024-Q3", "normalized": "2024-Q3"},
        {"type": "ratio", "value": "15.3%", "normalized": 0.153}
    ]
}
```

- 规则层判定为 `CONTRADICTED` 的 claim，整体忠实度分数最高不超过 0.5
- 规则层判定为 `NOT_SUPPORTED` 的数值/日期/比例 claim，直接降低该 claim 分数至 0
- 规则层 `SUPPORTED` 的 claim，LLM 层可不再重复判断，减少 LLM 调用成本

**论断拆解算法**：

| 阶段 | 方法 | 适用场景 |
|------|------|---------|
| 阶段 1 | 按句子切分（标点分割） | 简单陈述 |
| 阶段 2 | 对复合句用依存句法拆分 | "因为...所以..."等复合句 |
| 阶段 3 | 对列表/枚举项单独成 claim | 答案中包含多个要点 |
| 阶段 4 | 对数值型论断合并上下文 | "2024 年 Q3 营收增长 15%" 作为一个 claim |

- 每个 claim 长度控制在 200 字以内
- 使用 LLM 辅助拆分时，Prompt 要求"保留完整语义，不拆分过细"

**忠实度分数计算方式**：

采用**加权三分类投票法**：

```python
# 规则层 + 模型层 + LLM 层各自给出判断
faithfulness_score = (
    0.4 * rule_score +
    0.4 * nli_score +
    0.2 * llm_score
)

# 其中：
# rule_score: 数值/日期完全匹配得 1，否则 0
# nli_score: entailment=1, neutral=0.5, contradiction=0
# llm_score: SUPPORTED=1, NOT_SUPPORTED=0.3, CONTRADICTED=0
```

- 每个 claim 单独计算分数
- 整体忠实度 = 所有 claims 分数的加权平均（按 claim 长度加权）
- 如果某个 claim 被规则层判定为 contradiction，则整体分数最高不超过 0.5

**2. 忠实度评估 Prompt**

```
你是一个事实一致性检查专家。请分析以下答案中的每个论断是否被提供的上下文文档所支撑。

## 上下文文档
{context}

## 待检查答案
{answer}

请逐一列出答案中的每个论断，并判断：
- SUPPORTED：该论断可从上下文中推导出来
- NOT_SUPPORTED：该论断无法从上下文中推导出来
- CONTRADICTED：该论断与上下文内容矛盾

输出格式：
1. [SUPPORTED/NOT_SUPPORTED/CONTRADICTED] 论断内容
   证据：相关文档片段（如有）
```

**3. 判定规则**

| 忠实度分数 | 判定结果 | 后续动作 |
|-----------|---------|---------|
| ≥ 0.85 | 验证通过 | 返回最终答案 |
| 0.6 ~ 0.85 | 轻微幻觉 | 过滤掉不可支撑的论断，返回带修正的保守答案 |
| 0.3 ~ 0.6 | 部分幻觉 | 使用高置信度文档重新生成，最多 1 次 |
| < 0.3 | 严重幻觉 | 返回保守答案或拒答提示，不重新生成 |

**4. 重新生成闭环**

- 仅对"部分幻觉"场景触发重新生成
- 重新生成前过滤掉低置信度文档，只使用 Grader 判定为 relevant 且 score ≥ 0.8 的文档
- 使用严格 Prompt + 低温度（temperature ≤ 0.3）+ few-shot 示例
- 最多重新生成 1 次，避免成本失控
- 若重新生成后仍不达标，返回**保守答案**（仅列出可验证的事实）或标准拒答提示

**保守答案模板**：
```
根据现有资料，我可以确认以下信息：
- [可验证的事实 1]（引用：[doc1]）
- [可验证的事实 2]（引用：[doc2]）

对于 [不可验证的部分]，当前资料不足以给出可靠结论。
```

**检测失败时的用户体验**：

不暴露"幻觉"等技术词，使用用户友好文案：

| 场景 | 返回文案 |
|------|---------|
| 验证通过 | 正常返回答案 |
| 轻微幻觉（0.6-0.85） | 开头加"根据现有资料，已为您整理可确认的信息：" |
| 部分幻觉重生成后通过 | 开头加"已为您综合资料生成回答：" |
| 严重幻觉 | "抱歉，当前资料不足以对您的问题给出可靠回答。建议您补充更具体的关键词或联系相关人员。" |

- 所有降级/保守答案都保留引用标记
- 前端对保守答案和正常答案做视觉区分（如不同背景色）

**5. 完整闭环流程**

```
答案生成 → 幻觉检测（规则层 + NLI 模型层 + LLM 层）
                ↓
         忠实度 ≥ 0.85？
        ╱              ╲
      是                否
      ↓                 ↓
返回答案        0.6 ≤ 忠实度 < 0.85？
               ╱              ╲
             是                否
             ↓                 ↓
      过滤不可支撑论断    0.3 ≤ 忠实度 < 0.6？
      返回保守答案        ╱              ╲
                       是                否
                       ↓                 ↓
              使用高置信度文档        返回保守答案
              严格 Prompt 重生成        或标准拒答
              （最多 1 次）
                       ↓
                再次幻觉检测
```

#### 验收标准

- [ ] 幻觉检测组件可独立注册到 Agent Canvas
- [ ] 能正确拆解答案为独立论断
- [ ] 忠实度评估准确率 ≥ 80%（基于人工标注测试集）
- [ ] 检测到幻觉时能触发重新生成
- [ ] 重新生成最多 1 次，超过后返回保守答案或标准拒答
- [ ] 检测结果包含完整的论断分析和证据
- [ ] 幻觉检测延迟 < 3s
- [ ] 单次请求幻觉检测 Token 成本可控（增加原答案生成成本的 30% 以内）
- [ ] 提供人工反馈入口（点赞/点踩/标记幻觉），支持 Badcase 回流

#### 涉及文件

| 操作 | 文件路径 |
|------|---------|
| 新增 | `agent/component/hallucination_detector.py` |
| 新增 | `rag/prompts/hallucination_check.md`（评估 Prompt） |
| 新增 | `rag/prompts/strict_generator.md`（严格生成 Prompt） |
| 修改 | `agent/component/__init__.py`（注册组件） |

---

### P2-FR-04：Agent 状态结构标准化

**一期现状**：
- RAGFlow 使用自有的 Canvas/Agent 状态管理，与 PRD 描述的 LangGraph State 结构不一致
- 缺少显式的 `graded_docs`、`retry_count`、`is_hallucination` 等标准字段

**二期目标**：在 Agent 共享状态中定义标准化的状态字段，确保各组件间数据传递规范统一。

#### 详细要求

**1. 标准状态字段定义**

在 Agent Canvas 的共享数据中，定义以下标准字段：

```python
STANDARD_STATE_FIELDS = {
    # 基础字段
    "query": str,                    # 当前查询文本
    "messages": list,                # 对话历史与当前消息
    "thread_id": str,                # 会话唯一标识

    # 检索相关
    "retrieved_docs": list,          # 原始检索文档列表
    "graded_docs": list,             # 经 Grader 评估的文档列表
    "rewritten_query": str,          # 重写后的查询

    # 重试相关
    "retry_count": int,              # 当前重试次数
    "max_retries": int,              # 最大重试次数（默认 3）
    "retry_history": list,           # 重试历史记录

    # 生成相关
    "final_answer": str,             # 最终生成的答案
    "answer_with_citations": str,    # 带引用标记的答案

    # 幻觉检测相关
    "is_hallucination": bool,        # 幻觉检测结果
    "faithfulness_score": float,     # 忠实度分数
    "hallucination_retry_count": int,# 幻觉检测重试次数

    # 工具调用记录
    "tool_calls": list,              # 已调用的工具记录
}
```

**2. 状态安全与生命周期管理**

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `state_encryption_enabled` | `True` | 持久化状态是否加密 |
| `state_max_size_mb` | 1 | 单个状态最大大小，超过则压缩或截断 |
| `state_ttl_seconds` | 3600 | 运行时状态 TTL |
| `checkpoint_ttl_days` | 7 | 检查点数据保留天数 |
| `state_schema_version` | `1.0` | 状态 schema 版本，支持向后兼容 |

**敏感字段处理**：
- 敏感字段列表：`messages`、`retrieved_docs`、`final_answer`、`graded_docs`
- 持久化到 Redis/DB 前自动加密或脱敏
- 状态查询需要严格鉴权，防止越权访问

**状态大小控制**：
- 超过 `state_max_size_mb` 时，对长文本字段进行摘要压缩
- 保留必要元数据，丢弃可重新计算的内容

**序列化格式与存储位置**：

| 场景 | 序列化格式 | 存储位置 | 说明 |
|------|-----------|---------|------|
| 运行时组件间传递 | JSON | 内存 | 可读性好，便于调试 |
| Redis 运行时缓存 | JSON | Redis | 便于人工排查 |
| 检查点持久化 | MessagePack + gzip | Redis（热）+ 对象存储（冷） | 体积小、序列化快 |
| 长期归档 | MessagePack + gzip | 对象存储 | 节省存储 |

- 运行时状态丢失后可从检查点恢复
- 检查点定期异步转存到对象存储
- 定义统一的序列化/反序列化工具函数
- 向后兼容：读取旧版本时自动填充缺失字段为默认值

**分级 TTL**：

| 环境 | 运行时状态 TTL | 检查点 TTL |
|------|---------------|-----------|
| dev | 1 小时 | 1 天 |
| test | 2 小时 | 3 天 |
| prod | 1 小时 | 7 天 |
| 调试会话 | 24 小时 | 30 天 |

- 用户主动结束会话时立即清理运行时状态
- 调试模式下可延长 TTL

**3. 组件状态读写规范**

- 每个组件在执行前读取所需字段，执行后写入输出字段
- 组件参数中可配置字段映射（支持自定义字段名）
- 并发修改时采用乐观锁或最后写入优先策略（文档中明确）

#### 验收标准

- [ ] 所有新增组件（Grader、QueryRewriter、HyDE、HallucinationDetector）使用标准字段
- [ ] 状态字段在 Agent Canvas 执行日志中可查看
- [ ] 不同会话的状态相互隔离
- [ ] 敏感字段持久化前已加密或脱敏
- [ ] 状态大小超过限制时能自动压缩
- [ ] 旧版本检查点在新版本代码下可恢复（向后兼容）

#### 涉及文件

| 操作 | 文件路径 |
|------|---------|
| 新增 | `agent/component/state_fields.py`（标准字段定义） |
| 新增 | `api/utils/state_crypto.py`（状态加密工具） |
| 修改 | 各新增组件引用标准字段 |

---

### P2-FR-05：检查点时间旅行（调试回溯）

**一期现状**：
- Go 层 Canvas 已实现基于 Redis 的检查点机制（断点续跑）
- 缺少"时间旅行"功能（回溯到任意历史状态进行调试）

**二期目标**：支持在管理后台查看和回溯 Agent 执行的历史状态，便于调试和问题排查。

#### 详细要求

**1. 检查点历史查询 API**

```
GET /api/canvas/{canvas_id}/run/{run_id}/checkpoints
```

返回该次执行的所有检查点快照：

```json
{
  "checkpoints": [
    {
      "checkpoint_id": "cp_001",
      "node_name": "categorize",
      "timestamp": "2026-07-07T10:00:00Z",
      "state_snapshot": { ... }
    },
    {
      "checkpoint_id": "cp_002",
      "node_name": "retrieval",
      "timestamp": "2026-07-07T10:00:02Z",
      "state_snapshot": { ... }
    }
  ]
}
```

**2. 状态回溯 API**

```
POST /api/canvas/{canvas_id}/run/{run_id}/replay
```

```json
{
  "from_checkpoint": "cp_002",
  "override_state": {
    "query": "修改后的查询"
  }
}
```

- 从指定检查点恢复执行
- 可覆盖部分状态字段（如修改查询文本后重新执行）
- **Replay 不覆盖原 run，而是创建新的 run（fork）**
- Replay 操作需记录审计日志（操作人、时间、原 run、检查点、覆盖字段）
- Replay 权限需单独控制，仅管理员或 canvas 所有者可用

**检查点成本控制**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `checkpoint_enabled_nodes` | `["begin", "categorize", "retrieval", "generator"]` | 只在这些关键节点保存检查点 |
| `checkpoint_max_per_run` | 20 | 单个 run 最多保留检查点数量 |
| `checkpoint_ttl_days` | 7 | 检查点保留天数 |
| `checkpoint_compression` | `True` | 是否压缩存储 |

**检查点保留与清理策略**：

```python
checkpoint_retention_policy = {
    "max_per_run": 20,           # 单个 run 最多保留
    "ttl_days": 7,               # 超过 7 天自动删除
    "keep_latest": 5,            # 即使超过 TTL，也保留最新 5 个
    "debug_mode_extension": 30   # 调试模式下延长到 30 天
}
```

- 清理任务每小时运行一次
- 清理前检查是否有未完成的 Replay 依赖

**Replay 数据影响范围**：

- Replay 只读原 run，写入新 run，**不覆盖原 run 状态**

```python
def replay_from_checkpoint(run_id, checkpoint_id, override_state):
    # 1. 读取原 run 的检查点状态（只读）
    original_state = load_checkpoint(run_id, checkpoint_id)
    # 2. 创建新 run
    new_run_id = fork_run(original_state, override_state)
    # 3. 从新检查点继续执行
    execute_from_state(new_run_id, merged_state)
    return new_run_id
```

- 原 run 状态永久不可修改
- Replay 产生的新 run 与原 run 建立关联关系（`parent_run_id`）
- 覆盖字段只允许白名单内的字段（如 `query`），禁止覆盖 `user_id`、`tenant_id` 等

**3. 前端展示**

在 Agent Canvas 调试界面增加：
- 执行时间线（Timeline），展示每个节点的执行状态
- 点击节点可查看该节点执行前后的状态快照
- "从此处重新执行"按钮

#### 验收标准

- [ ] 检查点历史查询 API 可正常返回
- [ ] 可从指定检查点恢复执行
- [ ] 可覆盖部分状态后重新执行
- [ ] Replay 创建新 run，不覆盖原 run
- [ ] Replay 操作有审计日志
- [ ] 检查点保留策略生效，过期自动清理
- [ ] 前端时间线展示正确

#### 涉及文件

| 操作 | 文件路径 |
|------|---------|
| 新增 | `api/apps/checkpoint_app.py`（检查点 API） |
| 新增 | `api/db/services/checkpoint_service.py`（检查点服务） |
| 修改 | `agent/canvas.py`（增加检查点查询接口） |
| 修改 | 前端 Canvas 调试页面 |

---

### P2-FR-06：全链路可观测性与告警

**一期现状**：
- 已集成 Langfuse，覆盖 LLM 调用追踪
- **缺少**：业务指标采集（命中率、幻觉率、重试率）、结构化日志、告警机制、可视化 Dashboard

**二期目标**：建立完整的可观测性体系，覆盖链路追踪、指标采集、日志管理和告警通知。

#### 详细要求

**1. 业务指标采集**

通过 Prometheus 格式导出以下业务指标：

**质量类指标**：

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `rag_retrieval_hit_rate` | Gauge | 检索命中率 = 最终答案包含引用的请求数 / 总请求数 |
| `rag_grader_relevant_ratio` | Gauge | Grader 判定相关文档比例 |
| `rag_hallucination_detection_rate` | Gauge | 幻觉检测通过率 = 忠实度 ≥ 0.85 的请求数 / 总请求数 |
| `rag_answer_helpful_rate` | Gauge | 用户反馈有帮助率（点赞/点踩） |

**成本类指标**：

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `rag_llm_call_total_tokens` | Counter | LLM 调用总 Token 数 |
| `rag_request_total_cost` | Counter | 单次请求总成本（美元） |
| `rag_retry_token_cost` | Counter | 重试阶段 Token 消耗 |
| `rag_hallucination_check_token_cost` | Counter | 幻觉检测 Token 消耗 |

**性能类指标**：

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `rag_retrieval_latency_seconds` | Histogram | 检索延迟分布 |
| `rag_rerank_latency_seconds` | Histogram | Rerank 延迟分布 |
| `rag_llm_call_latency_seconds` | Histogram | LLM 调用延迟 |
| `rag_end_to_end_latency_seconds` | Histogram | 端到端延迟 |
| `rag_http_request_duration_seconds` | Histogram | HTTP 接口延迟 |

**可靠性指标**：

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `rag_http_5xx_rate` | Gauge | HTTP 5xx 错误率 |
| `rag_timeout_rate` | Gauge | 请求超时率 |
| `rag_degradation_count` | Counter | 降级触发次数 |
| `rag_retry_trigger_count` | Counter | 重试触发次数 |
| `rag_query_rewrite_count` | Counter | 查询重写次数（按策略分标签） |

**命中率口径说明**：

主口径（业务指标）：
```python
retrieval_hit_rate = count(answers_with_citation) / count(total_answers)
```

辅助口径（诊断指标）：
```python
grader_hit_rate = count(runs_with_relevant_docs) / count(total_runs)
```

- 主口径用于业务指标，辅助口径用于诊断
- 命中率按知识库、查询类型、时间维度下钻
- 排除闲聊/问候类查询

**2. 结构化日志**

所有关键节点输出结构化 JSON 日志：

```json
{
  "timestamp": "2026-07-07T10:00:00Z",
  "thread_id": "thread_001",
  "canvas_id": "canvas_001",
  "node_name": "retrieval",
  "duration_ms": 450,
  "status": "success",
  "metadata": {
    "query": "用户问题",
    "result_count": 10,
    "engine": "elasticsearch"
  }
}
```

**3. 告警规则**

> 注：以下阈值为初始建议值，需基于试运行 1-2 周的实际数据校准。

| 告警条件 | 初始阈值 | 级别 | 通知方式 |
|---------|---------|------|---------|
| 检索命中率下降 | 低于试运行基线 P50 | Warning | 企业微信/钉钉 |
| 端到端延迟 P95 | > 10s（5 分钟窗口） | Warning | 企业微信/钉钉 |
| 幻觉检测通过率 | 低于试运行基线 P50 | Critical | 企业微信/钉钉 + 邮件 |
| LLM 调用失败率 | > 5%（5 分钟窗口） | Critical | 企业微信/钉钉 + 邮件 |
| HTTP 5xx 错误率 | > 1%（5 分钟窗口） | Critical | 企业微信/钉钉 + 邮件 |
| 单次请求成本 | 超过基线 3 倍 | Warning | 企业微信/钉钉 |
| 降级触发率 | > 10%（1 小时窗口） | Warning | 企业微信/钉钉 |

**告警治理**：
- 支持告警抑制：相同告警 5 分钟内只发送一次
- 支持告警分级：Warning → Critical → Emergency
- 支持按环境（dev/test/prod）配置不同阈值

**4. Grafana Dashboard**

提供预配置的 Grafana Dashboard，包含：
- 请求量与延迟概览
- 检索命中率趋势
- 幻觉检测通过率趋势
- 重试触发率趋势
- LLM 调用延迟分布
- 错误率统计
- **单次请求成本分布**
- **用户反馈（点赞/点踩）趋势**

#### 验收标准

- [ ] Prometheus 指标可正常采集和查询
- [ ] 结构化日志格式正确，可通过 Promtail 采集
- [ ] Grafana Dashboard 可正常展示核心指标
- [ ] 告警规则触发正确，通知送达
- [ ] 关键请求链路有统一 Trace ID，可在 Langfuse 中查看完整链路
- [ ] 成本指标可按租户/知识库/对话维度下钻

#### 涉及文件

| 操作 | 文件路径 |
|------|---------|
| 新增 | `api/utils/metrics.py`（Prometheus 指标定义） |
| 新增 | `api/utils/structured_logger.py`（结构化日志） |
| 新增 | `deployment/grafana/dashboard.json`（Grafana 面板） |
| 新增 | `deployment/alertmanager/alert_rules.yml`（告警规则） |
| 修改 | `deployment/promtail-config-prod.yaml`（适配结构化日志） |

---

## 三、非功能需求详情

---

### P2-NFR-01：性能基准验证与优化

**一期现状**：
- Go 服务层 + C++ 分词引擎提供高性能基础
- Embedding LRU 缓存减少重复调用
- 未进行系统性压力测试

**二期目标**：通过压力测试验证各项延迟指标达标，并进行针对性优化。

#### 详细要求

**1. 性能测试方法论**

性能测试分两个阶段：

| 阶段 | 名称 | 目标 |
|------|------|------|
| 阶段 1 | 基线测试 | 在现有系统上测出实际延迟分布，作为目标制定依据 |
| 阶段 2 | 达标测试 | 验证优化后是否满足目标值 |

**2. 性能指标目标**

> 注：以下目标值需在基线测试后根据实际数据调整，建议目标 = 基线 P95 × 0.7 或类似合理比例。

| 指标 | 目标值 | 测量方式 |
|------|--------|---------|
| 端到端响应延迟 P95 | < 10s | APM 监控（Langfuse + Prometheus） |
| 端到端响应延迟 P99 | < 15s | APM 监控 |
| 检索阶段延迟 P95 | < 2s | Prometheus Histogram |
| Rerank 延迟 P95 | < 1s（50 条文档） | Prometheus Histogram |
| 意图分析延迟 P95 | < 500ms | Prometheus Histogram |
| Grader 评估延迟 P95 | < 2s（10 条文档） | Prometheus Histogram |
| 幻觉检测延迟 P95 | < 3s | Prometheus Histogram |
| 并发吞吐量 | ≥ 50 QPS | 压力测试（Locust/wrk） |
| HTTP 5xx 错误率 | < 0.5% | Prometheus |
| 请求超时率 | < 1% | Prometheus |
| 降级触发率 | < 5% | Prometheus |

**3. 压力测试方案**

- 工具：Locust / wrk
- 测试数据集：
  - 至少 3 个不同规模的知识库（小：1K 文档、中：10K 文档、大：50K 文档）
  - 覆盖 FAQ、技术文档、规章制度等典型文档类型
- 测试环境：
  - 明确 CPU、内存、GPU 规格
  - 明确 ES/OpenSearch、Redis、LLM 服务的部署规格
- 场景：
  - 简单问答（直接回答）
  - 知识库检索问答（单次检索）
  - 复杂问答（含重试和幻觉检测）
  - 突发流量（从 10 QPS 瞬间提升到 100 QPS）
- 并发梯度：10 → 30 → 50 → 100 QPS
- 持续时间：每轮 10 分钟，稳定期至少 5 分钟
- 预热：每轮测试前预热 2 分钟，确保缓存和模型加载完成

**测试环境硬件规格**：

| 组件 | 最小配置 | 推荐配置 |
|------|---------|---------|
| API 服务 | 4C8G × 2 | 8C16G × 2 |
| Python 服务 | 8C16G × 2 | 16C32G × 2 |
| ES/OpenSearch | 8C16G × 3 | 16C32G × 3 |
| Redis | 4C8G × 1 | 8C16G × 1（主从） |
| Rerank 模型 | CPU 推理 | GPU T4/V100 × 1 |
| LLM | 外部 API | 外部 API |

- 测试报告必须记录实际使用的硬件规格
- 如果使用外部 LLM API，需记录模型版本和并发限制

**并发模型**：

| 阶段 | 模式 | 说明 |
|------|------|------|
| 预热阶段 | 5 QPS，持续 2 分钟 | 缓存预热 |
| 稳定负载 | 10/30/50 QPS，各持续 10 分钟 | 测量稳态性能 |
| 突发负载 | 10 → 100 QPS，5 分钟内完成 | 测试弹性 |
| 恢复阶段 | 10 QPS，持续 5 分钟 | 观察恢复能力 |

- 每个并发级别独立输出报告
- 失败率、错误类型、P50/P95/P99 延迟都要记录

**3. 优化措施**

| 优化项 | 措施 |
|--------|------|
| 检索延迟 | 调整 ES 分片数、优化查询 DSL、增加缓存 |
| Rerank 延迟 | 批量推理、模型量化、GPU 加速 |
| LLM 调用延迟 | 流式输出、请求池化、超时控制 |
| 端到端延迟 | 并行执行无依赖节点、预加载模型 |

#### 验收标准

- [ ] 基线测试报告输出，包含 P50/P95/P99 延迟分布
- [ ] 压力测试报告输出，各项指标达标或有明确优化方案
- [ ] 性能瓶颈已识别并记录
- [ ] 关键路径有缓存和超时保护
- [ ] HTTP 5xx 错误率和超时率达到目标值
- [ ] 性能测试报告包含测试环境配置和数据集说明

---

### P2-NFR-02：优雅降级与熔断机制

**一期现状**：
- 无降级逻辑：LLM 不可用时直接报错
- 无熔断机制：外部服务异常时仍持续重试

**二期目标**：当依赖服务异常时，系统能优雅降级而非直接崩溃。

#### 详细要求

**1. 降级策略**

| 故障场景 | 降级行为 | 用户提示 |
|---------|---------|---------|
| LLM 服务不可用 | 仅返回检索结果（原文摘要），不生成答案 | "当前智能生成服务暂不可用，已为您返回相关检索内容" |
| Rerank 服务不可用 | 跳过 Rerank，直接返回检索排序结果 | "当前检索结果未经重排序，可能相关性有所下降" |
| ES/Infinity 不可用 | 返回缓存结果或提示"知识库暂时不可用" | "知识库检索服务暂时不可用，请稍后重试" |
| Embedding 服务不可用 | 降级为纯 BM25 检索 | "当前使用关键词检索，结果可能不够精准" |
| Web Search API 不可用 | 跳过 Web 搜索，提示"无法获取实时信息" | "当前无法获取网络实时信息" |

**降级规范**：
- 所有降级响应必须包含 `degraded: true` 字段和 `degraded_reason` 字段
- 前端根据 `degraded` 标识展示明显的降级提示
- 降级触发后记录结构化日志，用于后续分析

**降级后 SLA 变化**：

| 指标 | 正常 SLA | 降级 SLA |
|------|---------|---------|
| 端到端延迟 P95 | < 10s | < 5s（因为跳过了部分步骤） |
| 可用性 | 99.9% | 99% |
| 回答完整度 | 100% | 70%（检索结果） |
| 引用准确率 | > 90% | > 80% |

- 降级 SLA 也需要在 Dashboard 中单独监控
- 连续降级超过 10 分钟触发 Critical 告警

**用户提示文案与前端展示**：

| 降级类型 | 用户提示 | 前端展示 |
|---------|---------|---------|
| LLM 不可用 | "当前智能生成服务暂不可用，已为您返回相关检索内容" | 顶部黄色横幅 + 答案区标注"检索结果" |
| Rerank 不可用 | "当前检索结果未经重排序，可能相关性有所下降" | 底部小字提示 |
| ES 不可用 | "知识库检索服务暂时不可用，请稍后重试" | 中央错误提示 + 重试按钮 |
| Embedding 不可用 | "当前使用关键词检索，结果可能不够精准" | 底部小字提示 |
| Web Search 不可用 | "当前无法获取网络实时信息" | 底部小字提示 |

- 文案支持多语言（至少中英文）
- 提供"重新生成"按钮，让用户在恢复后重试

**2. 熔断机制**

- 采用**熔断器模式**（Circuit Breaker）
- 状态机：`CLOSED` → `OPEN` → `HALF_OPEN` → `CLOSED`

按依赖重要性和调用成本分级设置熔断参数：

| 依赖服务 | 失败阈值 | 熔断时长 | 半开探测数 | 超时时长 | 说明 |
|---------|---------|---------|-----------|---------|------|
| LLM 服务 | 5 次 | 60s | 3 次 | 30s | 核心依赖，熔断后降级为检索结果 |
| Rerank 服务 | 3 次 | 30s | 2 次 | 10s | 可选增强，熔断后跳过 |
| ES/Infinity | 5 次 | 60s | 3 次 | 5s | 核心依赖，熔断后使用缓存或拒答 |
| Embedding | 5 次 | 60s | 3 次 | 10s | 核心依赖，熔断后降级为 BM25 |
| Web Search | 3 次 | 30s | 2 次 | 10s | 可选增强，熔断后跳过 |

**3. 实现方案**

- Python 层：使用 `pybreaker` 库
- Go 层：使用 `sony/gobreaker` 库
- **统一熔断状态存储**：熔断状态写入 Redis，Python/Go 共享同一状态
- **主动健康检查**：每 10 秒探测一次依赖服务健康状态，不健康时提前熔断
- **熔断事件广播**：状态变更时通过 Redis Pub/Sub 通知各层
- 熔断状态变更时输出告警日志

#### 验收标准

- [ ] LLM 不可用时返回检索结果而非报错
- [ ] 各外部依赖均有熔断保护
- [ ] 熔断触发后自动恢复（半开探测成功）
- [ ] 降级和熔断事件有日志记录
- [ ] 降级响应包含 `degraded` 和 `degraded_reason` 字段
- [ ] Python/Go 层共享同一熔断状态
- [ ] 通过混沌测试验证降级和熔断效果

#### 涉及文件

| 操作 | 文件路径 |
|------|---------|
| 新增 | `api/utils/circuit_breaker.py`（Python 熔断器） |
| 新增 | `internal/utility/circuit_breaker.go`（Go 熔断器） |
| 新增 | `api/utils/health_checker.py`（主动健康检查） |
| 修改 | `rag/llm/chat_model.py`（LLM 调用增加降级逻辑） |
| 修改 | `rag/llm/rerank_model.py`（Rerank 增加降级逻辑） |
| 修改 | `internal/engine/elasticsearch/`（ES 调用增加降级逻辑） |

---

## 四、二期开发排期建议

### 4.1 迭代规划

| 迭代 | 周期 | 交付内容 |
|------|------|---------|
| **Sprint 1** | 第 1-2 周 | **P2-FR-04 状态标准化**（基础优先）+ P2-FR-01 Grader 组件 |
| **Sprint 2** | 第 3-4 周 | P2-FR-02 查询重写与分级重试（含子查询拆解 + HyDE） |
| **Sprint 3** | 第 5-6 周 | P2-FR-03 幻觉检测与忠实度验证 |
| **Sprint 4** | 第 7-8 周 | P2-NFR-02 优雅降级与熔断 + P2-NFR-01 性能基线测试 |
| **Sprint 5** | 第 9-10 周 | P2-FR-06 可观测性与告警 + P2-FR-05 检查点时间旅行 + 性能达标测试 |

### 4.2 依赖关系

```
P2-FR-04 状态标准化 ──→ P2-FR-01 Grader 组件
        │                      │
        │                      ↓
        │              P2-FR-02 查询重写与重试
        │                      │
        │                      ↓
        └────────────→ P2-FR-03 幻觉检测
                               │
                               ↓
                       P2-FR-06 可观测性
                               │
P2-NFR-02 降级与熔断 ──→ P2-NFR-01 性能验证
                               │
                       P2-FR-05 检查点时间旅行
```

**关键路径**：P2-FR-04 → P2-FR-01 → P2-FR-02 → P2-FR-03 → P2-FR-06

### 4.3 风险项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 幻觉检测准确率不达标 | 误判导致用户体验下降 | 准备多套 Prompt，A/B 测试选优 |
| HyDE 效果不佳 | 第 3 次重试命中率提升有限 | 可配置跳过 HyDE，仅使用前两级策略 |
| 性能指标不达标 | 端到端延迟 > 10s | 优先优化 Rerank 和 LLM 调用链路 |
| 检查点存储量过大 | Redis 内存压力 | 设置检查点 TTL，定期清理 |
| **LLM 调用成本超支** | 新增 Grader/幻觉检测/重试后单次成本翻倍 | 设置 Token 预算上限、批量评估、使用轻量模型 |
| **状态敏感信息泄露** | 检查点/审计日志中明文存储对话内容 | 敏感字段加密/脱敏，严格访问控制 |
| **降级提示不清晰** | 用户收到不完整答案产生误解 | 统一降级响应格式，前端明确标识 |
| **告警阈值不合理** | 误报/漏报影响运维效率 | 试运行收集基线，动态调整阈值 |

---

## 五、附录

### 5.1 一期已实现需求（不在二期范围内）

| 编号 | 需求 | 状态 |
|------|------|------|
| FR-02 | 意图分析与查询路由 | ✅ 已覆盖 |
| FR-03 | 多工具调用框架 | ✅ 已覆盖 |
| FR-04 | 混合检索 | ✅ 已覆盖 |
| FR-05 | 重排序 | ✅ 已覆盖 |
| FR-06 | Web 搜索兜底 | ✅ 已覆盖 |
| FR-09 | 答案生成 | ✅ 已覆盖 |
| FR-11 | 多轮对话记忆 | ✅ 已覆盖 |
| FR-13 | 答案溯源与引用 | ✅ 已覆盖 |
| NFR-03 | 扩展性 | ✅ 已覆盖 |
| NFR-04 | 安全 | ✅ 已覆盖 |
| NFR-05 | 部署 | ✅ 已覆盖 |

### 5.2 架构映射说明

二期新增组件需遵循 RAGFlow 的 Agent 组件架构，而非引入 LangGraph：

| 概念 | RAGFlow 实现方式 |
|------|-----------------|
| Agent 组件 | 继承 `agent/component/base.py` 的 `ComponentBase` |
| 组件注册 | 在 `agent/component/__init__.py` 中注册 |
| 状态共享 | Canvas 的共享数据（`shared_data`） |
| 条件路由 | `agent/component/switch.py` |
| 循环控制 | `agent/component/loop.py` / `iteration.py` |
| 工具调用 | `agent/tools/base.py` 的 `ToolBase` |

### 5.3 参考文档

- 一期 PRD：`prd.md`
- 差距分析报告：`prd_gap_analysis.md`
- RAGFlow Agent 组件开发指南：`agent/component/base.py`
- RAGFlow 部署配置：`conf/service_conf.yaml`
