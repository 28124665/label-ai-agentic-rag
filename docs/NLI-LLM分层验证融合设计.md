# NLI + LLM 分层验证融合设计（v3.0）

> 在现有 CitationBinder（纯 Rule 层）基础上，引入 NLI 层和 LLM 层语义验证，
> 采用 **Batch 分组** 方式调用大模型，平衡成本与精度。

---

## 1. 设计目标

### 现状问题

| 问题 | 详情 |
|------|------|
| CitationBinder 只有 Rule 层 | `_judge_overlap()` 纯正则+实体重叠率，无法处理语义级判断 |
| nli_result / llm_result 永远为空 | PairVerdict 的 nli_result="unknown", llm_result="unknown" |
| ALL_LAYERS_SUPPORTED 永远不命中 | VerdictMatrix 第三层规则形同虚设 |
| 所有 Claim 最终都落到 INSUFFICIENT | 无法输出 supported 的 Claim |

### 改造目标

1. **Pair 级三层验证**：每个 Claim × Evidence pair 都经过 Rule + NLI + LLM 三层判断
2. **Batch 调用**：多个 pair 打包成一批，一次 LLM 调用完成，控制成本
3. **向后兼容**：无 LLM 配置时退化为纯 Rule 层
4. **预算可控**：受 `VerificationBudget` 约束，不超限

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────┐
│                    CitationBinder.bind_async()            │
│                                                          │
│  Claim 1 ─┬─ Evidence A ─→ PairVerifier.verify_pair()    │
│            ├─ Evidence B ─→ PairVerifier.verify_pair()    │  ┐
│            └─ Evidence C ─→ PairVerifier.verify_pair()    │  │
│                                                          │  │ Batch 1
│  Claim 2 ─┬─ Evidence A ─→ PairVerifier.verify_pair()    │  │ (5 pairs)
│            ├─ Evidence D ─→ PairVerifier.verify_pair()    │  │
│            └─ Evidence E ─→ PairVerifier.verify_pair()    │  ┘
│                                                          │
│                              ↓ 收集所有 pair              │
│                    ┌───────────────────┐                  │
│                    │  PairVerifier     │                  │
│                    │  .verify_batch()  │                  │
│                    └────────┬──────────┘                  │
│                             │                             │
│                     ┌───────▼───────┐                     │
│                     │  LLM 调用 1   │  ← 5 个 pair 一批   │
│                     │  LLM 调用 2   │                     │
│                     │  ...          │                     │
│                     └───────────────┘                     │
│                                                          │
│  ← 每个 pair 拿到 {rule_result, nli_result, llm_result}  │
│  ← VerdictMatrix 三层判定自动跑通                         │
└──────────────────────────────────────────────────────────┘
```

---

## 3. PairVerifier 组件设计

### 3.1 类结构

```
PairVerifier
├── verify_pair()          # 单 pair 验证入口（内部走 batch 调度）
├── verify_batch()         # ★ 核心：批量验证，分组调 LLM
├── _rule_check()          # Layer 1: Rule 层（本地正则）
├── _llm_verify_batch()    # Layer 2+3: NLI + LLM 层（一次 LLM 调用）
├── _build_batch_prompt()  # 构建 batch prompt
├── _parse_batch_response()# 解析 batch 响应
└── _aggregate_pair_status() # 三层结果聚合
```

### 3.2 Batch 调用流程

```
verify_batch(pairs)
    │
    ├── 1. Rule 层：对所有 pair 执行本地正则验证（同步，0 成本）
    │
    ├── 2. 短路过滤：Rule 矛盾 / Rule 支持+实体 的 pair 跳过 LLM
    │
    ├── 3. 剩余 pair 按 BATCH_SIZE 分组（默认 5 个/组）
    │
    ├── 4. 对每组调用一次 LLM：
    │      ┌─────────────────────────────────────┐
    │      │ 请分别判断以下各组证据和声明的关系：    │
    │      │                                      │
    │      │  Pair 1:                             │
    │      │    证据：公司 X 2024 年营收 15.3 亿  │
    │      │    声明：公司 X 营收 15.3 亿         │
    │      │  Pair 2:                             │
    │      │    证据：该政策于 2024-03-01 生效    │
    │      │    声明：该政策 2024 年 3 月生效     │
    │      │  ...                                 │
    │      │                                      │
    │      │  输出 JSON：                         │
    │      │  [{"pair_index":1, "nli":"entailment",│
    │      │    "llm":"SUPPORTED"}, ...]           │
    │      └─────────────────────────────────────┘
    │
    ├── 5. 解析响应，按 pair_index 回填到各 pair
    │
    └── 6. 返回 List[PairVerdict]（含三层结果）
