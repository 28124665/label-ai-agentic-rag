# Grader 组件重构：移除 Cross-Encoder 主动评估模式

## 1. 问题分析

### 1.1 当前架构

当前 Grader 组件支持三种评估模式：
- **LLM 模式**：使用 LLM 进行语义相关性判断
- **Cross-Encoder 模式**：使用 Rerank 模型计算相似度分数
- **Local NLI 模式**：使用 NLI 风格的 prompt 进行蕴含判断

### 1.2 核心问题

**执行顺序**：Grader 在 Retrieval 组件之后执行，而 Retrieval 内部已经调用了 Rerank（Cross-Encoder）进行重排序。

**冗余计算**：当 Grader 使用 `cross_encoder` 模式时，实际上是对已经 Rerank 过的文档再次调用 Cross-Encoder 模型，造成：
- 重复计算相同的语义相似度分数
- 浪费计算资源和延迟
- 没有带来额外的质量提升

**代码证据**：
```python
# agent/tools/retrieval.py L245-251
if rerank_mdl is not None and kbinfos["chunks"]:
    kbinfos["chunks"] = multilingual_rerank(
        query_simplified,
        kbinfos["chunks"],
        top_k=self._param.top_n,
        rerank_model=rerank_mdl,
    )

# agent/component/grader.py L636-643
async def _evaluate_with_cross_encoder(self, query: str, docs: list[dict]) -> list[dict]:
    rerank_mdl = self._create_rerank_bundle(self._param.rerank_model_id)
    texts = [self._doc_text_for_eval(d) for d in docs]
    scores, _ = await asyncio.wait_for(
        asyncio.to_thread(rerank_mdl.similarity, query, texts),
        timeout=self._param.timeout_seconds,
    )
```

### 1.3 降级机制已经存在

Grader 的降级逻辑已经实现了复用 Rerank 分数：
```python
# agent/component/grader.py L680-694
def _fallback_by_rerank(self, docs: list[dict], reason: str) -> list[dict]:
    graded = []
    for doc in docs:
        score = float(doc.get("rerank_score") or doc.get("similarity") or 0.5)
        graded.append({
            "content": doc.get("content", ""),
            "relevance": RELEVANT if score >= self._param.relevance_threshold else NOT_RELEVANT,
            "score": score,
            "reason": f"Rerank fallback due to {reason}",
            "graded_by": "rerank_fallback",
            "fallback_reason": reason,
        })
    return graded
```

## 2. 设计方案

### 2.1 核心改动

**移除 `cross_encoder` 作为主动评估模式**，只保留：
- **LLM 模式**（LLM-as-Judge）：语义相关性判断，精度高，可解释性强
- **NLI 模式**（Natural Language Inference）：蕴含推理，适合事实性查询

**Rerank 分数作为降级方案**：
- 当 LLM/NLI 模式调用失败时，自动降级到 Rerank 分数
- 复用已有的 `rerank_score`，零额外延迟和成本

### 2.2 设计原则

1. **避免冗余计算**：不在 Rerank 之后重复调用 Cross-Encoder
2. **保持灵活性**：LLM 和 NLI 两种模式覆盖不同场景
3. **优雅降级**：LLM/NLI 失败时使用 Rerank 分数兜底
4. **向后兼容**：对于已配置 `cross_encoder` 的工作流，自动映射到降级逻辑

### 2.3 评估模式对比

| 模式 | 判断维度 | 适用场景 | 延迟 | 成本 |
|------|---------|---------|------|------|
| LLM-as-Judge | 语义相关性 | 复杂查询、多文档 QA | 高（1-3s） | 高（LLM token） |
| NLI | 蕴含推理 | 事实性查询、数据查询 | 高（1-3s） | 高（LLM token） |
| Rerank 降级 | 语义相似度 | LLM/NLI 失败时的兜底 | 低（0ms，复用已有分数） | 无 |

