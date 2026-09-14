# Token 预算调度器 — 正式设计文档

> **版本**: v2.0  
> **状态**: 设计完成  
> **最后更新**: 2026-08-28  

---

## 一、背景与目标

### 1.1 问题

在多工具（RAG + DB + Web）并存的 Agentic RAG 系统中，当检索返回的文档总长度超过 LLM 上下文窗口时，简单的"截断"会导致关键信息丢失。

### 1.2 核心原则

- **System Prompt 和当前对话是最高优先级**，不可压缩
- **有限的上下文窗口是"稀缺资源"**，需要动态预算分配
- **不同类型数据采用不同压缩策略**：DB 表格 vs RAG 文本块 vs Web 摘要
- **按价值密度排序**：高置信度优先装入，低价值优先牺牲
- **按实际数据量决策**：不着眼于权重，着眼于工具实际返回了多少 Token
- **只压缩超标工具**：≤30K 的工具直接豁免，不参与压缩

### 1.3 设计目标

| 目标 | 描述 |
|------|------|
| 不超限 | 组装后的 Prompt 始终 ≤ 模型窗口的 85%（留 15% 给输出） |
| 不浪费 | 未用尽的配额二次分配给其他工具 |
| 不丢信息 | 优先做语义压缩（LLM 抽取式），而非简单丢弃 |
| 可降级 | 极端情况下自动进入精简模式，保证系统可用 |
| 按需压缩 | total ≤ budget 时零开销，只压缩超标工具 |

---

## 二、现状分析

### 2.1 已有基础（可直接复用）

| 组件 | 位置 | 说明 |
|------|------|------|
| Token 计数器 | `common/token_utils.py` | `num_tokens_from_string()` 基于 `cl100k_base` |
| Evidence 评分 | `agent/langgraph/evidence/fusion.py#L35` | `_evidence_weight()` = authority×0.4 + relevance×0.4 + freshness×0.2 |
| 来源配额 | `agent/langgraph/nodes/evidence_fusion.py#L41` | `DEFAULT_SOURCE_QUOTA` 静态条数配额 |
| Evidence 融合 | `agent/langgraph/evidence/fusion.py#L74` | `fuse_evidences()` 去重+排序，`token_budget` 参数已存在 |
| 状态压缩 | `agent/langgraph/state_fields.py#L270` | `clamp_state_size()` 1MB 字节级兜底 |
| Token 截断 | `common/token_utils.py#L85` | `truncate(string, max_len)` |
| ReAct 预算 | `agent/langgraph/react/budget.py` | `token_budget` 用于子图终止判断 |

### 2.2 核心差距

| 差距 | 影响 |
|------|------|
| `fuse_evidences` 的 `token_budget` 未实际使用，仅作元数据传递 | 有预算概念但无实际约束 |
| `_format_evidence_context` 不做 Token 实时监控 | 多工具场景下可能超限 |
| 来源配额是**静态条数**（rag:10, db:10），非动态 Token 预算 | 单工具和多工具分配不合理 |
| 单条 2000 字符硬截断，无语义压缩 | 短文档浪费空间，长文档丢信息 |
| 无"精简模式"标志 | 预算紧张时 LLM 仍尝试详细回答 |

### 2.3 RAG 检索粒度澄清

经过代码分析，确认 RAG 检索的粒度是 **chunk 而非完整文档**：

- 默认 chunk 大小：**512 tokens**（约 400-500 中文字符），可配置
- 检索返回的是单个 chunk，`content` 字段来自 `content_with_weight`
- 存在 Parent Document Retrieval 机制（`retrieval_by_children`），子 chunk 检索后合并回父 chunk
- 2000 字符截断对正常 chunk 基本是 no-op（chunk 本身远小于 2000 字符）

**因此，压缩策略的核心是"减少 chunk 数量 + LLM 抽取式压缩"，而非"截断单个 chunk 内容"。**

### 2.4 典型数据量分布

| 工具 | 典型返回量 | 说明 |
|------|-----------|------|
| RAG | 3~12K tokens（20 chunk） | 粗排 100 → 精排 20 → 动态截断，基本稳定可控 |
| DB | 1K~200K+ tokens | 几十到几千行，波动极大，最容易超标 |
| Web | 1K~10K tokens | 基本可控 |

**关键洞察**：RAG 返回量稳定、超额可能性低，DB 返回量波动极大、经常爆炸。因此固定权重分配配额（RAG 55% / DB 25%）存在根本性缺陷——RAG 配额大量浪费，DB 配额严重不足。

---

## 三、架构设计

### 3.1 新增/修改文件

```
agent/langgraph/evidence/
├── models.py              # 不变
├── fusion.py              # 修改：fuse_evidences 接入实际 Token 预算
├── token_budget.py        # 新增：TokenBudgetScheduler 核心类
└── ...
agent/langgraph/nodes/
├── evidence_fusion.py     # 修改：动态预算分配 + LLM 压缩触发
├── prompt_assembly.py     # 修改：Token 感知装配 + 精简模式
└── ...
agent/langgraph/
├── state.py               # 修改：新增 Token 预算字段
└── ...
```

### 3.2 数据流

