# LangGraph 与 API 集成设计文档

## 实现状态：✅ 已完成

集成代码已落地实现，语法验证通过。

## Why

当前 `api/v1/core/langgraph_integration.py` 和 `api/v1/services/conversation_service.py` 是两个独立模块：
- LangGraph 状态图是硬编码的，不读取 Agent 配置
- 对话服务的 `stream_response` 方法返回模拟数据，未调用 LangGraph
- LangGraph 节点内部也是返回模拟数据，未调用 RAGFlow/数据源服务

三者之间没有形成调用链路，系统无法真正执行智能问答流程。

## What Changes

### 变更 1：LangGraph 图构建由 Agent 配置驱动
- `AgenticRAGGraph` 接收 Agent 配置（`tools_config`、`routing_config`、`degradation_config`）
- 根据 `tools_config.tools` 动态决定启用哪些工具节点
- 根据 `routing_config.strategy` 选择路由实现（关键词 / LLM）
- 根据 `degradation_config` 添加重试和降级边

### 变更 2：LangGraph 节点调用实际服务
- `_rag_retrieval` 节点调用 `RAGFlowClient.retrieval()`
- `_database_query` 节点调用 `DataSourceService.execute_sql()`
- `_web_search` 节点暂保留 mock（后续接入搜索引擎）
- `_response_generator` 节点调用 LLM 生成回答

### 变更 3：对话服务集成 LangGraph 执行
- `stream_response` 方法加载 Agent 配置
- 构建 LangGraph 图并执行
- 将 LangGraph 状态变化转换为 SSE 事件流

### 变更 4：SSE 事件流适配
- 定义 LangGraph 状态 → SSE 事件的映射规则
- 支持 thinking / tool_call / tool_result / text / reference / done / error 事件

## Impact

- Affected code:
  - `api/v1/core/langgraph_integration.py`（重构）
  - `api/v1/services/conversation_service.py`（重构 stream_response）
  - `api/v1/routes/conversations.py`（传入 Agent 上下文）

## 详细设计

### 1. 整体架构

```
用户消息
    ↓
ConversationService.stream_response()
    ↓
加载 Agent 配置（tools_config, routing_config, degradation_config）
    ↓
AgenticRAGGraph(agent_config).build_graph()  ← 动态构建
    ↓
graph.ainvoke(initial_state)
    ↓
┌─────────────────────────────────────────────────────┐
│  LangGraph 状态图执行                                 │
│                                                       │
│  [intent_router] ──→ 路由决策                          │
│       ↓                                               │
│  ┌───┴───┬───────┐                                   │
│  ↓       ↓       ↓                                   │
│ [RAG]  [DB]   [Web]  ← 只启用配置中存在的工具节点      │
│  ↓       ↓       ↓                                   │
│  └───┬───┴───────┘                                   │
│       ↓                                               │
│  [quality_check] ──→ 质量不达标？降级/重试             │
│       ↓                                               │
│  [response_generator] ← 调用 LLM 生成回答             │
│       ↓                                               │
│  [END]                                                │
└─────────────────────────────────────────────────────┘
    ↓
状态变化 → SSE 事件流 → 前端
```

### 2. AgenticRAGGraph 重构设计

#### 2.1 构造函数

```python
class AgenticRAGGraph:
    def __init__(
        self,
        agent_config: dict,       # Agent 的 tools_config + routing_config + degradation_config
        ragflow_client,           # RAGFlow 客户端
        db_service,               # 数据源服务
        knowledge_service,        # 知识库服务
        event_callback            # SSE 事件回调函数
    ):
```

#### 2.2 动态图构建逻辑

```python
def _build_graph(self) -> StateGraph:
    workflow = StateGraph(AgentState)

    # 1. 始终添加的节点
    workflow.add_node("intent_router", self._intent_router)
    workflow.add_node("response_generator", self._response_generator)

    # 2. 根据 tools_config 动态添加工具节点
    enabled_tools = self.agent_config.get("tools_config", {}).get("tools", [])

    if "rag" in enabled_tools:
        workflow.add_node("rag_retrieval", self._rag_retrieval)
    if "database" in enabled_tools:
        workflow.add_node("database_query", self._database_query)
    if "web" in enabled_tools:
        workflow.add_node("web_search", self._web_search)

    # 3. 添加质量检查节点（如果有降级配置）
    degradation = self.agent_config.get("degradation_config", {})
    max_retries = degradation.get("max_retries", 3)
    if max_retries > 0:
        workflow.add_node("quality_check", self._quality_check)

    # 4. 设置入口
    workflow.set_entry_point("intent_router")

    # 5. 根据路由策略添加条件边
    routing_strategy = self.agent_config.get("routing_config", {}).get("strategy", "keyword")
    workflow.add_conditional_edges(
        "intent_router",
        self._route_decision,
        self._build_route_map(enabled_tools)
    )

    # 6. 工具节点 → 质量检查 → 响应生成器
    for tool in enabled_tools:
        node_name = self._tool_node_name(tool)
        if max_retries > 0:
            workflow.add_edge(node_name, "quality_check")
        else:
            workflow.add_edge(node_name, "response_generator")

    # 7. 质量检查的条件边（通过 → 响应生成器，不通过 → 降级/重试）
    if max_retries > 0:
        workflow.add_conditional_edges(
            "quality_check",
            self._quality_decision,
            {
                "pass": "response_generator",
                "retry": self._fallback_tool(enabled_tools),  # 降级到下一个工具
                "fail": "response_generator"  # 已达最大重试，用已有结果生成
            }
        )

    # 8. 响应生成器 → 结束
    workflow.add_edge("response_generator", END)

    # 9. 无工具时直接闲聊
    if not enabled_tools:
        workflow.add_edge("intent_router", "response_generator")

    return workflow.compile()
```

