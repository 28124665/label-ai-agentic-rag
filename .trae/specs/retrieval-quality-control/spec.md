# 检索质量控制设计文档

## 1. 概述

### 1.1 目标
构建基于 **NLI（自然语言推理）+ LLM-as-Judge（语义相似度）** 的双层质量把控体系，使用 **Rerank 分数作为降级方案**，确保检索结果的高质量。

### 1.2 核心问题
- **Rerank 的局限性**：Cross-Encoder 只能判断"语义相似度"，无法区分"语义相似但不能回答"和"语义相似且能回答"
- **质量把控需求**：需要在 Rerank 之后进行更深层次的语义判断，过滤不相关文档
- **可靠性保障**：当高级评估失败时，需要有可靠的降级机制

### 1.3 设计原则
1. **双层评估**：NLI 适合事实性查询，LLM-as-Judge 适合复杂查询
2. **优雅降级**：LLM/NLI 失败时自动降级到 Rerank 分数
3. **零冗余**：不复用 Rerank 的 Cross-Encoder 作为主动评估（避免重复计算）
4. **可观测性**：记录评估耗时、命中率、降级原因等指标

---

## 2. 架构设计

### 2.1 整体流程

```
用户查询
  ↓
Retrieval（检索）
  ↓
Rerank（Cross-Encoder 重排序）→ 输出 rerank_score
  ↓
Grader（质量评估）
  ├─ 模式 1: LLM-as-Judge（语义相关性判断）
  ├─ 模式 2: NLI（自然语言推理）
  └─ 降级: Rerank 分数（当 LLM/NLI 失败时）
  ↓
输出 graded_docs（包含 relevance、score、graded_by、fallback_reason）
  ↓
RetryController（根据质量决定是否重试）
```

### 2.2 核心组件

#### 2.2.1 Grader 组件
- **职责**：对检索返回的文档进行深层语义评估
- **输入**：用户查询（query）+ 检索文档列表（retrieved_docs）
- **输出**：
  - `graded_docs`：评估后的文档列表
  - `has_relevant`：是否有相关文档
  - `relevant_count`：相关文档数量

#### 2.2.2 评估模式

**模式 1: LLM-as-Judge**
- **适用场景**：复杂查询、多文档 QA、需要深层语义理解
- **评估方式**：使用 LLM 判断文档与查询的相关性
- **输出格式**：
  ```json
  {
    "index": 0,
    "relevance": "relevant",
    "score": 0.92,
    "reason": "文档直接回答了用户问题"
  }
  ```

**模式 2: NLI（Natural Language Inference）**
- **适用场景**：事实性查询（查数据、查指标）
- **评估方式**：判断文档（premise）是否蕴含查询（hypothesis）
- **判断标准**：
  - `entailment`（蕴含）→ relevant
  - `contradiction`（矛盾）→ not_relevant
  - `neutral`（中立）→ not_relevant

**降级方案: Rerank 分数**
- **触发条件**：LLM/NLI 调用失败（超时、配额超限、服务不可用等）
- **评估方式**：复用检索阶段已有的 `rerank_score`
- **优势**：零额外延迟和成本

---

## 3. 详细设计

### 3.1 参数配置

```python
class GraderParam:
    evaluator_model: str = "llm"  # "llm" | "local_nli"
    batch_size: int = 5  # 每批评估的文档数量
    max_batch_size: int = 10  # 最大批次大小
    max_eval_tokens: int = 2000  # 单次 LLM 调用的最大 token 数
    timeout_seconds: int = 10  # LLM 调用超时时间
    fallback_on_failure: bool = True  # 失败时是否降级（True=使用 Rerank，False=标记全部相关）
    max_retry_on_parse_error: int = 1  # JSON 解析失败时的重试次数
    backup_llm_model: str = None  # 备用模型（主模型配额超限时使用）
    relevance_threshold: float = 0.5  # 判定相关的分数阈值
    llm_id: str = ""  # LLM 模型 ID
    prompt: str = ""  # 自定义 LLM 评估 prompt
    nli_prompt: str = ""  # 自定义 NLI 评估 prompt
```

### 3.2 评估流程

#### 3.2.1 LLM-as-Judge 流程

```python
async def _evaluate_with_llm(query, docs):
    1. 创建 LLM Bundle
    2. 调用 _llm_evaluate 进行批量评估
    3. 异常处理：
       - TimeoutError → Rerank 降级
       - QuotaExceeded → 切换备用模型
       - ParseError → Rerank 降级
       - ServiceUnavailable → Rerank 降级
```