```
工具结果 (rag_docs / db_result / web_docs)
  │
  ▼
evidence_fusion_node
  │ 1. 标准化为 Evidence
  │ 2. _detect_active_tools → ["rag", "db"]
  │ 3. _count_tool_tokens → {"rag": 12000, "db": 180000}
  │ 4. _calculate_overflow_ratio → 0.34 (34%)
  │ 5. determine_level(overflow_ratio) → "moderate"
  │ 6. _identify_overflown_tools → overflown=["db"], exempt=["rag"]
  │ 7. 对超标工具按级别执行压缩，豁免工具保持原样
  ▼
prompt_assembly_node
  │ 1. 锁定 System Prompt + 历史 → 计算已用 Token
  │ 2. TokenBudgetScheduler.assemble(evidences, remaining_budget)
  │    → 按 source_type 分组 + 组内按权重降序
  │    → 逐条装配，实时 Token 监控
  │    → 剩余 < OUTPUT_RESERVE 时停止
  │ 3. 若 compact_mode → 追加精简指令
  │ 4. 构建最终 Prompt
  ▼
LLM 调用
```

---

## 四、AgentState 新增字段

```python
# agent/langgraph/state.py — AgentState TypedDict 新增

# ========== Token 预算调度器 ==========

# 总 Token 预算上限（默认 128K × 0.85 = 110K）
total_token_budget: int

# 已锁定 Token（System Prompt + Tool Schema），启动时 tiktoken 计算一次
locked_system_tokens: int

# 已锁定 Token（最近 N 轮对话历史 + 摘要）
locked_history_tokens: int

# 当前激活的工具列表，如 ["rag", "db", "web"]
active_tools: list[str]

# 各工具实际返回的 Token 数
tool_token_counts: dict  # {"rag": 12000, "db": 180000, "web": 5000}

# 超标工具列表（> 30K 豁免阈值）
overflown_tools: list[str]  # ["db"]

# 当前压缩级别：normal / light / moderate / severe / compact
compression_level: str

# 精简模式标志（剩余预算 < 2000 时置为 True）
compact_mode: bool
```

---

## 五、TokenBudgetScheduler 核心（新增文件）

### 5.1 配置常量

```python
# agent/langgraph/evidence/token_budget.py

from common.token_utils import num_tokens_from_string

# ─── 模型窗口 ───
DEFAULT_MODEL_WINDOW = 128000
MODEL_WINDOW_RATIO = 0.85          # 留 15% 给输出
OUTPUT_RESERVE_TOKENS = 2000       # 输出预留，永远不可侵占

# ─── 单工具豁免阈值 ───
# 单个工具返回结果 ≤ 此值，不参与压缩
# 30K ≈ 60 个 RAG chunk 或 1000 行 DB 紧凑数据
PER_TOOL_EXEMPTION_TOKENS = 30000

# ─── 溢出比例 → 压缩级别 ───
# 设计依据：
#   0-30%:   差得不多，低价值过滤即可，不值得调用 LLM
#   30-80%:  需要实质性压缩，但还能保留关键信息
#   80-150%: 数据量远超窗口，必须激进压缩
#   >150%:   数据爆炸，只能留摘要兜底
OVERFLOW_THRESHOLDS = {
    "light":    0.30,   # 溢出 ≤ 30%
    "moderate": 0.80,   # 溢出 ≤ 80%
    "severe":   1.50,   # 溢出 ≤ 150%
    # > 150% → compact
}

# 单条 Evidence 最低保留 Token（低于此值直接丢弃整条）
MIN_EVIDENCE_TOKENS = 50

# 来源装配顺序
SOURCE_ORDER = ["rag", "db", "web", "report"]
```

### 5.2 类结构

```python
class TokenBudgetScheduler:
    """Token 预算调度器。

    职责：
    1. 统计各工具实际返回的 Token 数
    2. 计算总溢出比例，确定压缩级别
    3. 识别超标工具（> 30K），豁免低量工具
    4. 对超标工具按级别执行压缩策略
    5. 对 RAG chunks 执行 LLM 抽取式压缩
    6. 对 DB 结果执行行级截断 + 列级裁剪
    7. 逐条装配 Evidence 为 Prompt 文本，实时 Token 监控
    8. 精简模式兜底
    """

    def __init__(self, total_budget: int | None = None):
        self.total_budget = total_budget or int(DEFAULT_MODEL_WINDOW * MODEL_WINDOW_RATIO)
```

### 5.3 工具 Token 计数

```python
    def _count_tool_tokens(self, state: AgentState) -> dict[str, int]:
        """统计每个工具返回结果的 Token 数。

        使用 tiktoken 精确计算，确保阈值判定准确。
        """
        counts = {}

        # RAG
        rag_tokens = 0
        for doc in state.get("rag_docs", []):
            rag_tokens += num_tokens_from_string(doc.get("content", ""))
        if rag_tokens > 0:
            counts["rag"] = rag_tokens

        # DB
        db_result = state.get("db_result") or {}
        db_tokens = 0
        for row in db_result.get("rows", []):
            db_tokens += num_tokens_from_string(str(row))
        if db_tokens > 0:
            counts["db"] = db_tokens

        # Web
        web_tokens = 0
        for doc in state.get("web_docs", []):
            web_tokens += num_tokens_from_string(doc.get("content", ""))
        if web_tokens > 0:
            counts["web"] = web_tokens

        return counts
```