#### 2.3 路由映射构建

```python
def _build_route_map(self, enabled_tools: list) -> dict:
    """构建路由映射表"""
    route_map = {}
    if "rag" in enabled_tools:
        route_map["rag"] = "rag_retrieval"
    if "database" in enabled_tools:
        route_map["database"] = "database_query"
    if "web" in enabled_tools:
        route_map["web"] = "web_search"
    route_map["chitchat"] = "response_generator"
    return route_map
```

#### 2.4 路由决策实现

```python
async def _intent_router(self, state: AgentState) -> AgentState:
    strategy = self.routing_config.get("strategy", "keyword")

    if strategy == "keyword":
        return await self._route_by_keyword(state)
    elif strategy == "llm":
        return await self._route_by_llm(state)
    else:
        return await self._route_by_priority(state)

async def _route_by_keyword(self, state: AgentState) -> AgentState:
    """关键词路由：根据 routing_config.rules 匹配"""
    rules = self.routing_config.get("rules", [])
    query = state["messages"][-1].content.lower()

    for rule in rules:
        keywords = rule.get("keywords", [])
        target = rule.get("target", "")
        if any(kw in query for kw in keywords):
            state["route_target"] = target
            return state

    # 默认走优先级最高的工具
    enabled_tools = self.tools_config.get("tools", [])
    state["route_target"] = enabled_tools[0] if enabled_tools else "chitchat"
    return state
```

### 3. 节点调用实际服务

#### 3.1 RAG 检索节点

```python
async def _rag_retrieval(self, state: AgentState) -> AgentState:
    query = state.get("rag_query", "")

    # 发送 SSE 事件：工具调用开始
    await self._emit_event("tool_call", {
        "tool": "rag",
        "query": query,
        "status": "running"
    })

    # 从 Agent 配置中获取 RAG 参数
    rag_config = self.tools_config.get("rag_config", {})
    dataset_ids = rag_config.get("dataset_ids", [])
    top_k = rag_config.get("top_k", 5)
    similarity_threshold = rag_config.get("similarity_threshold", 0.5)

    all_documents = []
    for dataset_id in dataset_ids:
        try:
            result = await self.ragflow_client.retrieval(
                dataset_id=dataset_id,
                query=query,
                top_k=top_k
            )
            chunks = result.get("data", {}).get("chunks", [])
            for chunk in chunks:
                score = chunk.get("similarity", 0)
                if score >= similarity_threshold:
                    all_documents.append({
                        "content": chunk.get("content", ""),
                        "source": chunk.get("document_keyword", ""),
                        "score": score
                    })
        except Exception as e:
            logger.error(f"RAG retrieval failed for dataset {dataset_id}: {e}")

    # 按分数排序取 top_k
    all_documents.sort(key=lambda x: x["score"], reverse=True)
    all_documents = all_documents[:top_k]

    state["rag_documents"] = all_documents
    state["rag_quality_score"] = max([d["score"] for d in all_documents], default=0.0)

    # 发送 SSE 事件：工具调用完成 + 引用来源
    await self._emit_event("tool_result", {
        "tool": "rag",
        "documents": all_documents,
        "status": "completed"
    })
    if all_documents:
        await self._emit_event("reference", {
            "references": [
                {"type": "document", "title": d["source"], "content": d["content"][:200], "score": d["score"]}
                for d in all_documents
            ]
        })

    return state
```

#### 3.2 数据库查询节点

