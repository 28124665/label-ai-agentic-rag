# 对话记忆 Token 预算与摘要压缩改造方案

## 一、概述

### 目标

将当前"硬截断最近 10 条消息"的对话记忆机制，升级为：

1. **Step 1**：Token 预算感知的动态截断 — 按 Token 数而非固定条数控制窗口
2. **Step 2**：超出窗口的历史 → LLM 摘要压缩 — 被截断的消息不直接丢弃，而是压缩为摘要注入 Prompt

### 不做什么

- 不引入向量检索（那是 Step 3 的范畴）
- 不引入跨 conversation 的长期记忆
- 不改变 `conversation_history` 的对外接口签名（保持向后兼容）

---

## 二、现状分析

### 当前数据流

```
ConversationService._load_conversation_history(conversation_id, limit=10)
  → DB: SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 10
  → 反转正序
  → 返回 [{role, content}, ...]
  ↓
LangGraphRunner._build_initial_state(conversation_history=...)
  → 写入 AgentState["conversation_history"]
  ↓
prompt_assembly._format_conversation_history(history)
  → history[-10:] 硬截断
  → 格式化为 【对话历史】 段落
  → 注入 Prompt
```

### 当前问题

| 问题 | 影响 |
|------|------|
| 固定 10 条，不管长短 | 短消息浪费 Token 预算，长消息可能超出上下文窗口 |
| 超出 10 条直接丢弃 | 用户追问前面讨论的内容时无法回答 |
| 无 Token 感知 | 不同模型上下文窗口不同（4K / 8K / 32K / 128K），一刀切不合理 |

### 已有基础设施

- `common/token_utils.num_tokens_from_string(text: str) -> int`：tiktoken 封装的 Token 计数工具
- `grader.py` 中已有 Token 预算感知分批的先例（`_split_items_into_batches`）
- `Message` 表已有 `created_at` 排序索引

---

## 三、Step 1：Token 预算感知的动态截断

### 3.1 核心逻辑

将 `_load_conversation_history` 的 `limit=10` 改为 `max_tokens` 约束：

```python
async def _load_conversation_history(
    self,
    conversation_id: str,
    max_tokens: int = 2000,      # 替代 limit=10
    max_rounds: int = 20,        # 安全上限，防止无限回溯
) -> tuple[List[Dict], int, List[Dict]]:
    """
    Returns:
        recent_messages: 窗口内的消息（Token 预算内）
        total_count: 该对话的总消息数（不含当前轮）
        overflow_messages: 超出窗口的消息（需要摘要压缩）
    """
```

### 3.2 填充算法

```
1. 从 DB 加载最近 max_rounds 条消息（desc 排序）
2. 反转正序
3. 从最新消息开始向前累加 Token 数
4. 当累加 Token 数 > max_tokens 时停止
5. 已累加的消息 → recent_messages
6. 未累加的消息 → overflow_messages
```

```python
from common.token_utils import num_tokens_from_string

def _fill_token_window(
    messages: list[dict],
    max_tokens: int,
) -> tuple[list[dict], list[dict]]:
    """从消息列表（正序）中按 Token 预算填充窗口。

    从最新消息（列表末尾）向前累加，直到超出预算。
    返回 (窗口内消息, 溢出消息)，均保持正序。
    """
    recent = []
    token_count = 0

    for msg in reversed(messages):
        msg_tokens = num_tokens_from_string(msg.get("content", "") or "")
        if token_count + msg_tokens <= max_tokens:
            recent.append(msg)
            token_count += msg_tokens
        else:
            break

    recent.reverse()  # 恢复正序
    overflow = messages[: len(messages) - len(recent)]
    return recent, overflow
```

### 3.3 边界处理

| 场景 | 处理 |
|------|------|
| 单条消息本身超过 `max_tokens` | 保留该条（至少保留最近一条），不因超长而丢失上下文 |
| 对话总数 < `max_rounds` 且总 Token < `max_tokens` | 全部保留，无溢出 |
| 对话为空 | 返回空列表，`total_count=0` |