### 5.4 溢出比例计算与压缩级别判定

```python
    def _calculate_overflow_ratio(
        self,
        tool_token_counts: dict[str, int],
        locked_tokens: int,
    ) -> float:
        """计算总溢出比例。

        Args:
            tool_token_counts: {"rag": 12000, "db": 180000, "web": 5000}
            locked_tokens: System + History 的 Token 数

        Returns:
            溢出比例，≤ 0 表示不溢出
        """
        total_evidence = sum(tool_token_counts.values())
        available = self.total_budget - locked_tokens - OUTPUT_RESERVE_TOKENS

        if total_evidence <= available:
            return 0.0

        return (total_evidence - available) / available

    def determine_level(self, overflow_ratio: float) -> str:
        """根据溢出比例确定压缩级别。"""
        if overflow_ratio <= 0:
            return "normal"
        elif overflow_ratio <= OVERFLOW_THRESHOLDS["light"]:
            return "light"
        elif overflow_ratio <= OVERFLOW_THRESHOLDS["moderate"]:
            return "moderate"
        elif overflow_ratio <= OVERFLOW_THRESHOLDS["severe"]:
            return "severe"
        else:
            return "compact"
```

### 5.5 超标工具识别

```python
    def _identify_overflown_tools(
        self,
        tool_token_counts: dict[str, int],
    ) -> tuple[list[str], list[str]]:
        """识别超标工具和豁免工具。

        Returns:
            (overflown_tools, exempt_tools)
        """
        overflown = []
        exempt = []
        for tool, tokens in tool_token_counts.items():
            if tokens > PER_TOOL_EXEMPTION_TOKENS:
                overflown.append(tool)
            else:
                exempt.append(tool)
        return overflown, exempt
```

### 5.6 LLM 抽取式压缩

```python
    async def compress_rag_chunks(
        self,
        chunks: list[dict],
        question: str,
        level: str,
        llm,  # 低成本 LLM 实例
    ) -> list[dict]:
        """对 RAG chunks 进行 LLM 抽取式压缩。

        并行处理多个 chunk，每个 chunk 独立压缩。
        仅提取与问题相关的原句，不做改写。

        Args:
            chunks: RAG 检索结果（已按 rerank 过滤）
            question: 用户原始问题
            level: 压缩级别（moderate/severe/compact）
            llm: 低成本 LLM 实例（如 GPT-4o-mini）

        Returns:
            压缩后的 chunks（content 字段被替换为压缩文本）
        """
        if level in ("normal", "light"):
            return chunks  # normal/light 不压缩

        prompt_template = COMPRESS_PROMPTS[level]

        async def compress_one(chunk: dict) -> dict:
            content = chunk.get("content", "")
            tokens = num_tokens_from_string(content)

            # 短 chunk（< 100 tokens）不需要压缩
            if tokens < 100:
                return chunk

            prompt = prompt_template.format(
                question=question,
                chunk_text=content,
            )

            try:
                response = await llm.chat(prompt)
                compressed = response.strip()

                compressed_tokens = num_tokens_from_string(compressed)

                # 安全检查：压缩后不应比原文更长
                if compressed_tokens > tokens * 0.9:
                    return chunk

                # 检查是否返回了"无相关内容"
                if "无相关内容" in compressed:
                    chunk["content"] = ""
                    chunk["compressed"] = True
                    return chunk

                chunk["content"] = compressed
                chunk["compressed"] = True
                chunk["compression_ratio"] = compressed_tokens / tokens
                return chunk

            except Exception:
                return chunk  # 失败时保留原文

        return await asyncio.gather(*[compress_one(c) for c in chunks])
```

### 5.7 DB 行级截断 + 列级裁剪

```python
    def compress_db_evidence(
        self,
        db_result: dict,
        quota: int,
        level: str,
    ) -> dict:
        """对 DB 查询结果进行行级截断和列级裁剪。

        策略层级：
        - normal/light: 保留所有行，Markdown 表格
        - moderate: 行截断到 50 行，表级配额分配
        - severe: 紧凑格式，10 行，丢弃大文本列，仅保留最高相关性表
        - compact: 仅摘要（行数+数值统计+1 条样本）

        Args:
            db_result: {"rows": [...], "columns": [...], "sql": "...", "table_name": "..."}
            quota: 分配给该表结果的 Token 配额
            level: 压缩级别

        Returns:
            {"text": str, "truncated": bool, "original_rows": int, "kept_rows": int}
        """
        rows = db_result.get("rows", [])
        columns = db_result.get("columns", [])
        if not rows:
            return {"text": "（查询无结果）", "truncated": False, "original_rows": 0, "kept_rows": 0}

        original_rows = len(rows)

        if level in ("normal", "light"):
            # 完整保留，Markdown 表格
            return {
                "text": self._format_db_table(rows, columns),
                "truncated": False,
                "original_rows": original_rows,
                "kept_rows": original_rows,
            }

        elif level == "moderate":
            # 行截断到 50 行，表级配额分配
            max_rows = min(original_rows, 50)
            kept_rows = rows[:max_rows]
            return {
                "text": self._format_db_table(kept_rows, columns),
                "truncated": original_rows > max_rows,
                "original_rows": original_rows,
                "kept_rows": len(kept_rows),
            }

        elif level == "severe":
            # 紧凑格式 + 丢弃大文本列
            max_rows = min(original_rows, 10)
            text_columns = self._identify_text_columns(rows, columns)

            # 保留：标识列 + 数值列 + 分类列；丢弃大文本列
            keep_columns = [c for c in columns if c not in text_columns]
            if not keep_columns:
                keep_columns = columns  # 如果全是文本列，兜底保留

            kept_rows = rows[:max_rows]
            compact_text = self._format_compact_rows(kept_rows, keep_columns)
            return {
                "text": compact_text,
                "truncated": True,
                "original_rows": original_rows,
                "kept_rows": len(kept_rows),
            }

        else:  # compact
            # 仅摘要
            summary_text = self._format_db_summary(rows, columns)
            return {
                "text": summary_text,
                "truncated": True,
                "original_rows": original_rows,
                "kept_rows": 1,
            }
```