**批量评估逻辑**：
1. 构建评估项（文档按 token 限制拆分为 chunks）
2. 将评估项按 batch_size 分批
3. 对每批执行：调用 LLM → 解析 JSON 响应 → 失败则重试
4. 聚合所有批次结果，同一文档取最高分
5. 合并为最终评估结果

#### 3.2.2 NLI 流程

```python
async def _evaluate_with_local_nli(query, docs):
    1. 创建 LLM Bundle（使用 NLI prompt）
    2. 调用 _llm_evaluate（mode="local_nli"）
    3. 异常处理同 LLM 模式
```

**NLI Prompt 示例**：
```
你是自然语言推理（NLI）专家。请判断每个文档（premise）是否支持或蕴含用户问题（hypothesis）。

用户问题：{query}

待评估文档：
{documents}

请输出 JSON 数组，格式如下：
[
  {"index": 0, "relevance": "relevant", "score": 0.92, "reason": "文档蕴含问题答案"},
  {"index": 1, "relevance": "not_relevant", "score": 0.15, "reason": "文档与问题无关"}
]

relevance 取值说明：
- relevant：文档能回答或蕴含用户问题（entailment）
- not_relevant：文档与用户问题无关、矛盾或无法支撑（contradiction / neutral）
```

#### 3.2.3 降级流程

```python
def _handle_failure(docs, reason):
    if fallback_on_failure:
        return _fallback_by_rerank(docs, reason)
    else:
        return _mark_all_relevant(docs, reason)

def _fallback_by_rerank(docs, reason):
    graded = []
    for doc in docs:
        score = doc.get("rerank_score") or doc.get("similarity") or 0.5
        graded.append({
            "content": doc.get("content", ""),
            "relevance": "relevant" if score >= relevance_threshold else "not_relevant",
            "score": score,
            "reason": f"Rerank fallback due to {reason}",
            "graded_by": "rerank_fallback",
            "fallback_reason": reason
        })
    return graded
```

### 3.3 长文档处理

**语义段落拆分策略**（按优先级）：
1. 如果整段文本不超过限制，直接返回
2. 按双换行（段落边界）拆分
3. 超长段落按句子边界（句号、问号、感叹号）进一步拆分
4. 单个句子仍超长时，按 token 比例截断

```python
def _split_semantic_paragraphs(text, max_tokens):
    if num_tokens_from_string(text) <= max_tokens:
        return [text]
    
    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current = ""
    
    for p in paragraphs:
        if num_tokens_from_string(p) > max_tokens:
            sentences = re.split(r"(?<=[。！？.!?])\s+", p)
            for s in sentences:
                if num_tokens_from_string(current + s) > max_tokens:
                    if current:
                        chunks.append(current.strip())
                        current = ""
                    if num_tokens_from_string(s) > max_tokens:
                        ratio = max_tokens / max(1, num_tokens_from_string(s))
                        s = s[:int(len(s) * ratio)]
                    current = s
                else:
                    current = current + " " + s if current else s
        else:
            combined = current + "\n\n" + p if current else p
            if num_tokens_from_string(combined) > max_tokens:
                if current:
                    chunks.append(current.strip())
                    current = ""
                current = p
            else:
                current = combined
    
    if current:
        chunks.append(current.strip())
    
    return chunks if chunks else [text[:max_tokens * 4]]
```

### 3.4 结果聚合

同一文档可能被拆分为多个 chunks，评估结果需要聚合：

```python
def _aggregate_chunk_results(docs, items, parsed_results, graded_by):
    doc_scores = {}
    for i, res in enumerate(parsed_results):
        doc_idx = items[i]["doc_index"]
        current = doc_scores.get(doc_idx, {"score": 0.0, "relevance": "not_relevant", "reason": ""})
        if res["score"] > current["score"]:
            current = {
                "score": res["score"],
                "relevance": res["relevance"],
                "reason": res.get("reason", "")
            }
        doc_scores[doc_idx] = current
    
    results = []
    for doc in docs:
        info = doc_scores.get(doc["index"], {"score": 0.0, "relevance": "not_relevant", "reason": "No evaluation result"})
        results.append({
            "index": doc["index"],
            "relevance": info["relevance"],
            "score": info["score"],
            "reason": info["reason"]
        })
    
    return _merge_graded_docs(docs, results, graded_by)
```

### 3.5 错误分类