```python
# 边界：单条消息超过 max_tokens 时，至少保留最近一条
if not recent and messages:
    recent = [messages[-1]]
    overflow = messages[:-1]
```

---

## 四、Step 2：LLM 摘要压缩

### 4.1 设计原则

- **增量摘要**：每次新消息超出窗口时，只对新增溢出部分做摘要，与已有摘要合并
- **持久化存储**：摘要存入新表 `ConversationSummary`，避免每次请求重复生成
- **惰性生成**：摘要仅在首次需要时生成，后续请求复用
- **摘要版本标记**：通过 `last_message_id` 判断摘要是否过期

### 4.2 数据模型

#### 新表：`conversation_summaries`

```sql
CREATE TABLE conversation_summaries (
    id              VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL,
    summary_text    TEXT NOT NULL,           -- 摘要内容
    last_message_id VARCHAR(36) NOT NULL,    -- 摘要覆盖到的最后一条消息 ID
    message_count   INT NOT NULL DEFAULT 0,  -- 被摘要的消息数量
    token_count     INT NOT NULL DEFAULT 0,  -- 摘要的 Token 数
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),

    INDEX idx_conversation_id (conversation_id),
    UNIQUE KEY uk_conversation (conversation_id)
);
```

### 4.3 摘要生成流程

```
_load_conversation_history()
  │
  ├── 1. 加载最近 max_rounds 条消息
  ├── 2. _fill_token_window() → recent_messages + overflow_messages
  │
  ├── 3. 如果 overflow_messages 为空 → 无需摘要，返回 recent_messages
  │
  └── 4. 如果 overflow_messages 非空：
        │
        ├── 4a. 查询 conversation_summaries WHERE conversation_id=?
        │
        ├── 4b. 判断摘要是否过期：
        │      last_message_id == overflow_messages[-1].id ?
        │      ├── 是 → 摘要有效，直接复用
        │      └── 否 → 需要更新摘要
        │
        └── 4c. 更新摘要（如需要）：
               ├── 调用 LLM 生成/更新摘要
               ├── 写入 conversation_summaries（UPSERT）
               └── 返回 summary_text
```

### 4.4 摘要 Prompt 设计

```python
SUMMARY_PROMPT = """你是一个对话摘要助手。请将以下对话历史压缩为简洁的摘要，保留关键信息。

要求：
1. 保留关键实体（人名、项目名、技术术语、数字等）
2. 保留重要结论和决策
3. 保留用户明确表达的偏好和需求
4. 忽略寒暄和无关细节
5. 摘要长度控制在 300 字以内
6. 使用中文输出

对话历史：
{conversation_text}

摘要："""
```

### 4.5 增量摘要合并

当已有摘要且新溢出消息需要追加时：

```python
async def _update_summary(
    existing_summary: str,
    new_overflow: list[dict],
) -> str:
    """增量更新摘要：已有摘要 + 新溢出消息 → LLM 合并为一段摘要"""
    new_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}"
        for m in new_overflow
    )

    merge_prompt = f"""已有摘要：{existing_summary}

新增对话：{new_text}

请将以上内容合并为一段简洁的摘要，保留所有关键信息，控制在 300 字以内。"""

    return await llm.chat(merge_prompt)
```

### 4.6 摘要注入 Prompt 的格式

在 `_format_conversation_history` 之前，先注入摘要：

```python
def _format_context_with_summary(
    conversation_summary: str,
    recent_messages: list[dict],
) -> str:
    parts = []
    if conversation_summary:
        parts.append(f"【历史摘要】\n{conversation_summary}")
    history_text = _format_conversation_history(recent_messages)
    if history_text:
        parts.append(history_text)
    return "\n\n".join(parts)
```

最终 Prompt 中的效果：

```
【历史摘要】
用户此前咨询了RAG系统的部署方案，讨论了GPU选型（A100/A6000），
关注成本控制，偏好性价比方案。已确认使用Docker Compose部署。

【对话历史】
用户: 那么数据库连接池配置有什么建议？
助手: 建议连接池大小设置为 CPU 核心数的 2 倍...
```

