# GraphTool 上游对接文档

> 面向 `data-knowledge-api` 的上游服务开发者，描述如何通过 Agent 对话接口使用图检索（Graph QA）能力。

---

## 1. 概述

### 1.1 什么是 GraphTool

GraphTool 是 `data-knowledge-api` Agentic RAG 系统中的一个**知识检索工具**，提供基于 Neo4j 图数据库的结构化知识问答能力。它通过以下流水线回答用户问题：

```
用户问题 → Planner 规划 → 本体 Schema 自省 → Cypher 生成 → Neo4j 执行 → 修复重试 → 答案合成
```

与向量检索（RAGTool）互补——RAGTool 擅长非结构化文本匹配，GraphTool 擅长**关系推理、多跳路径、实体关联**类问题。

### 1.2 适用场景

| 场景 | 示例问题 |
|------|---------|
| 实体关系查询 | "张三的直接上级是谁？" |
| 多跳路径推理 | "张三和李四之间有哪些共同参与的会议？" |
| 关联链路追溯 | "这个项目涉及哪些供应商？他们的合同金额是多少？" |
| 图谱探索 | "技术部最近三个月组织了哪些跨部门活动？" |

### 1.3 前置条件

- `data-knowledge-api` 中 Agent 已启用 GraphTool（见 §2 Agent 配置）
- `ontology_v2_formal` 图 QA 服务已部署并可达（`data-knowledge-api` 内部通过 HTTP 调用）
- 图数据库（Neo4j）中已灌入相关本体数据

---

## 2. Agent 配置

### 2.1 启用 GraphTool

在创建或更新 Agent 时，通过 `tools_config` 启用 `graph` 工具：

**创建 Agent：**

```
POST /api/v1/agents
Authorization: <user_access_token>
Content-Type: application/json
```

```json
{
  "name": "知识助手",
  "description": "具备向量检索和图检索能力的智能助手",
  "tools_config": {
    "tools": ["rag", "graph"],
    "rag_config": {
      "kb_ids": ["kb_001"],
      "similarity_threshold": 0.5,
      "top_k": 10
    },
    "graph_config": {
      "source_type": "Meeting",
      "max_rows": 20,
      "enable_pg": false
    }
  },
  "routing_config": {},
  "model_config": {
    "llm_id": "deepseek-v3"
  }
}
```

**更新 Agent：**

```
PUT /api/v1/agents/<agent_id>
Authorization: <user_access_token>
Content-Type: application/json
```

```json
{
  "tools_config": {
    "tools": ["rag", "graph"]
  }
}
```

### 2.2 `graph_config` 参数说明

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `source_type` | string | 否 | `"Meeting"` | 本体 Schema 子类型，决定查询时使用的实体/关系映射 |
| `max_rows` | int | 否 | `20` | 单次图查询返回的最大行数 |
| `max_result_chars` | int | 否 | `12000` | 单次图查询返回结果的最大字符数 |
| `enable_hybrid_retrieval` | bool | 否 | `false` | 是否启用 hybrid 向量上下文检索（联合向量检索增强 Cypher 生成） |
| `enable_pg` | bool | 否 | `false` | 是否启用 PostgreSQL 双源分支（同时查询 Neo4j + PostgreSQL） |
| `timeout_ms` | int | 否 | `30000` | 图查询超时时间（毫秒） |

---

## 3. 对话接口

### 3.1 接口地址

```
POST /api/v1/agents/<agent_id>/completions
```

### 3.2 认证方式

在请求 Header 中携带用户 access token：

```
Authorization: <user_access_token>
```

### 3.3 请求参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `question` | string | 是 | — | 用户问题 |
| `stream` | bool | 否 | `true` | 是否流式返回 |
| `session_id` | string | 否 | — | 会话 ID，用于多轮对话上下文保持 |
| `user_id` | string | 否 | — | 用户标识（用于日志和审计） |
| `return_trace` | bool | 否 | `false` | 是否在流式响应中返回节点执行 trace（调试用） |

### 3.4 请求示例

```bash
curl -X POST "https://<host>/api/v1/agents/<agent_id>/completions" \
  -H "Authorization: <user_access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "张三参加了哪些会议？",
    "stream": true
  }'
```

### 3.5 路由机制

用户问题进入 Agent 后，经过四层路由决策自动匹配到最合适的工具：

| 路由层 | 说明 | GraphTool 触发条件 |
|--------|------|--------------------|
| 第0层：前置过滤 | 精确指令匹配 | 用户以 `@graph` 开头，如 `@graph 张三参加了哪些会议` |
| 第1层：规则路由 | 关键词匹配 | 问题含"关系图谱""知识图谱""实体关联""图查询""多跳"等关键词 |
| 第2层：LLM 语义路由 | LLM 判断意图 | LLM 判断问题适合图查询（关系推理、多跳路径类） |
| 第3层：Planner | 复杂任务拆解 | 复杂任务被拆分为多个子步骤，部分子步骤路由到 graph |