```python
def _classify_llm_error(error_text):
    low = error_text.lower()
    if any(k in low for k in ["timeout", "timed out", "time out"]):
        return asyncio.TimeoutError()
    if any(k in low for k in ["quota", "rate limit", "insufficient quota", "billing", "limit exceeded", "too many requests"]):
        return LLMQuotaExceededError(error_text)
    if any(k in low for k in ["unavailable", "service unavailable", "503", "connection refused", "connection error"]):
        return LLMServiceUnavailableError(error_text)
    return GraderError(error_text)
```

---

## 4. 多级降级策略

### 4.1 降级层级

```
Level 1: 主 LLM 模型
  ↓（失败）
Level 2: 备用 LLM 模型（如果配置了 backup_llm_model）
  ↓（失败）
Level 3: Rerank 分数降级（如果 fallback_on_failure=True）
  ↓（fallback_on_failure=False）
Level 4: 标记全部相关（保守策略，确保工作流不中断）
```

### 4.2 降级原因追踪

每个降级结果都包含 `graded_by` 和 `fallback_reason` 字段：

- `graded_by`：评估方式
  - `"llm"`：主 LLM 模型评估
  - `"backup_llm"`：备用 LLM 模型评估
  - `"local_nli"`：NLI 评估
  - `"rerank_fallback"`：Rerank 分数降级
  - `"llm_all_failed"`：所有 LLM 评估失败，标记全部相关

- `fallback_reason`：降级原因
  - `"llm_timeout"`：LLM 调用超时
  - `"llm_quota_exceeded"`：LLM 配额超限
  - `"parse_error"`：JSON 解析失败
  - `"service_unavailable"`：服务不可用
  - `"llm_failure"`：LLM 调用失败
  - `"missing_result"`：缺少评估结果

---

## 5. 可观测性

### 5.1 指标记录

```python
def _record_metrics(graded, start_ts, status):
    duration_ms = (time.perf_counter() - start_ts) * 1000.0
    has_relevant = any(d.get("relevance") == "relevant" for d in graded)
    relevant_count = sum(1 for d in graded if d.get("relevance") == "relevant")
    fallback_reason = next((d.get("fallback_reason") for d in graded if d.get("fallback_reason")), "")
    kb_id = str(graded[0].get("kb_id") or "unknown") if graded else "unknown"
    
    # 记录命中率
    metrics.record_grader_hit(kb_id, has_relevant)
    
    # 记录结构化日志
    log_grader(
        trace_id=getattr(self._canvas, "task_id", ""),
        span_id=self._id,
        duration_ms=duration_ms,
        status=status,
        fallback_reason=fallback_reason,
        relevant_count=relevant_count,
        total_count=len(graded),
        metadata={"evaluator_model": self._param.evaluator_model, "kb_id": kb_id}
    )
```

### 5.2 日志内容

结构化日志包含：
- `trace_id`：追踪 ID
- `span_id`：组件 ID
- `duration_ms`：评估耗时（毫秒）
- `status`：状态（success/failure）
- `fallback_reason`：降级原因
- `relevant_count`：相关文档数量
- `total_count`：总文档数量
- `metadata`：元数据（评估模式、知识库 ID）

---

## 6. 与 RetryController 的集成

### 6.1 数据流

```
Grader 输出
  ├─ graded_docs → 传递给下游节点
  ├─ has_relevant → RetryController 判断是否需要重试
  └─ relevant_count → RetryController 判断相关文档数量是否足够
```

### 6.2 RetryController 决策逻辑

```python
def evaluate_retry_conditions(
    has_relevant: bool,
    relevant_count: int,
    retry_count: int,
    accumulated_retry_tokens: int,
    max_retries: int = 3,
    max_retry_tokens: int = 2000,
    min_relevant_docs: int = 2,
    is_chitchat: bool = False,
    web_search_fallback_triggered: bool = False
):
    # 1. 已触发 Web 搜索兜底 → 不重试
    if web_search_fallback_triggered:
        return {"should_retry": False, "stop_reason": "web_search_fallback_already_triggered"}
    
    # 2. 闲聊/问候 → 不重试
    if is_chitchat:
        return {"should_retry": False, "stop_reason": "chitchat_or_greeting"}
    
    # 3. 检索质量足够 → 不重试
    needs_retry = not has_relevant or relevant_count < min_relevant_docs
    if not needs_retry:
        return {"should_retry": False, "stop_reason": "sufficient_relevant_docs"}
    
    # 4. 重试次数已达上限 → 触发 Web 搜索兜底
    if retry_count >= max_retries:
        return {"should_retry": False, "retry_exceeded": True, "trigger_web_search_fallback": True, "stop_reason": "max_retries_reached"}
    
    # 5. Token 成本已达上限 → 触发 Web 搜索兜底
    if accumulated_retry_tokens >= max_retry_tokens:
        return {"should_retry": False, "retry_exceeded": True, "trigger_web_search_fallback": True, "stop_reason": "max_retry_tokens_reached"}
    
    # 6. 以上均不满足 → 触发重试
    return {"should_retry": True, "stop_reason": "retrieval_quality_below_threshold"}
```