---

## 五、代码改动清单

### 5.1 改动文件一览

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `api/v1/services/conversation_service.py` | 修改 | `_load_conversation_history` 重构 + 新增摘要逻辑 |
| `agent/langgraph/nodes/prompt_assembly.py` | 修改 | `_format_conversation_history` 支持摘要注入 |
| `agent/langgraph/state.py` | 修改 | 新增 `conversation_summary` 字段 |
| `agent/langgraph/runner.py` | 修改 | `_build_initial_state` 传递摘要 |
| `api/db/models/message.py` | 新增 | `ConversationSummary` ORM 模型 |
| `api/db/migrations/` | 新增 | 数据库迁移脚本 |
| `common/config.py` | 修改 | 新增配置项 |

### 5.2 改动详情

#### 5.2.1 `conversation_service.py`

当前 `_load_conversation_history` 签名：

```python
async def _load_conversation_history(
    self, conversation_id: str, limit: int = 10
) -> List[Dict[str, str]]:
```

改为：

```python
async def _load_conversation_history(
    self,
    conversation_id: str,
    max_tokens: int = 2000,
    max_rounds: int = 20,
) -> tuple[List[Dict], str]:
    """
    Returns:
        recent_messages: Token 预算内的消息列表
        conversation_summary: 超出窗口的历史摘要（可能为空字符串）
    """
```

调用方 `stream_response` 中也需要同步更新：

```python
# 原来
conversation_history = await self._load_conversation_history(conversation_id, limit=10)

# 改为
conversation_history, conversation_summary = await self._load_conversation_history(
    conversation_id,
    max_tokens=agent_config.get("memory_max_tokens", 2000),
)
```

#### 5.2.2 `state.py`

新增字段：

```python
# 对话历史摘要（超出 Token 窗口的历史消息的 LLM 压缩摘要）
conversation_summary: str
```

#### 5.2.3 `runner.py`

`_build_initial_state` 新增参数：

```python
def _build_initial_state(
    self,
    ...
    conversation_summary: str = "",
    ...
) -> AgentState:
    return {
        ...
        "conversation_summary": conversation_summary,
        ...
    }
```

`arun()` / `arun_stream()` 等所有入口方法也需同步新增 `conversation_summary` 参数。

#### 5.2.4 `prompt_assembly.py`

`_format_conversation_history` 函数改为接收摘要参数：

```python
def _format_conversation_history(
    history: list[dict],
    summary: str = "",
) -> str:
    """将对话历史和摘要格式化为 Prompt 段落。"""
    parts = []
    if summary:
        parts.append(f"【历史摘要】\n{summary}")

    if not history:
        return "\n\n".join(parts) if parts else ""

    role_label = {"user": "用户", "assistant": "助手", "system": "系统"}
    recent = history[-10:]  # 保留硬截断作为安全上限
    lines = ["【对话历史】"]
    for msg in recent:
        role = msg.get("role", "user")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        label = role_label.get(role, role)
        lines.append(f"{label}: {content}")

    if len(lines) > 1:
        parts.append("\n".join(lines))

    return "\n\n".join(parts)
```

`_build_prompt` 和 `_build_citation_aware_prompt` 中传入 `conversation_summary`：

```python
# prompt_assembly_node 中
conversation_summary = state.get("conversation_summary", "") or ""
history_text = _format_conversation_history(
    conversation_history, conversation_summary
)
```

#### 5.2.5 `ConversationSummary` ORM 模型（新增）

```python
# api/db/models/conversation_summary.py
from sqlalchemy import Column, String, Text, Integer, DateTime, func
from api.db.base import Base

class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id = Column(String(36), primary_key=True)
    conversation_id = Column(String(36), nullable=False, index=True, unique=True)
    summary_text = Column(Text, nullable=False)
    last_message_id = Column(String(36), nullable=False)
    message_count = Column(Integer, default=0)
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
```