**上游调用方只需正常提问，无需手动指定 tool。** 如需强制使用图检索，在问题前加 `@graph` 前缀即可。

---

## 4. 响应格式

### 4.1 流式响应（`stream=true`，默认）

响应格式为 SSE (Server-Sent Events)，Content-Type 为 `text/event-stream`。

**SSE 事件类型：**

| 事件类型 | 说明 |
|---------|------|
| `message` | 文本片段（思考过程 / 回答内容） |
| `node_finished` | 节点执行完成（含 trace，当 `return_trace=true` 时） |
| `message_end` | 流结束，包含最终引用（reference）和状态 |

**完整流式响应示例：**

```
data:{"event":"message","data":{"content":"正在分析","start_to_think":true,"end_to_think":false},"session_id":"sess_abc123"}

data:{"event":"message","data":{"content":"您的问题...","start_to_think":false,"end_to_think":true},"session_id":"sess_abc123"}

data:{"event":"message","data":{"content":"根据图数据库","start_to_think":false,"end_to_think":false},"session_id":"sess_abc123"}

data:{"event":"message","data":{"content":"查询结果，张三参加了以下会议：\n\n1. **2024年Q1战略规划会**（2024-01-15）\n2. **技术评审会**（2024-02-20）\n3. **年度总结会**（2024-12-10）","start_to_think":false,"end_to_think":false},"session_id":"sess_abc123"}

data:{"event":"message_end","data":{"content":"","reference":{"chunks":[{"content":"张三参加了Q1战略规划会","source_uri":"graph://neo4j/Meeting/2024Q1","source_type":"graph","score":0.85},{"content":"张三参加了技术评审会","source_uri":"graph://neo4j/Meeting/tech-review","source_type":"graph","score":0.85},{"content":"张三参加了年度总结会","source_uri":"graph://neo4j/Meeting/annual-2024","source_type":"graph","score":0.85}],"doc_aggs":[]},"status":200},"session_id":"sess_abc123"}

data:[DONE]
```

### 4.2 `message` 事件字段

```json
{
  "event": "message",
  "data": {
    "content": "文本片段内容",
    "start_to_think": false,
    "end_to_think": false
  },
  "session_id": "sess_abc123"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `data.content` | string | 当前文本片段（增量） |
| `data.start_to_think` | bool | 是否开始思考阶段（Planner 规划、Cypher 生成思考过程） |
| `data.end_to_think` | bool | 是否结束思考阶段，进入正式回答 |
| `session_id` | string | 当前会话 ID |

### 4.3 `message_end` 事件字段

```json
{
  "event": "message_end",
  "data": {
    "content": "",
    "reference": {
      "chunks": [
        {
          "content": "张三参加了Q1战略规划会",
          "source_uri": "graph://neo4j/Meeting/2024Q1",
          "source_type": "graph",
          "score": 0.85
        }
      ],
      "doc_aggs": []
    },
    "status": 200
  },
  "session_id": "sess_abc123"
}
```

**`reference.chunks` 中图检索结果的字段说明：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | 该条图查询结果的文本描述 |
| `source_uri` | string | 图数据来源 URI，格式为 `graph://neo4j/<Label>/<Key>` |
| `source_type` | string | 固定为 `"graph"`，用于区分来源（RAG / DB / Graph） |
| `score` | float | 质量分数（0.0～1.0），图查询成功即为 0.85 |

### 4.4 非流式响应（`stream=false`）

```json
{
  "code": 0,
  "message": "",
  "data": {
    "event": "message_end",
    "data": {
      "content": "根据图数据库查询结果，张三参加了以下会议：\n\n1. **2024年Q1战略规划会**（2024-01-15）\n2. **技术评审会**（2024-02-20）\n3. **年度总结会**（2024-12-10）",
      "reference": {
        "chunks": [
          {"content": "张三参加了Q1战略规划会", "source_uri": "graph://neo4j/Meeting/2024Q1", "source_type": "graph", "score": 0.85},
          {"content": "张三参加了技术评审会", "source_uri": "graph://neo4j/Meeting/tech-review", "source_type": "graph", "score": 0.85},
          {"content": "张三参加了年度总结会", "source_uri": "graph://neo4j/Meeting/annual-2024", "source_type": "graph", "score": 0.85}
        ],
        "doc_aggs": []
      },
      "trace": [...],
      "status": 200
    },
    "session_id": "sess_abc123"
  }
}
```

