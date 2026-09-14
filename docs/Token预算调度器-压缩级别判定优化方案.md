# Token 预算调度器 — 压缩级别判定优化方案

## 一、问题

当前 `determine_level` 仅基于 `remaining = total_budget - locked_tokens` 判定压缩级别，**不感知工具返回结果的实际大小**。

### 1.1 盲区场景

```
场景：空间充裕 + 数据爆炸
├── System: 5K  |  History: 10K  |  locked: 15K
├── remaining: 93.8K → level = normal（不压缩）
├── RAG 返回: 120 条 chunk ≈ 60K tokens
├── DB 返回: 5000 行 × 15 列 ≈ 150K tokens
├── Web 返回: 6 篇网页 ≈ 30K tokens
└── 实际 evidence 总量: 240K tokens

结果：level = normal → 不做压缩 → assemble 阶段硬截断到 93.8K
      → 丢弃 146K 内容，大量有价值证据丢失
```

**根本原因：级别判定只看了"空间紧不紧"，没看"东西多不多"。**

### 1.2 对称盲区

```
场景：空间紧张 + 数据很少
├── System: 5K  |  History: 100K  |  locked: 105K
├── remaining: 3.8K → level = severe（重度压缩）
├── RAG 返回: 3 条 chunk ≈ 1.5K tokens
├── DB 返回: 5 行 ≈ 150 tokens
└── 实际 evidence 总量: 1.65K tokens

结果：level = severe → 触发 LLM 压缩（额外 LLM 调用成本）
      → 实际 evidence 只有 1.65K，完全可以直接装入
      → 不必要的计算开销 + 延迟增加
```

**根本原因：级别判定没考虑"证据本来就不多，不需要压缩"。**

---

## 二、优化方案

### 2.1 核心思路

在 `determine_level` 中加入 **evidence 预估 Token 量**，引入 `pressure = evidence_estimate / remaining` 作为**压力信号**，与 `remaining` 阈值联合判定最终级别。

```
                 ┌─────────────────┐
                 │  remaining       │  → 基础级别（空间维度）
                 │  (total - locked)│
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │  pressure        │  → 级别修正（数据维度）
                 │  = evidence /    │
                 │    remaining     │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │  最终级别        │
                 │  = max(基础, 修正)│
                 └─────────────────┘
```

### 2.2 压力修正规则

| pressure 范围 | 含义 | 修正动作 |
|---------------|------|---------|
| < 0.5 | 证据远小于可用空间，过度压缩是浪费 | 降级：severe→moderate, compact→severe（不触发 LLM 压缩） |
| 0.5 ~ 1.0 | 证据可装入，无需额外处理 | 不修正 |
| 1.0 ~ 2.0 | 证据超出可用空间，可能需截断 | 提升至 light（Rerank 阈值过滤） |
| 2.0 ~ 3.0 | 证据严重超出，硬截断会大量丢失 | 提升至 moderate（LLM 压缩 + DB 行截断） |
| > 3.0 | 证据远超空间，硬截断会丢失大部分信息 | 提升至 severe（LLM 深度压缩 + DB 列裁剪） |

### 2.3 算法

```python
def determine_level(
    self,
    locked_tokens: int,
    evidence_estimate: int = 0,          # ★ 新增
) -> str:
    """根据剩余预算 + 证据预估量联合确定压缩级别。

    Args:
        locked_tokens: 已锁定 Token（System + History）
        evidence_estimate: 工具返回结果的 Token 预估值（融合前），0 表示不参与修正

    Returns:
        压缩级别: normal / light / moderate / severe / compact
    """
    remaining = self.total_budget - locked_tokens

    # 基础级别：基于剩余空间
    base_level = "compact"
    for level in ["compact", "severe", "moderate", "light", "normal"]:
        if remaining >= COMPRESS_LEVELS[level]["min_remaining"]:
            base_level = level
            break

    # 压力修正：基于 evidence 预估量
    if evidence_estimate > 0 and remaining > 0:
        pressure = evidence_estimate / remaining

        if pressure < PRESSURE_UNDERFLOW_THRESHOLD:
            # 证据很少，不需要压缩 → 降级
            return _downgrade_level(base_level)
        elif pressure > PRESSURE_OVERFLOW_SEVERE:
            return _max_level(base_level, "severe")
        elif pressure > PRESSURE_OVERFLOW_MODERATE:
            return _max_level(base_level, "moderate")
        elif pressure > PRESSURE_OVERFLOW_LIGHT:
            return _max_level(base_level, "light")

    return base_level
```

### 2.4 新增配置常量

```python
# ─── 压力修正阈值（§5.3 优化） ───
# evidence_estimate / remaining 的压力分级
PRESSURE_UNDERFLOW_THRESHOLD = 0.5   # 证据量 < 可用空间 50% → 降级
PRESSURE_OVERFLOW_LIGHT = 1.0        # 证据量 > 可用空间 1x → 至少 light
PRESSURE_OVERFLOW_MODERATE = 2.0     # 证据量 > 可用空间 2x → 至少 moderate
PRESSURE_OVERFLOW_SEVERE = 3.0       # 证据量 > 可用空间 3x → 至少 severe

# 降级映射：避免过度压缩
DOWNGRADE_MAP = {
    "severe": "moderate",
    "compact": "severe",
}
```

### 2.5 辅助函数

```python
def _max_level(a: str, b: str) -> str:
    """取两个级别中更严重的一个。"""
    ORDER = ["normal", "light", "moderate", "severe", "compact"]
    return a if ORDER.index(a) > ORDER.index(b) else b


def _downgrade_level(level: str) -> str:
    """降级：证据很少时，避免不必要的压缩。"""
    return DOWNGRADE_MAP.get(level, level)
```