```python
async def _database_query(self, state: AgentState) -> AgentState:
    user_query = state.get("db_sql", "") or state["messages"][-1].content

    await self._emit_event("tool_call", {
        "tool": "database",
        "query": user_query,
        "status": "running"
    })

    # 从 Agent 配置中获取数据库参数
    db_config = self.tools_config.get("database_config", {})
    datasource_id = db_config.get("datasource_id", "")
    max_rows = db_config.get("max_rows", 100)

    try:
        # 调用数据源服务执行 SQL
        # 注意：这里需要 LLM 将自然语言转为 SQL，或直接传递用户输入的 SQL
        sql = user_query  # 后续可接入 Text-to-SQL
        result = await self.db_service.execute_sql(
            datasource_id=datasource_id,
            user_id=state["user_id"],
            sql=sql,
            max_rows=max_rows
        )

        state["db_result"] = result.get("rows", [])
        state["db_sql"] = sql
        state["db_quality_score"] = 0.8 if result.get("rows") else 0.0

        await self._emit_event("tool_result", {
            "tool": "database",
            "sql": sql,
            "rows": result.get("rows", []),
            "row_count": result.get("row_count", 0),
            "status": "completed"
        })
        await self._emit_event("reference", {
            "references": [{
                "type": "database",
                "title": f"SQL: {sql[:50]}",
                "content": f"查询返回 {result.get('row_count', 0)} 条结果",
                "source": sql
            }]
        })
    except Exception as e:
        logger.error(f"Database query failed: {e}")
        state["db_result"] = []
        state["db_quality_score"] = 0.0
        await self._emit_event("tool_result", {
            "tool": "database",
            "error": str(e),
            "status": "error"
        })

    return state
```

#### 3.3 响应生成节点

```python
async def _response_generator(self, state: AgentState) -> AgentState:
    route_target = state.get("route_target", "chitchat")

    # 构建上下文
    context_parts = []
    if route_target == "rag" and state.get("rag_documents"):
        context_parts.append("知识库检索结果：")
        for doc in state["rag_documents"]:
            context_parts.append(f"- [{doc['source']}] {doc['content']}")

    elif route_target == "database" and state.get("db_result"):
        context_parts.append(f"数据库查询结果（SQL: {state.get('db_sql', '')}）：")
        context_parts.append(json.dumps(state["db_result"], ensure_ascii=False, indent=2))

    elif route_target == "web" and state.get("web_results"):
        context_parts.append("网上搜索结果：")
        for result in state["web_results"]:
            context_parts.append(f"- [{result['title']}] {result['snippet']}")

    context = "\n".join(context_parts) if context_parts else ""
    user_query = state["messages"][-1].content

    # TODO: 调用 LLM 生成回答（目前使用模板）
    if context:
        response = f"根据您的问题「{user_query}」，以下是相关信息：\n\n{context}\n\n希望以上信息对您有帮助。"
    else:
        response = "您好！我是智能助手，有什么可以帮助您的吗？"

    state["final_response"] = response

    # 流式输出文本
    for char in response:
        await self._emit_event("text", {"content": char})

    return state
```

### 4. SSE 事件流适配

#### 4.1 事件回调机制

```python
class AgenticRAGGraph:
    def __init__(self, ..., event_callback):
        self.event_callback = event_callback  # async def callback(event_type: str, data: dict)

    async def _emit_event(self, event_type: str, data: dict):
        """发送 SSE 事件"""
        if self.event_callback:
            await self.event_callback(event_type, data)
```

#### 4.2 事件类型映射

| LangGraph 状态变化 | SSE 事件类型 | 数据内容 |
|---|---|---|
| 进入 intent_router | `thinking` | `{"content": "正在分析意图..."}` |
| 路由决策完成 | `thinking` | `{"content": "选择工具: xxx"}` |
| 进入工具节点 | `tool_call` | `{"tool": "rag/db/web", "status": "running", ...}` |
| 工具节点完成 | `tool_result` | `{"tool": "xxx", "status": "completed", ...}` |
| 检索到引用 | `reference` | `{"references": [...]}` |
| 生成文本 | `text` | `{"content": "字符"}` |
| 执行完成 | `done` | `{"message_id": "xxx"}` |
| 执行异常 | `error` | `{"error": "错误信息"}` |

#### 4.3 ConversationService 集成