### 5.8 DB 格式化辅助方法

```python
    def _format_db_table(self, rows: list[dict], columns: list[str]) -> str:
        """格式化为 Markdown 表格。"""
        if not rows:
            return "（空结果）"
        header = "| " + " | ".join(columns) + " |"
        sep = "|" + "|".join(["---" for _ in columns]) + "|"
        body = "\n".join(
            "| " + " | ".join(str(row.get(c, "")) for c in columns) + " |"
            for row in rows
        )
        return f"{header}\n{sep}\n{body}"

    def _format_compact_rows(self, rows: list[dict], columns: list[str]) -> str:
        """紧凑行格式：col=val, col=val（每行一行）。"""
        lines = []
        for i, row in enumerate(rows):
            parts = [f"{c}={row.get(c, '')}" for c in columns]
            lines.append(f"[{i+1}] {', '.join(parts)}")
        return "\n".join(lines)

    def _format_db_summary(self, rows: list[dict], columns: list[str]) -> str:
        """总结格式：行数 + 数值列统计 + 1 条样本。"""
        if not rows:
            return "（空结果）"
        parts = [f"共 {len(rows)} 行。"]
        numeric_cols = self._identify_numeric_columns(rows, columns)
        for nc in numeric_cols:
            values = [float(row[nc]) for row in rows if row.get(nc) is not None]
            if values:
                parts.append(f"{nc}: sum={sum(values):.2f}, avg={sum(values)/len(values):.2f}")
        sample = rows[0]
        parts.append(f"样本: {self._format_compact_rows([sample], columns[:6])}")
        return "\n".join(parts)

    @staticmethod
    def _identify_text_columns(rows: list[dict], columns: list[str]) -> list[str]:
        """识别大文本列（平均长度 > 100 字符）。"""
        text_cols = []
        for col in columns:
            lengths = [len(str(row.get(col, ""))) for row in rows if row.get(col) is not None]
            if lengths and sum(lengths) / len(lengths) > 100:
                text_cols.append(col)
        return text_cols

    @staticmethod
    def _identify_numeric_columns(rows: list[dict], columns: list[str]) -> list[str]:
        """识别数值列。"""
        numeric_cols = []
        for col in columns:
            for row in rows:
                val = row.get(col)
                if val is not None and isinstance(val, (int, float)):
                    numeric_cols.append(col)
                    break
        return numeric_cols
```

### 5.9 逐条装配 + 实时 Token 监控

```python
    def assemble(
        self,
        evidences: list[dict],
        quota_map: dict[str, int],
        locked_tokens: int = 0,
    ) -> tuple[str, bool, int]:
        """按 Token 预算逐条装配 Evidence 为 Prompt 文本。

        装配顺序：rag → db → web → report
        每组内按 _evidence_weight 降序
        逐条格式化，累计 Token 数，超过配额或剩余 < OUTPUT_RESERVE 时停止

        Args:
            evidences: 融合后的 Evidence 列表
            quota_map: 各工具 Token 配额
            locked_tokens: 已锁定的 Token（System + History）

        Returns:
            (assembled_text, compact_mode, total_tokens_used)
        """
        available = self.total_budget - locked_tokens
        compact_mode = False

        if available <= OUTPUT_RESERVE_TOKENS:
            return self._compact_assemble(evidences, locked_tokens)

        # 按 source_type 分组
        groups: dict[str, list] = {}
        for ev in evidences:
            st = ev.get("source_type", "unknown")
            groups.setdefault(st, []).append(ev)

        # 组内按权重降序
        from agent.langgraph.evidence.fusion import _evidence_weight
        for st in groups:
            groups[st].sort(key=_evidence_weight, reverse=True)

        parts = ["【参考资料】"]
        global_idx = 0
        total_used = 0

        for st in SOURCE_ORDER:
            group = groups.get(st)
            if not group:
                continue
            quota = quota_map.get(st, 0)
            tool_used = 0

            for ev in group:
                if tool_used >= quota:
                    break
                if total_used + locked_tokens >= self.total_budget - OUTPUT_RESERVE_TOKENS:
                    compact_mode = True
                    break

                global_idx += 1
                content = self._format_evidence(ev, st)
                line = f"[{global_idx}] {content}"
                line_tokens = num_tokens_from_string(line)

                if tool_used + line_tokens > quota:
                    continue

                if total_used + line_tokens + locked_tokens > self.total_budget - OUTPUT_RESERVE_TOKENS:
                    compact_mode = True
                    break

                parts.append(line)
                tool_used += line_tokens
                total_used += line_tokens

        assembled = "\n".join(parts)
        return assembled, compact_mode, total_used

    def _format_evidence(self, ev: dict, source_type: str) -> str:
        """格式化单条 Evidence。"""
        content = ev.get("content", "")
        title = ev.get("title", "")
        score = ev.get("relevance_score") or ev.get("confidence", 0.0)

        if source_type == "rag":
            prefix = f"(相关度:{score:.2f}) "
            return prefix + content
        elif source_type == "db":
            return f"(来源:db, 相关度:{score:.2f}) [{title}] {content}"
        elif source_type == "web":
            return f"(来源:web, 相关度:{score:.2f}) [{title}] {content}"
        else:
            return f"(来源:{source_type}, 相关度:{score:.2f}) {content}"
```