### 4.5 通用响应码

| `code` | 说明 |
|--------|------|
| `0` | 成功 |
| `100` | 参数错误 |
| `101` | 认证失败 |
| `102` | Agent 不存在或未启用 |
| `500` | 服务内部错误 |

---

## 5. 特殊指令 `@graph`

### 5.1 用法

在问题前添加 `@graph` 前缀，可强制路由到图检索工具，**跳过意图路由的自动判断**：

```
@graph 张三和李四之间有哪些共同参与的会议？
```

### 5.2 适用场景

- 你明确知道问题需要图查询，但 LLM 路由可能误判为 RAG
- 调试阶段，需要确保走 graph 路径
- 问题超出规则路由关键词覆盖范围

### 5.3 行为

- `@graph` 后的问题文本仍会正常传递给图检索流水线
- 路由置信度为 1.0（最高）
- 不会与其他 `@` 指令（`@rag`、`@database`、`@web`）冲突——一次只能使用一个

---

## 6. 多工具协同

### 6.1 混合场景

Agent 支持在一次回答中组合多个工具的结果。当问题复杂度为 `complex` 时，Planner 会将问题拆解为多个子步骤，并并行/串行调用不同工具：

```
用户问题: "张三最近的会议中讨论了哪些供应商？这些供应商的合同金额是多少？"

Planner 拆解:
  步骤1 (graph): 查询"张三最近参加的会议以及会议中涉及的供应商"
  步骤2 (database): 查询"供应商的合同金额"

最终答案: 融合两个工具的结果，生成完整回答
```

### 6.2 证据融合

多工具的结果在 `evidence_fusion` 节点中融合去重，最终统一呈现给用户。在 `reference.chunks` 中，不同来源的 `source_type` 字段可以区分：

| `source_type` | 来源 |
|---------------|------|
| `"rag"` | 向量检索（RAG） |
| `"db"` | 数据库查询 |
| `"graph"` | 图检索（GraphTool） |
| `"web"` | 网络搜索 |

---

## 7. 错误处理

### 7.1 图检索异常

当图检索失败时，Agent 不会直接报错，而是通过以下机制处理：

| 情况 | 行为 |
|------|------|
| 图服务不可达（连接失败 / 超时） | 返回空结果，`quality_score = 0.0`，触发重试或降级 |
| Cypher 生成失败（Schema 不匹配） | Pipeline 内 repair 重试循环，最多 3 次 |
| 查询结果为空（无匹配实体） | `row_count = 0`，`quality_score = 0.0`，触发降级到 RAG |
| 重试次数耗尽 | 降级到 RAGTool 或 WebTool，不阻塞用户获取答案 |

### 7.2 降级策略

当图检索质量不达标（`quality_score < 0.7`）时，quality_check 节点自动触发降级：

```
graph_tool (失败) → evidence_fusion → quality_check → retry_graph → graph_tool (重试)
                                                       ↓ (重试耗尽)
                                                  fallback → RAG / Web
```

### 7.3 上游感知

上游调用方无需额外处理图检索异常——降级和重试对上游透明。只需关注最终 `message_end` 事件中的 `reference.chunks` 的 `source_type` 字段，即可判断实际使用了哪些数据源。

---

## 8. 完整示例

### 8.1 使用 curl

```bash
# 创建带 graph 工具的 Agent
curl -X POST "https://<host>/api/v1/agents" \
  -H "Authorization: <user_access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "图查询助手",
    "description": "支持图检索的智能助手",
    "tools_config": {
      "tools": ["graph"],
      "graph_config": {
        "source_type": "Meeting",
        "max_rows": 20
      }
    },
    "model_config": {
      "llm_id": "deepseek-v3"
    }
  }'

# 发起图查询对话
curl -X POST "https://<host>/api/v1/agents/<agent_id>/completions" \
  -H "Authorization: <user_access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "@graph 张三参加了哪些会议？",
    "stream": true
  }'
```

### 8.2 使用 Python

```python
import httpx
import json

BASE_URL = "https://<host>"
TOKEN = "<user_access_token>"
AGENT_ID = "<agent_id>"

async def ask_graph(question: str):
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/api/v1/agents/{AGENT_ID}/completions",
            headers={
                "Authorization": TOKEN,
                "Content-Type": "application/json",
            },
            json={
                "question": question,
                "stream": True,
            },
        ) as response:
            full_answer = ""
            references = []

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break

                event = json.loads(data_str)
                evt_type = event.get("event")

                if evt_type == "message":
                    content = event["data"].get("content", "")
                    if not event["data"].get("start_to_think"):
                        print(content, end="", flush=True)
                    full_answer += content

                elif evt_type == "message_end":
                    references = (
                        event["data"]
                        .get("reference", {})
                        .get("chunks", [])
                    )
                    # 筛选图检索结果
                    graph_refs = [
                        r for r in references
                        if r.get("source_type") == "graph"
                    ]
                    print(f"\n\n[图检索证据: {len(graph_refs)} 条]")

            return full_answer, references

# 使用
import asyncio
answer, refs = asyncio.run(ask_graph("张三参加了哪些会议？"))
```