### 2.4 降级策略

```
LLM/NLI 模式调用
  ├─ 成功 → 返回评估结果
  ├─ 超时 → 降级到 Rerank 分数
  ├─ 配额超限 → 尝试备用 LLM → 失败则降级到 Rerank 分数
  ├─ 解析失败 → 重试 → 失败则降级到 Rerank 分数
  └─ 服务不可用 → 降级到 Rerank 分数
```

## 3. 实现细节

### 3.1 后端改动

**文件**：`agent/component/grader.py`

1. **移除 `cross_encoder` 模式**：
   - 删除 `_evaluate_with_cross_encoder` 方法
   - 从 `evaluator_model` 的可选值中移除 `cross_encoder`
   - 更新 `GraderParam.check()` 的验证逻辑

2. **保留降级逻辑**：
   - `_fallback_by_rerank` 方法保持不变
   - `_mark_all_relevant` 方法保持不变

3. **更新文档注释**：
   - 说明为什么移除 `cross_encoder` 模式
   - 明确降级策略

### 3.2 前端改动

**文件**：`web/src/pages/agent/form/grader-form/index.tsx`

1. **更新评估模式选项**：
   ```typescript
   const EvalModeOptions = [
     { value: 'llm', label: 'LLM-as-Judge' },
     { value: 'local_nli', label: 'NLI (Natural Language Inference)' },
   ];
   ```

2. **移除 Cross-Encoder 选项**

**文件**：`web/src/constants/agent.tsx`

1. **更新初始值**：
   - `eval_mode` 的默认值保持 `llm`
   - 移除对 `cross_encoder` 的引用

**文件**：`web/src/locales/en.ts` 和 `web/src/locales/zh.ts`

1. **更新国际化文本**：
   - 移除 Cross-Encoder 相关的描述
   - 更新评估模式的提示文本

### 3.3 测试改动

**文件**：`test/agent/component/test_grader.py`

1. **移除 Cross-Encoder 测试**：
   - 删除 `test_cross_encoder_evaluation` 测试用例

2. **保留其他测试**：
   - LLM 模式测试
   - NLI 模式测试
   - 降级逻辑测试

## 4. 影响范围

### 4.1 向后兼容性

**已有工作流**：
- 如果工作流配置了 `evaluator_model: cross_encoder`，需要在加载时自动映射到降级逻辑
- 或者直接报错，要求用户重新配置

**建议方案**：
- 在 `GraderParam.check()` 中检查 `evaluator_model`，如果是 `cross_encoder`，抛出明确的错误信息，提示用户选择 `llm` 或 `local_nli`

### 4.2 性能影响

**正面影响**：
- 移除冗余的 Cross-Encoder 调用，降低延迟
- 减少计算资源消耗

**无负面影响**：
- 降级逻辑已经存在，不会引入新的风险

### 4.3 质量影响

**无质量损失**：
- LLM 和 NLI 模式的评估质量高于 Cross-Encoder
- 降级时使用 Rerank 分数，质量与原来的 `cross_encoder` 模式相同

## 5. 实施步骤

1. **编写设计文档**（当前步骤）
2. **修改后端组件**：`agent/component/grader.py`
3. **修改前端表单**：`web/src/pages/agent/form/grader-form/index.tsx`
4. **修改前端常量和国际化**：`web/src/constants/agent.tsx`、`web/src/locales/en.ts`、`web/src/locales/zh.ts`
5. **修改测试**：`test/agent/component/test_grader.py`
6. **验证**：运行测试，确保所有改动正确

## 6. 总结

本次重构的核心目标是**消除冗余计算，提升系统效率**。通过移除 `cross_encoder` 主动评估模式，避免了在 Rerank 之后重复调用 Cross-Encoder 模型的问题。同时，保留 LLM 和 NLI 两种高质量评估模式，并使用 Rerank 分数作为降级方案，确保系统的健壮性和性能。