```

### 3.3 短路策略（成本控制关键）

```
                    ┌─────────────────────┐
                    │  Rule 层（0 成本）   │
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │ Rule = CONTRADICTED? │
                    └──────────┬──────────┘
                       Yes     │     No
                    ┌──────────┘ └──────────┐
                    │                       │
        跳过 batch              ┌───────────▼───────────┐
        nli=contradiction       │ Rule = SUPPORTED +    │
        llm=CONTRADICTED        │ 有精确实体（可配）？  │
                                └───────────┬───────────┘
                                   Yes      │      No
                                ┌───────────┘ └───────────┐
                                │                         │
                    跳过 batch                 加入 batch 队列
                    nli=entailment              等待 LLM 调用
                    llm=SUPPORTED
```

**短路默认配置**：

| Rule 结果 | 是否跳过 LLM | 原因 |
|-----------|-------------|------|
| CONTRADICTED | 跳过 | 正则已检测到明确矛盾，无需语义确认 |
| SUPPORTED + 有精确实体 | 跳过 | 数值/日期/专名精确匹配，LLM 不会更好 |
| SUPPORTED + 无精确实体 | 不跳过 | 语义级确认，防正则误判 |
| NOT_SUPPORTED | 不跳过 | 规则不匹配不代表不支持，需语义兜底 |

---

## 4. 与 VerdictMatrix 的对接

### 4.1 三层数据流

```
PairVerdict 结构（改造后）：
{
    "evidence_id": "ev_001",
    "pair_status": "supported",          # 聚合后的终态
    "verifier_status": "ok",
    "rule_result": "SUPPORTED",          # ← 已有，不变
    "nli_result": "entailment",          # ← 原来空，现在填满
    "llm_result": "SUPPORTED",           # ← 原来空，现在填满
    "is_counter_evidence": false
}
```

### 4.2 VerdictMatrix 规则命中变化

| 规则 | 改造前 | 改造后 |
|------|--------|--------|
| VERIFIER_STATUS_IN → VERIFIER_ERROR | 能命中 | 不变 |
| ANY_LAYER_CONTRADICTED → CONTRADICTED | 仅检查 rule 层 | **三层全检查** |
| NO_VERIFIED_SUPPORT → INSUFFICIENT | 能命中 | 不变 |
| **ALL_LAYERS_SUPPORTED → SUPPORTED** | **永远不命中**（nli/llm 为空） | **三层全支持时命中** |
| DEFAULT → INSUFFICIENT | 兜底 | 兜底 |

**关键变化**：`_all_layers_supported` 检查 `rule_result=="SUPPORTED" && nli_result=="entailment" && llm_result=="SUPPORTED"`，三层数据齐全后能正确判定 supported。

---

## 5. 预算控制

### 5.1 LLM 调用预算

```
VerificationBudget:
    max_llm_calls: 50          ← 整轮最大 LLM 调用次数
    max_total_pairs: 150       ← 整轮最大 pair 数
    per_pair_timeout_ms: 3000  ← 单 pair 超时（batch 内按 pair 均摊）
    total_verification_timeout_ms: 30000  ← 整轮超时
```

### 5.2 Batch 模式下的预算计算

```
场景：10 个 Claim，每个命中 3 条证据
总 pair 数：30 对
短路跳过：~30%（9 对跳过 LLM）
剩余 pair：21 对
Batch 大小：5 对/批
LLM 调用次数：ceil(21 / 5) = 5 次
```

| 场景 | 总 pair 数 | 短路率 | 剩余 pair | Batch 大小 | LLM 调用次数 |
|------|-----------|--------|-----------|-----------|-------------|
| 典型 | 30 | 30% | 21 | 5 | 5 |
| 密集 | 60 | 20% | 48 | 5 | 10 |
| 稀疏 | 10 | 50% | 5 | 5 | 1 |
| 极端 | 150 | 10% | 135 | 5 | 27 |

所有场景均在 `max_llm_calls=50` 预算内。

### 5.3 预算耗尽降级

```
LLM 调用次数达到 max_llm_calls 后：
    → 剩余未验证 pair 退化为纯 Rule 层
    → PairVerdict.verifier_status = "budget_exhausted"
    → VerdictMatrix 检测到 budget_exhausted → 判为 VERIFIER_ERROR