---

## 7. 前端配置

### 7.1 表单字段

```typescript
const initialGraderValues = {
  query: '{sys.query}',
  documents: '',
  eval_mode: 'llm',  // 'llm' | 'local_nli'
  batch_size: 5,
  relevance_threshold: 0.7,
  max_eval_tokens: 2000,
  timeout: 30,
  max_retry_on_parse_error: 2,
  backup_llm_model: '',
  outputs: {
    graded_documents: { type: 'Array<Object>', value: [] },
    relevant_count: { type: 'integer', value: 0 }
  }
};
```

### 7.2 评估模式选项

```typescript
const EvalModeOptions = [
  { value: 'llm', label: 'LLM-as-Judge' },
  { value: 'local_nli', label: 'NLI (Natural Language Inference)' }
];
```

### 7.3 国际化

**中文**：
```typescript
graderEvalModeTip: '选择评估方法：LLM-as-Judge（语义相关性判断，精度最高）或 NLI（自然语言推理，适合事实性查询）。当评估失败时自动降级为 Rerank 分数。'
```

**英文**：
```typescript
graderEvalModeTip: 'Select evaluation method: LLM-as-Judge (semantic relevance judgment, highest accuracy) or NLI (natural language inference, suitable for factual queries). Automatically falls back to Rerank scores when evaluation fails.'
```

---

## 8. 测试策略

### 8.1 单元测试

1. **参数验证测试**
   - 默认值验证
   - 参数范围验证（batch_size、relevance_threshold 等）
   - 评估模式验证（只允许 "llm" 和 "local_nli"）

2. **LLM 评估测试**
   - 成功评估
   - 超时降级
   - 配额超限降级（有备用模型）
   - 配额超限降级（无备用模型）
   - 解析错误重试
   - 服务不可用降级

3. **NLI 评估测试**
   - 成功评估
   - 降级处理

4. **长文档处理测试**
   - 语义段落拆分
   - 批次拆分

5. **降级策略测试**
   - Rerank 降级
   - 标记全部相关

### 8.2 集成测试

1. **端到端流程测试**
   - Retrieval → Rerank → Grader → RetryController
   - 验证数据流和状态传递

2. **降级链路测试**
   - 主 LLM → 备用 LLM → Rerank
   - 验证降级原因追踪

---

## 9. 性能优化

### 9.1 批量评估
- 将文档按 batch_size 分批，减少 LLM 调用次数
- 每批文档的总 token 数不超过 max_eval_tokens

### 9.2 并行处理
- 使用 `asyncio` 进行异步评估
- 支持并发处理多个批次

### 9.3 缓存策略
- 对于相同的查询和文档，可以缓存评估结果
- 避免重复评估（未来优化方向）

---

## 10. 总结

### 10.1 核心特性

1. **双层评估**：NLI + LLM-as-Judge，覆盖不同场景
2. **优雅降级**：LLM/NLI 失败时自动降级到 Rerank 分数
3. **零冗余**：不复用 Rerank 的 Cross-Encoder，避免重复计算
4. **可观测性**：完整的指标和日志记录
5. **灵活配置**：支持自定义 prompt、备用模型、降级策略

### 10.2 与现有系统的集成

- **Retrieval**：输出 rerank_score 供 Grader 降级使用
- **Grader**：输出 graded_docs、has_relevant、relevant_count
- **RetryController**：根据 Grader 输出决定是否触发查询重写重试

### 10.3 未来优化方向

1. **评估结果缓存**：避免重复评估相同的查询和文档
2. **动态调整阈值**：根据历史数据动态调整 relevance_threshold
3. **多模型融合**：结合多个 LLM 的评估结果，提高准确性
4. **评估质量监控**：监控降级频率，及时发现模型问题