### 5.10 精简模式兜底

```python
    def _compact_assemble(
        self, evidences: list[dict], locked_tokens: int
    ) -> tuple[str, bool, int]:
        """精简模式：仅保留 top 3 标题 + 首句。"""
        from agent.langgraph.evidence.fusion import _evidence_weight

        if not evidences:
            return "（无足够上下文，请直接回答）", True, 0

        sorted_evs = sorted(evidences, key=_evidence_weight, reverse=True)[:3]
        parts = ["【精简参考资料】"]
        for i, ev in enumerate(sorted_evs, 1):
            title = ev.get("title", "")
            content = ev.get("content", "")
            source_type = ev.get("source_type", "")
            first_sentence = content.split("。")[0][:200] if content else ""
            parts.append(f"[{i}] ({source_type}) {title}: {first_sentence}...")

        text = "\n".join(parts)
        return text, True, num_tokens_from_string(text)
```

### 5.11 LLM 抽取式压缩 Prompt

```python
COMPRESS_PROMPTS = {
    # 中度压缩：保留约一半相关句子
    "moderate": """你是一个精确的文本提取器。根据用户问题，从以下文本中提取与问题相关的句子。

规则：
1. 只提取原句，不要改写、不要总结、不要添加任何新信息
2. 保留原句顺序
3. 提取约一半的相关句子，优先保留包含关键事实、数据、定义的句子
4. 如果文本与问题完全无关，输出"无相关内容"

问题：{question}

文本：
{chunk_text}""",

    # 重度压缩：仅保留 2-3 个关键句
    "severe": """你是一个精确的文本提取器。根据用户问题，从以下文本中提取最关键的 2-3 个句子。

规则：
1. 只提取原句，不要改写、不要总结、不要添加任何新信息
2. 优先提取包含直接答案、核心数据、关键定义的句子
3. 如果文本与问题无关，输出"无相关内容"

问题：{question}

文本：
{chunk_text}""",

    # 精简模式：仅保留 1 个关键句
    "compact": """根据用户问题，从以下文本中提取最关键的 1 个句子或短语。

规则：
1. 只提取原句，不要改写
2. 如果文本与问题无关，输出"无相关内容"

问题：{question}

文本：
{chunk_text}""",
}
```

---

## 六、分级处理策略

### 6.1 核心流程

```
工具结果 Token 统计
  │
  ├─ Step 1: _count_tool_tokens → {"rag": 12K, "db": 180K, "web": 5K}
  │
  ├─ Step 2: total = 12K + 180K + 5K = 197K
  │
  ├─ Step 3: total ≤ available_budget ?
  │    YES → level = normal，所有工具不压缩，直接装配
  │    NO  → 进入 Step 4
  │
  ├─ Step 4: 单工具豁免检查
  │    对每个工具: tool_tokens ≤ 30K ?
  │      YES → 标记为豁免，不压缩
  │      NO  → 标记为超标，参与压缩
  │
  ├─ Step 5: 计算溢出比例
  │    overflow_ratio = (total - available_budget) / available_budget
  │
  ├─ Step 6: 确定压缩级别
  │    overflow_ratio ≤ 0     → normal
  │    overflow_ratio ≤ 0.30  → light
  │    overflow_ratio ≤ 0.80  → moderate
  │    overflow_ratio ≤ 1.50  → severe
  │    overflow_ratio > 1.50  → compact
  │
  └─ Step 7: 对超标工具按级别执行压缩策略
       豁免工具保持原样不动
```

### 6.2 压缩级别总览

| 级别 | 溢出比例 | 触发条件 | 核心策略 |
|------|---------|---------|---------|
| normal | ≤ 0% | total ≤ budget | 不压缩，所有工具原样保留 |
| light | 0~30% | 轻度溢出 | 低价值过滤，不调用 LLM |
| moderate | 30~80% | 中度溢出 | 语义压缩 + 行截断 |
| severe | 80~150% | 重度溢出 | 深度压缩 + 列裁剪 |
| compact | > 150% | 数据爆炸 | 仅摘要，全局精简 |

### 6.3 各级别详细策略

#### normal — 不压缩（溢出 ≤ 0%）

| 工具 | 处理 |
|------|------|
| 所有 | 原样保留，直接装配 |

#### light — 轻度溢出（0~30%）