### 8.3 使用 JavaScript

```javascript
const BASE_URL = "https://<host>";
const TOKEN = "<user_access_token>";
const AGENT_ID = "<agent_id>";

async function askGraph(question) {
  const response = await fetch(
    `${BASE_URL}/api/v1/agents/${AGENT_ID}/completions`,
    {
      method: "POST",
      headers: {
        Authorization: TOKEN,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question, stream: true }),
    }
  );

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let fullAnswer = "";
  let references = [];

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const chunk = decoder.decode(value, { stream: true });
    const lines = chunk.split("\n");

    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const dataStr = line.slice(5).trim();
      if (dataStr === "[DONE]") break;

      const event = JSON.parse(dataStr);

      if (event.event === "message") {
        if (!event.data.start_to_think) {
          process.stdout.write(event.data.content);
        }
        fullAnswer += event.data.content;
      }

      if (event.event === "message_end") {
        references = event.data.reference?.chunks || [];
        const graphRefs = references.filter(
          (r) => r.source_type === "graph"
        );
        console.log(`\n\n[图检索证据: ${graphRefs.length} 条]`);
      }
    }
  }

  return { answer: fullAnswer, references };
}

askGraph("张三参加了哪些会议？");
```

---

## 9. 常见问题

### Q1: 如何判断回答是否使用了图检索？

检查 `message_end` 事件中 `reference.chunks` 的 `source_type` 字段。如存在 `"graph"` 类型的条目，说明图检索被使用。

### Q2: 图检索和向量检索可以同时使用吗？

可以。当 Planner 判断问题为复杂任务时，会自动拆解为多个子步骤，分别调用不同工具。最终结果在 evidence_fusion 中融合。

### Q3: 图检索超时了怎么办？

Agent 内置重试机制（最多 3 次）。重试耗尽后自动降级到 RAGTool 或 WebTool，不会阻塞用户获取答案。如需调整超时时间，在 `graph_config.timeout_ms` 中配置。

### Q4: 如何确保问题一定走图检索？

在问题前加 `@graph` 前缀，或在 Agent 配置中 `tools_config.tools` 只保留 `["graph"]`。

### Q5: `graph_config` 中的 `source_type` 是什么意思？

`source_type` 是 ontology_v2_formal 中的本体 Schema 子类型，决定图查询时使用的实体标签和关系类型映射。不同业务场景应使用不同的 `source_type`。可选值请咨询 ontology 团队。

---

## 10. 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-09-03 | 初版，定义 GraphTool 上游对接接口 |

---

## 附录 A：GraphTool 内部流水线（供参考）

```
用户问题
  │
  ▼
Planner ─── 理解意图，拆分子问题
  │
  ▼
Schema 自省 ─── 获取本体标签/关系映射
  │
  ▼
Cypher 生成 ─── LLM 生成 Cypher 查询语句
  │
  ▼
Neo4j 执行 ─── 在图数据库中执行查询
  │
  ├── 成功 → 答案合成 ──→ 最终回答
  │
  └── 失败 → repair 修复重试（最多 3 次）
                 │
                 ├── 修复成功 → 答案合成
                 └── 修复失败 → 返回空结果
```

## 附录 B：LangGraph 状态图全貌（GraphTool 接入后）

```
question_input
    │
    ▼
intent_router ────┬── clarification ──→ intent_router (re-route)
    │              │
    │              ├── rag_tool ──┬── db_tool (hybrid) ──┐
    │              │              └── evidence_fusion ◄──┘
    │              │
    │              ├── db_tool ──→ evidence_fusion
    │              │
    │              ├── graph_tool ──→ evidence_fusion    ★
    │              │
    │              ├── rest_tool ──→ evidence_fusion
    │              │
    │              ├── plan_executor ──→ evidence_fusion
    │              │
    │              └── prompt_assembly (chitchat)
    │
    ▼
evidence_fusion ──→ reflection ──→ quality_check
                                        │
                      ┌─────────────────┼──────────────────┐
                      ▼                 ▼                  ▼
               prompt_assembly    rag/db/graph/      fallback
                  (pass)         web (retry)
                      │
                      ▼
               llm_generate → hallucination → answer_renderer → 输出
```