```

---

## 6. 部分失败处理

### 6.1 Batch 内部分 pair 失败

```
LLM 响应中可能部分 pair 解析成功、部分失败：
{
    "pair_verdicts": [
        {"pair_index": 1, "nli": "entailment", "llm": "SUPPORTED"},
        {"pair_index": 2, "nli": "neutral", "llm": null},    ← 解析失败
        {"pair_index": 3, "nli": "entailment", "llm": "SUPPORTED"}
    ]
}
```

处理策略：
1. 成功 pair → 正常使用三层结果
2. 失败 pair → 退化为 Rule 层结果（Rule 层已执行，数据可用）
3. 记录 `partial_failure=true`，日志告警

### 6.2 整批失败

```
LLM 调用超时 / 返回非 JSON / 全部解析失败
    → 该 batch 内所有 pair 退化为 Rule 层
    → 不重试（避免 LLM 雪崩）
    → 由 PolicyEngine 的 verifier_error 比例检查兜底
```

---

## 7. 与现有组件的接口

### 7.1 CitationBinder 改造

```python
class CitationBinder:
    def __init__(
        self,
        snapshot: EvidenceSnapshot,
        tenant_id: str,
        budget: VerificationBudget | None = None,
        pair_verifier: PairVerifier | None = None,  # ★ 新增
    ):
        ...
        self.pair_verifier = pair_verifier  # None 时退化为纯 Rule 层

    async def bind_async(self, claims: list[Claim], answer_text: str) -> list[Claim]:
        """异步版 bind（支持 LLM 调用）。"""
        # 同步部分（引用解析、预算检查）不变
        # 验证部分改为 await self._verify_pair_async()
```

### 7.2 hallucination_node 改造

```python
async def hallucination_node(state):
    # ... fail-closed 拦截不变 ...

    # 构建 PairVerifier（ENFORCED 模式下）
    if enforcement_mode == EnforcementMode.ENFORCED:
        llm_id = _resolve_tenant_llm_id(tenant_id)
        if llm_id:
            pair_verifier = PairVerifier(
                llm_id=llm_id,
                tenant_id=tenant_id,
                batch_size=5,  # 可配置
            )
            binder = CitationBinder(
                snapshot=snapshot,
                tenant_id=tenant_id,
                budget=VerificationBudget(),
                pair_verifier=pair_verifier,
            )
            claims = await binder.bind_async(claims, answer_text)
            # ... VerdictMatrix + PolicyEngine ...

    # 无 LLM → 退化为原有 VerifierGateway 路径
```

### 7.3 无需改造的组件

| 组件 | 原因 |
|------|------|
| VerdictMatrix | 三层判定逻辑已就绪，只等数据填充 |
| PolicyEngine | 加权评分逻辑不变 |
| answer_renderer | 只关注 final_status，不关心三层来源 |
| EvidenceSnapshot | 不变，仍为不可变快照 |

---

## 8. 成本与收益分析

### 8.1 成本对比

| 维度 | 改造前（纯 Rule） | 改造后（Rule + Batch NLI/LLM） |
|------|-----------------|-------------------------------|
| LLM 调用/轮 | 0 次 | 3~10 次 |
| 单次 prompt 长度 | 0 | ~3000 token（5 个 pair） |
| 总 token/轮 | 0 | ~15K~30K |
| 额外延迟 | 0 | 1~3 秒（并发） |
| 准确率 | 差（正则无法处理语义） | 好（三层验证） |

### 8.2 收益

1. **Claim 可判 supported**：VerdictMatrix 的 ALL_LAYERS_SUPPORTED 规则能正确命中
2. **矛盾检测更准**：NLI 层能检测语义矛盾（如"营收增长" vs "营收下降"）
3. **不足判定更智能**：LLM 能判断"没说，但不矛盾"和"说了，但没证据"的区别
4. **反证扫描更可靠**：LLM 能发现语义层面的冲突，而非仅实体重叠

---

## 9. 配置项

```yaml
# rag_enhancement.yaml 新增配置段
hallucination:
  pair_verifier:
    enabled: true                     # 是否启用 PairVerifier
    batch_size: 5                     # 每批 pair 数
    skip_llm_when_rule_contradicted: true   # Rule 矛盾时跳过 LLM
    skip_llm_when_rule_supported: true      # Rule 支持+实体时跳过 LLM
    temperature: 0.1                  # LLM 温度
    timeout_seconds: 30               # 单次 LLM 调用超时
    max_retries_per_batch: 1          # batch 失败重试次数
```

---

## 10. 验收标准

| 验收项 | 预期结果 |
|--------|---------|
| 10 Claim × 3 Evidence 场景 | LLM 调用 ≤ 6 次（batch=5） |
| Claim 明确被证据支持 | final_status = supported |
| Claim 与证据矛盾 | final_status = contradicted |
| 证据不足 | final_status = insufficient |
| 验证器异常 | final_status = verifier_error |
| 无 LLM 配置 | 退化为纯 Rule 层，兼容原有行为 |
| 预算耗尽 | 剩余 pair 退化为 Rule 层，不抛异常 |
| 部分 batch 失败 | 失败 pair 降级为 Rule 层，不影响其他 pair |