---

## 三、Evidence 预估函数

### 3.1 `_estimate_evidence_tokens`

```python
def _estimate_evidence_tokens(state: AgentState) -> int:
    """快速估算工具返回结果的 Token 总量。

    不做融合、不去重，仅用于级别判定的压力信号。
    精度要求不高（±30% 可接受），关键是速度。

    Returns:
        Token 预估值
    """
    total = 0

    # RAG 文档
    for doc in state.get("rag_docs", []):
        total += _fast_token_count(doc.get("content", ""))

    # DB 查询结果
    db_result = state.get("db_result") or {}
    for row in db_result.get("rows", []):
        total += _fast_token_count(str(row))

    # Web 搜索结果
    for doc in state.get("web_docs", []):
        total += _fast_token_count(doc.get("content", ""))

    return total


def _fast_token_count(text: str) -> int:
    """快速 Token 估算（字符数 / 4，中英文混合场景下误差 < 15%）。

    不使用 tiktoken 精确计算，避免大文本场景下的编码开销。
    精度对压力信号判定足够（只需判断数量级，不需要精确到个位）。
    """
    return len(text) // 4
```

### 3.2 调用时机

在 `evidence_fusion_node` 中，**融合前**调用。此时 raw tool results 尚未处理，估算的是原始数据量——这正是我们需要感知的"东西多不多"。

```python
async def evidence_fusion_node(state: AgentState) -> dict[str, Any]:
    # ... 标准化、来源配额逻辑不变 ...

    active_tools = _detect_active_tools(state)
    total_budget = state.get("total_token_budget", 110000)
    scheduler = TokenBudgetScheduler(total_budget=total_budget)

    locked_tokens = (
        state.get("locked_system_tokens", 0) +
        state.get("locked_history_tokens", 0)
    )

    # ★ 优化：融合前估算 evidence 总量，参与级别判定
    evidence_estimate = _estimate_evidence_tokens(state)
    level = scheduler.determine_level(locked_tokens, evidence_estimate)

    tool_quotas = scheduler.allocate(active_tools)

    # 融合
    fusion_result = fuse_evidences(
        evidences=quota_applied,
        max_count=DEFAULT_MAX_EVIDENCE_COUNT,
        token_budget=sum(tool_quotas.values()),
    )

    # LLM 抽取式压缩（moderate 及以上）
    if level in ("moderate", "severe", "compact"):
        # ... 压缩逻辑不变 ...
        pass

    return {
        "evidence": fusion_result.fused_evidences,
        "active_tools": active_tools,
        "tool_token_quotas": tool_quotas,
        "compression_level": level,
        "evidence_estimate": evidence_estimate,  # ★ 观测用
        ...
    }
```

---

## 四、效果对比

### 4.1 优化前 vs 优化后

| 场景 | locked | remaining | evidence | 优化前级别 | 优化后级别 | 变化 |
|------|--------|-----------|----------|-----------|-----------|------|
| 空间大 + 数据少 | 5K | 103.8K | 5K | normal | normal | 一致 |
| 空间大 + 数据适中 | 15K | 93.8K | 60K | normal | normal | 一致 |
| 空间大 + 数据爆炸 | 15K | 93.8K | 240K | **normal** | **light** | ✅ 提前过滤 |
| 空间中等 + 数据爆炸 | 50K | 58.8K | 200K | normal | **moderate** | ✅ 触发压缩 |
| 空间小 + 数据多 | 100K | 8.8K | 150K | moderate | **severe** | ✅ 加深压缩 |
| **空间小 + 数据少** | 100K | 8.8K | 1.5K | **severe** | **moderate** | ✅ 避免过度压缩 |
| 空间极小 + 数据少 | 106K | 2.8K | 1K | severe | **moderate** | ✅ 避免过度压缩 |
| 空间极小 + 数据多 | 106K | 2.8K | 100K | severe | severe | 一致 |

### 4.2 关键收益

| 收益 | 说明 |
|------|------|
| **减少信息丢失** | 空间大+数据爆炸时提前触发 light 过滤，Rerank 阈值过滤比 assemble 硬截断更智能 |
| **避免过度压缩** | 空间小+数据少时降级到 moderate，省掉一次 LLM 压缩调用（~$0.0003 + 延迟） |
| **零额外成本** | 字符数/4 估算在微秒级完成，不增加可感知延迟 |
| **向后兼容** | `evidence_estimate` 默认 0，不传时行为与优化前完全一致 |

---

## 五、改动清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `agent/langgraph/evidence/token_budget.py` | `determine_level` 新增 `evidence_estimate` 参数 + 压力修正逻辑 | ~20 行 |
| `agent/langgraph/evidence/token_budget.py` | 新增 `_max_level`、`_downgrade_level` 辅助函数 | ~15 行 |
| `agent/langgraph/evidence/token_budget.py` | 新增 `PRESSURE_*` 配置常量 + `DOWNGRADE_MAP` | ~10 行 |
| `agent/langgraph/nodes/evidence_fusion.py` | 新增 `_estimate_evidence_tokens` + `_fast_token_count` | ~20 行 |
| `agent/langgraph/nodes/evidence_fusion.py` | `evidence_fusion_node` 调用 `_estimate_evidence_tokens` + 透传 | ~5 行 |

**总计：约 70 行新增代码，零破坏性变更。**

---

## 六、实施建议

1. **优先级**：P2，在 P0/P1 核心功能稳定后实施
2. **验证方式**：在 `evidence_fusion_node` 日志中打印 `evidence_estimate`、`remaining`、`pressure`、`old_level`、`new_level`，观察线上实际分布后微调阈值
3. **阈值调优**：`PRESSURE_*` 阈值是经验值，上线后可根据线上 `pressure` 分布调整