| 工具 | 超标时 | 豁免时（≤ 30K） |
|------|--------|----------------|
| RAG | rerank_score < 0.5 的 chunk 丢弃 | 不处理 |
| DB | 行截断到 100 行，完整列 | 不处理 |
| Web | 每条 500→300 字符，max 6→4 条 | 不处理 |

#### moderate — 中度溢出（30~80%）

| 工具 | 超标时 | 豁免时（≤ 30K） |
|------|--------|----------------|
| RAG | LLM 抽取式压缩到 50%（并行调用低成本 LLM） | 不处理 |
| DB | 行截断到 50 行 + 多表按行数加权分配配额 | 不处理 |
| Web | 每条 200 字符，max 3 条 | 不处理 |

#### severe — 重度溢出（80~150%）

| 工具 | 超标时 | 豁免时（≤ 30K） |
|------|--------|----------------|
| RAG | LLM 压缩到 30%（2-3 个关键句） | 不处理 |
| DB | 10 行 + 丢弃大文本列 + 紧凑格式 `col=val` + 多表仅保留 1 张 | 不处理 |
| Web | 丢弃 | 不处理 |

#### compact — 数据爆炸（> 150%）

| 工具 | 处理（此级别豁免无效，全局精简） |
|------|------|
| 所有 | top 3 Evidence（不限来源），标题 + 首句 200 字符 |
| DB | 仅摘要：行数 + 数值列统计（sum/avg） + 1 条样本 |
| Prompt | 追加精简指令："仅输出核心结论（1-3 句话），不要展开详细分析" |

---

## 七、集成改造

### 7.1 evidence_fusion_node 改造

```python
# agent/langgraph/nodes/evidence_fusion.py

async def evidence_fusion_node(state: AgentState) -> dict[str, Any]:
    # ... 前面标准化、合并、来源配额逻辑不变 ...

    # ★ 新增：检测激活工具
    active_tools = _detect_active_tools(state)
    total_budget = state.get("total_token_budget", 110000)
    scheduler = TokenBudgetScheduler(total_budget=total_budget)

    locked_tokens = (
        state.get("locked_system_tokens", 0) +
        state.get("locked_history_tokens", 0)
    )

    # ★ 新增：统计各工具实际 Token
    tool_token_counts = scheduler._count_tool_tokens(state)

    # ★ 新增：计算溢出比例 → 判定压缩级别
    overflow_ratio = scheduler._calculate_overflow_ratio(
        tool_token_counts, locked_tokens
    )
    level = scheduler.determine_level(overflow_ratio)

    # ★ 新增：识别超标工具
    overflown_tools, exempt_tools = scheduler._identify_overflown_tools(
        tool_token_counts
    )

    # 融合（仅对超标工具施加配额约束）
    fusion_result = fuse_evidences(
        evidences=quota_applied,
        max_count=DEFAULT_MAX_EVIDENCE_COUNT,
        token_budget=sum(tool_token_counts.values()),
    )

    # ★ 新增：LLM 抽取式压缩（仅对超标 RAG 工具）
    if level in ("moderate", "severe", "compact") and "rag" in overflown_tools:
        rag_evidences = [
            e for e in fusion_result.fused_evidences
            if e.get("source_type") == "rag"
        ]
        if rag_evidences:
            compression_llm = get_lightweight_llm()
            compressed = await scheduler.compress_rag_chunks(
                chunks=rag_evidences,
                question=state["user_question"],
                level=level,
                llm=compression_llm,
            )
            for i, ev in enumerate(fusion_result.fused_evidences):
                if ev.get("source_type") == "rag":
                    fusion_result.fused_evidences[i] = compressed.pop(0)

    return {
        "evidence": fusion_result.fused_evidences,
        "active_tools": active_tools,
        "tool_token_counts": tool_token_counts,
        "overflown_tools": overflown_tools,
        "compression_level": level,
        "locked_system_tokens": locked_tokens,
        ...
    }


def _detect_active_tools(state: AgentState) -> list[str]:
    """检测当前请求激活了哪些工具。"""
    active = []
    if state.get("rag_docs"):
        active.append("rag")
    if state.get("db_result") and state["db_result"].get("rows"):
        active.append("db")
    if state.get("web_docs"):
        active.append("web")
    return active or ["rag"]
```

### 7.2 prompt_assembly_node 改造

```python
# agent/langgraph/nodes/prompt_assembly.py

def prompt_assembly_node(state: AgentState) -> dict[str, Any]:
    # ... 前面读取 state 逻辑不变 ...

    scheduler = TokenBudgetScheduler(
        total_budget=state.get("total_token_budget", 110000)
    )

    locked_tokens = (
        state.get("locked_system_tokens", 0) +
        state.get("locked_history_tokens", 0)
    )

    if evidences:
        tool_token_counts = state.get("tool_token_counts", {})
        evidence_context, compact_mode, used_tokens = scheduler.assemble(
            evidences=evidences,
            quota_map=tool_token_counts,
            locked_tokens=locked_tokens,
        )

        final_prompt = _build_citation_aware_prompt(
            question=user_question,
            evidence_context=evidence_context,
            lang=query_lang,
            enforcement_mode=enforcement_mode,
            conversation_history=conversation_history,
            conversation_summary=conversation_summary,
            compact_mode=compact_mode,  # ★ 新增
        )
    else:
        # 回退路径不变
        ...

    return {
        "merged_context": evidence_context,
        "final_prompt": final_prompt,
        "compact_mode": compact_mode,
        ...
    }


def _build_citation_aware_prompt(
    ...,
    compact_mode: bool = False,  # ★ 新增
) -> str:
    # ... 前面逻辑不变 ...

    if compact_mode:
        compact_instruction = (
            "\n【精简模式】上下文空间不足，请仅输出核心结论（1-3 句话），"
            "不要展开详细分析。"
        )
        # 在 evidence_context 之后、用户问题之前插入
        ...

    # ... 其余逻辑不变 ...
```