```python
async def stream_response(self, conversation_id, user_message, user_id):
    # 1. 验证对话权限
    conversation = await self.get(conversation_id, user_id)
    if not conversation:
        yield self._format_sse({"type": "error", "error": "对话不存在"})
        return

    # 2. 保存用户消息
    await self.add_message(conversation_id=conversation_id, role="user", content=user_message)

    # 3. 加载 Agent 配置
    agent_config = await self._load_agent_config(conversation.agent_id)

    # 4. 创建事件队列
    event_queue = asyncio.Queue()

    async def event_callback(event_type: str, data: dict):
        await event_queue.put({"type": event_type, **data})

    # 5. 构建并执行 LangGraph
    ragflow_client = get_ragflow_client()
    graph = AgenticRAGGraph(
        agent_config=agent_config,
        ragflow_client=ragflow_client,
        db_service=self.db,  # 需要传入 DataSourceService 实例
        knowledge_service=None,
        event_callback=event_callback
    )

    # 6. 启动 LangGraph 执行（后台任务）
    async def run_graph():
        try:
            initial_state = AgentState(
                messages=[HumanMessage(content=user_message)],
                user_id=user_id,
                conversation_id=conversation_id,
                ...
            )
            final_state = await graph.invoke(initial_state)
            await event_queue.put(None)  # 结束信号
        except Exception as e:
            await event_queue.put({"type": "error", "error": str(e)})

    task = asyncio.create_task(run_graph())

    # 7. 从队列中读取事件并 yield SSE
    while True:
        event = await event_queue.get()
        if event is None:
            break
        yield self._format_sse(event)

    # 8. 保存助手消息
    await task  # 确保图执行完成
    # 从最终状态中获取响应内容保存

    yield self._format_sse({"type": "done"})
```

### 5. Agent 配置加载

```python
async def _load_agent_config(self, agent_id: Optional[str]) -> dict:
    """加载 Agent 配置，如果没有则使用默认配置"""
    if not agent_id:
        return {
            "tools_config": {"tools": ["rag"]},
            "routing_config": {"strategy": "keyword", "rules": []},
            "degradation_config": {"max_retries": 3, "fallback": "default", "timeout_seconds": 30}
        }

    result = await self.db.execute(
        select(Agent).where(Agent.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        return {"tools_config": {"tools": ["rag"]}, "routing_config": {"strategy": "keyword"}, "degradation_config": {}}

    return {
        "tools_config": agent.tools_config or {"tools": []},
        "routing_config": agent.routing_config or {"strategy": "keyword"},
        "degradation_config": agent.degradation_config or {},
        "model_config": agent.model_config or {}
    }
```

### 6. 降级策略实现

```python
def _quality_check(self, state: AgentState) -> AgentState:
    """质量检查节点"""
    route_target = state.get("route_target")
    retry_count = state.get("metadata", {}).get("retry_count", 0)
    max_retries = self.degradation_config.get("max_retries", 3)

    # 获取当前工具的质量分数
    score_map = {
        "rag": state.get("rag_quality_score", 0),
        "database": state.get("db_quality_score", 0),
        "web": 0.7  # Web 搜索暂不评估质量
    }
    current_score = score_map.get(route_target, 0)

    state.setdefault("metadata", {})
    state["metadata"]["retry_count"] = retry_count + 1
    state["metadata"]["last_quality_score"] = current_score

    return state

def _quality_decision(self, state: AgentState) -> str:
    """质量检查后的路由决策"""
    score = state.get("metadata", {}).get("last_quality_score", 0)
    retry_count = state.get("metadata", {}).get("retry_count", 0)
    max_retries = self.degradation_config.get("max_retries", 3)
    threshold = self.tools_config.get("rag_config", {}).get("similarity_threshold", 0.5)

    if score >= threshold:
        return "pass"

    if retry_count >= max_retries:
        fallback = self.degradation_config.get("fallback", "default")
        if fallback == "error":
            return "fail"
        return "pass"  # 用已有结果生成

    return "retry"  # 降级到下一个工具
```

### 7. 文件修改清单

| 文件 | 修改类型 | 说明 |
|---|---|---|
| `api/v1/core/langgraph_integration.py` | 重构 | 动态图构建、实际服务调用、事件回调 |
| `api/v1/services/conversation_service.py` | 重构 stream_response | 集成 LangGraph 执行、SSE 事件流 |
| `api/v1/routes/conversations.py` | 小改 | 无需修改（已正确调用 stream_response） |

### 8. 调用链路总览

```
前端发送消息
    ↓
POST /api/v1/conversations/{id}/messages
    ↓
conversations.py: send_message()
    ↓
conversation_service.py: stream_response()
    ├── 1. 验证对话权限
    ├── 2. 保存用户消息
    ├── 3. 加载 Agent 配置（从 agents 表）
    ├── 4. 创建事件回调
    ├── 5. AgenticRAGGraph(agent_config).build_graph()
    │       └── 根据 tools_config 动态添加节点
    │       └── 根据 routing_config 设置路由策略
    │       └── 根据 degradation_config 添加质量检查
    ├── 6. graph.ainvoke(initial_state)
    │       ├── intent_router → 路由决策
    │       ├── rag_retrieval → ragflow_client.retrieval()
    │       ├── database_query → datasource_service.execute_sql()
    │       ├── quality_check → 质量评估
    │       └── response_generator → 生成回答
    ├── 7. 事件队列 → yield SSE 事件
    └── 8. 保存助手消息
```