---

## 六、配置项

```yaml
# 对话记忆配置
memory:
  # Token 预算：对话历史（不含摘要）最多占用的 Token 数
  max_tokens: 2000

  # 最大回溯轮数：防止超长对话无限回溯
  max_rounds: 20

  # 摘要最大 Token 数
  summary_max_tokens: 300

  # 摘要生成的最小溢出轮数：溢出消息少于 N 轮时跳过摘要生成，直接丢弃
  summary_min_overflow_rounds: 3

  # 摘要 LLM 配置（可复用现有 LLM，也可指定轻量模型降低成本）
  summary_llm_id: ""
```

---

## 七、Summary 生成时机策略

提供两种模式，通过配置切换：

### 模式 A：同步生成（默认）

- 摘要生成在 `_load_conversation_history` 中同步执行
- **优点**：摘要始终最新，无一致性问题
- **缺点**：首次生成摘要时增加 1-2s 延迟
- **适用**：溢出消息较少的场景，大部分请求摘要已缓存

### 模式 B：异步生成

- `_load_conversation_history` 返回现有摘要（可能略微过期）
- 后台任务检测到摘要过期时，异步生成新摘要
- **优点**：零延迟影响
- **缺点**：摘要可能滞后一个请求周期
- **适用**：溢出消息频繁产生的长对话场景

```python
# 模式选择
if config.memory_summary_mode == "async":
    # 返回现有摘要，触发后台更新
    asyncio.create_task(self._update_summary_async(conversation_id, overflow))
    return recent_messages, existing_summary or ""
else:
    # 同步生成/更新摘要
    summary = await self._ensure_summary(conversation_id, overflow)
    return recent_messages, summary
```

---

## 八、测试验证

### 8.1 单元测试

| 测试用例 | 验证点 |
|---------|--------|
| 对话消息总 Token < `max_tokens` | 全部保留，摘要为空 |
| 对话消息总 Token > `max_tokens` | 窗口内消息 Token 数 ≤ budget |
| 单条消息 > `max_tokens` | 至少保留最近一条 |
| 溢出消息 < `summary_min_overflow_rounds` | 不生成摘要 |
| 摘要已缓存且未过期 | 复用缓存，不调用 LLM |
| 摘要已缓存但过期 | 更新摘要 |
| 空对话 | 返回空列表，摘要为空 |

### 8.2 集成测试

| 场景 | 预期 |
|------|------|
| 短对话（5 轮） | 历史全部保留，无摘要 |
| 长对话（50 轮，总 Token 超预算） | 窗口内有最近消息，摘要覆盖早期消息 |
| 跨天续聊 | 摘要从 DB 加载，无需重新生成 |
| 摘要 + 近期对话注入 Prompt | LLM 能正确理解上下文，回答连贯 |

---

## 九、风险与注意事项

| 风险 | 缓解措施 |
|------|---------|
| 摘要生成增加延迟 | 优先使用缓存；支持异步模式；可配置轻量摘要 LLM |
| 摘要质量差导致 LLM 误解 | 摘要 Prompt 精心设计，保留关键实体；可配置摘要最小溢出轮数 |
| Token 计数不准确（tiktoken vs 实际模型） | 使用 0.8 系数保守估算；`max_tokens` 可配置 |
| DB 迁移兼容性 | 新表为可选，`conversation_summary` 字段默认为空字符串，不影响现有流程 |
| 向后兼容 | `conversation_summary` 为空时行为与当前完全一致 |

---

## 十、与当前架构的兼容性

```
改造前:
  ConversationService → runner(conversation_history) → prompt_assembly → 【对话历史】

改造后:
  ConversationService → runner(conversation_history, conversation_summary)
    → prompt_assembly → 【历史摘要】 + 【对话历史】

向后兼容:
  conversation_summary="" 时，行为与改造前完全一致
  /langgraph_completion 端点无需修改（conversation_summary 默认为空）
```