### 7.3 SQL Agent 列选择优化（源头削减）

在 SQL Agent 生成 SQL 的源头就限制 Token 消耗，避免生成包含不必要字段的 SELECT 查询。

**文件位置**：`agent/langgraph/tools/sql_agent/prompts.py`

**新增规则 3a**（在规则 3 "只生成只读 SELECT 查询" 之后）：

```python
# agent/langgraph/tools/sql_agent/prompts.py — 在 "工作规则" 中新增

3a. 字段选择原则（重要）：
    - 只 SELECT 回答用户问题所必需的字段，严禁 SELECT *
    - describe_table 后，先判断哪些列与问题相关，再写 SQL
    - 优先选择：数值列（用于聚合计算）、分类列（GROUP BY）、标识列（id/名称）、时间列
    - 避免选择：大文本列（description/note/content）、与问题无关的外键列、冗余状态列
    - 不确定某列是否必需时，宁可多选一列，不要漏掉关键列
```

**设计考量**：

| 考量 | 说明 |
|------|------|
| 位置 | 紧跟规则 3（SELECT 相关），形成认知关联 |
| 力度 | 建议性而非强制性（"宁可多选一列，不要漏掉"），避免过约束导致 SQL 错误 |
| 与大文本列的关系 | 与 DB 行级截断中 `_identify_text_columns` 形成呼应：源头避免 + 后置裁剪 |
| 兜底 | 即使 Prompt 未生效，后置的 `compress_db_evidence` 仍会丢弃大文本列 |

**配合效果**：

```
SELECT * FROM orders  →  SELECT id, amount, created_at, status FROM orders
  └─ 13 列，含大文本            └─ 4 列，无大文本
     Token 消耗 ↓ 60-70%
```

---

## 八、成本收益分析

### 8.1 LLM 抽取式压缩

| 场景 | 压缩前 | 压缩后 | 压缩成本 | 信息保留 |
|------|--------|--------|---------|---------|
| moderate（3 条 chunk） | 1536 tokens | 768 tokens | ~2300 tokens（低成本模型） | 3 条核心信息 |
| severe（2 条 chunk） | 1024 tokens | 306 tokens | ~1500 tokens（低成本模型） | 2 条关键句 |
| compact（1 条 chunk） | 512 tokens | 50 tokens | ~500 tokens（低成本模型） | 1 个关键句 |

> 压缩 LLM 使用低成本模型（如 GPT-4o-mini，约 $0.15/1M tokens），单次压缩成本约 $0.0003。

### 8.2 与丢弃方案的对比

| 级别 | 丢弃方案 | 压缩方案 | 收益 |
|------|---------|---------|------|
| moderate | 保留 1 条 chunk（512 tokens） | 保留 3 条压缩后（768 tokens） | 多保留 2 条信息，多耗 256 tokens |
| severe | 保留 1 条 chunk（512 tokens） | 保留 2 条压缩后（306 tokens） | 多保留 1 条信息，节省 206 tokens |

### 8.3 新方案 vs 固定权重方案

| 维度 | 固定权重方案 | 按实际溢出方案 |
|------|------------|--------------|
| 配额分配 | 基于业务价值权重 | 无配额，基于实际数据量 |
| 压缩触发 | remaining 空间 | total 溢出比例 |
| 压缩粒度 | 全局统一级别 | 按工具独立判定 |
| 单工具豁免 | 无 | ≤ 30K 自动豁免 |
| RAG 12K + DB 150K | RAG 和 DB 都参与 moderate 压缩 | 仅 DB 参与压缩，RAG 豁免 |
| 合理性 | 权重反映价值，不反映数据量 | 数据量驱动，按需压缩 |

---

## 九、实施计划

| 阶段 | 内容 | 改动 |
|------|------|------|
| **P0** | `token_budget.py` 核心类（Scheduler + 配置常量） | ~200 行新增 |
| **P0** | `_count_tool_tokens` + `_calculate_overflow_ratio` + `_identify_overflown_tools` | ~50 行新增 |
| **P0** | `determine_level` 改为基于 `overflow_ratio` | ~15 行修改 |
| **P0** | `state.py` 新增 Token 预算字段 | ~15 行 |
| **P0** | `evidence_fusion.py` 集成新流程 | ~50 行修改 |
| **P0** | `prompt_assembly.py` Token 感知装配 + 精简模式 | ~30 行修改 |
| **P1** | LLM 抽取式压缩 Prompt + `compress_rag_chunks` | ~80 行新增 |
| **P1** | `evidence_fusion.py` 集成 LLM 压缩触发 | ~20 行新增 |
| **P2** | 二次分配（`redistribute`） | ~40 行新增 |
| **P2** | 配置化（`agent_config.token_budget.*` 从 DB 读取） | ~20 行新增 |

**总计：约 520 行新增代码，零破坏性变更。**

---

## 十、附录：场景走查

### 场景 A：正常（不压缩）

```
available_budget = 110K

RAG:  12K (≤30K, 豁免)
DB:    8K (≤30K, 豁免)
Web:   3K (≤30K, 豁免)
─────────────────
total: 23K ≤ 110K → overflow = 0%
→ level = normal
→ 所有工具不压缩，直接装配
```

### 场景 B：DB 轻度超标，RAG/Web 豁免

```
RAG:  12K (≤30K, 豁免)
DB:  130K (>30K, 超标)
Web:   5K (≤30K, 豁免)
─────────────────
total: 147K, overflow = 34%
→ level = moderate
→ RAG: 豁免，不压缩
→ Web: 豁免，不压缩
→ DB:  行截断到 50 行
```

### 场景 C：DB 严重超标

```
RAG:  12K (≤30K, 豁免)
DB:  280K (>30K, 超标)
─────────────────
total: 292K, overflow = 165%
→ level = compact
→ 所有工具: top 3 Evidence 标题+首句
→ DB: 仅摘要 "共 5000 行，amount: sum=1.2M, avg=240"
→ 追加精简指令
```

### 场景 D：多工具同时超标

```
RAG:  50K (>30K, 超标)
DB:  180K (>30K, 超标)
Web:  40K (>30K, 超标)
─────────────────
total: 270K, overflow = 145%
→ level = severe
→ RAG:  LLM 压缩到 30%（2-3 个关键句）
→ DB:   10 行 + 丢弃大文本列 + 紧凑格式 + 仅 1 张表
→ Web:  丢弃
```

### 场景 E：RAG 超标但 total 未超（少见）

```
RAG:  80K (>30K, 超标)    ← 大量低质量检索
DB:   15K (≤30K, 豁免)
─────────────────
total: 95K ≤ 110K → overflow = 0%
→ level = normal
→ 不压缩

注：虽然 RAG 超过 30K，但 total 未超预算，所以不触发压缩。
    超预算才是压缩的触发条件，30K 只是"如果触发压缩，谁参与"的筛选。
```

### 场景 F：仅 RAGTool（top_k=5）

```
激活工具: ["rag"]
总预算: 110K
├── System: 5K  |  History: 3K  |  锁定: 8K
├── 剩余: 102K

RAG: 5 条 chunk × 512 tokens = 2.5K tokens
total: 2.5K ≤ 102K → level = normal
→ 全部完整保留，不压缩
compact_mode: False
```

### 场景 G：极度超限（历史对话很长，剩余 < 2K）

```
├── System: 5K  |  History: 103K  |  锁定: 108K
├── 剩余: 2K  → 无法装入任何 Evidence

RAG:  12K
DB:   50K
─────────────────
total: 62K, overflow = 3000% (available=2K)
→ level = compact
→ compact_mode: True
→ 仅保留 top 3 Evidence 标题 + 首句 ≈ 300 tokens
→ 追加精简指令
→ LLM 输出 1-3 句话结论
```

### 场景 H：多 DB 调用 + 行级截断 + 表级配额

```
激活工具: ["rag", "db"]
├── System: 5K  |  History: 15K  |  锁定: 20K
├── 剩余: 90K

假设 DB 查询了 3 张表，返回了大量数据：
├── orders 表: 5000 行 × 15 列（含 description 大文本列）→ 约 150K tokens
├── customers 表: 200 行 × 8 列 → 约 4K tokens
└── products 表: 100 行 × 5 列 → 约 1.5K tokens

→ DB 总返回 155.5K，远超 30K 豁免阈值！

系统处理流程：
  1. total = 155.5K + RAG 12K = 167.5K
  2. overflow = (167.5K - 90K) / 90K = 86%
  3. level = severe
  4. overflown_tools = ["db"], exempt_tools = ["rag"]
  5. RAG: 豁免，不压缩
  6. DB: severe 级别压缩
     ├── 行级截断：每表最多 10 行
     ├── 列级裁剪：丢弃 description 大文本列
     ├── 多表：仅保留行数最多的 1 张表（orders）
     ├── 格式：紧凑行 "col=val, col=val"
     └── 总消耗：10 行 × 10 列 × 15 tokens ≈ 1.5K
```

---

## 十一、配置常量汇总

```python
# ─── 模型窗口 ───
DEFAULT_MODEL_WINDOW = 128000
MODEL_WINDOW_RATIO = 0.85          # 留 15% 给输出
OUTPUT_RESERVE_TOKENS = 2000       # 输出预留，永远不可侵占

# ─── 单工具豁免阈值 ───
PER_TOOL_EXEMPTION_TOKENS = 30000  # ≤ 30K 自动豁免压缩

# ─── 溢出比例 → 压缩级别 ───
OVERFLOW_THRESHOLDS = {
    "light":    0.30,   # 溢出 ≤ 30%
    "moderate": 0.80,   # 溢出 ≤ 80%
    "severe":   1.50,   # 溢出 ≤ 150%
    # > 150% → compact
}

# ─── 其他 ───
MIN_EVIDENCE_TOKENS = 50           # 单条 Evidence 最低保留
SOURCE_ORDER = ["rag", "db", "web", "report"]
```