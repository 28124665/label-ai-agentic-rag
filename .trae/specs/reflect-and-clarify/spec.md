# 设计文档：工具结果反思与主动澄清交互

> **Spec ID**: reflect-and-clarify
> **分支**: dev-kkgraph
> **日期**: 2026-07-24

***

## 1. 背景与目标

### 1.1 现状分析

系统中存在两套并行的 Agent 执行引擎：

| 引擎                | 位置                                    | 状态                     |
| ----------------- | ------------------------------------- | ---------------------- |
| **Canvas ReAct**  | `agent/component/agent_with_tools.py` | 生产使用                   |
| **LangGraph 状态图** | `agent/langgraph/`                    | 新架构，已实现意图路由+工具+质检+幻觉检测 |

**当前问题**：

1. **工具结果反思缺失**：

   * Canvas ReAct 中 `_react_with_tools_streamly_async_simple`（[L411](file:///Users/renwk/workspace/data-knowledge-api/agent/component/agent_with_tools.py#L411)）使用 `build_observation()` 纯字符串拼接，无 LLM 二次分析

   * 旧的 `reflect_async()`（[generator.py:420](file:///Users/renwk/workspace/data-knowledge-api/rag/prompts/generator.py#L420)）被注释掉，Prompt 模板 [reflect.md](file:///Users/renwk/workspace/data-knowledge-api/rag/prompts/reflect.md) 仍存在

   * LangGraph 状态图中无反思节点

2. **主动澄清未闭环**：

   * LangGraph `intent_router` 中 `needs_clarification` 仅打标记（[intent\_router.py:143](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/intent_router.py#L143)）

   * 数据模型 `ClarificationRequest`（[models.py:130](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/routers/models.py#L130)）已定义但未使用

   * 无前端展示 → 用户回答 → 继续执行的完整交互链路

   * Canvas 引擎中已有 `UserFillUp` 组件（[fillup.py](file:///Users/renwk/workspace/data-knowledge-api/agent/component/fillup.py)），可参考其交互模式

### 1.2 设计目标

1. **恢复 LLM 驱动的工具结果反思**：在 ReAct 循环中，工具执行后由 LLM 对结果进行结构化反思，提取关键信息、判断信息充分性、建议下一步
2. **实现完整的主动澄清交互**：意图路由置信度低时，向用户返回澄清问题 → 用户回答 → 继续执行
3. **两套引擎融合**：LangGraph 中新增 reflection 节点；Canvas ReAct 恢复 reflect\_async 并优化
4. **保持流式输出**：反思和澄清都不能阻塞流式响应

***

## 2. 架构设计

### 2.1 整体流程图

```
用户提问
    │
    ▼
┌─────────────┐
│ user_question│
└──────┬──────┘
       │
       ▼
┌─────────────┐    needs_clarification    ┌──────────────┐
│intent_router │─────────────────────────▶│ clarification │
│              │                           │   _node       │
└──────┬──────┘                           └──────┬───────┘
       │                                         │
       │ route_target                            │ 返回澄清问题给前端
       │                                         │ (中断图执行)
       │                                         ▼
       │                                   ┌──────────────┐
       │                                   │  等待用户回答  │
       │                                   │ (下一轮请求)   │
       │                                   └──────┬───────┘
       │                                          │ 用户回答 + clarification_context
       │                                          ▼
       │                                   ┌──────────────┐
       │                                   │ intent_router │ (重新路由)
       │                                   └──────┬───────┘
       │                                          │
       ▼                                          ▼
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│  tool_node   │───▶│ reflection   │───▶│ quality_check │
│(rag/db/web)  │    │   _node      │    │              │
└─────────────┘    └──────────────┘    └──────┬───────┘
                                              │
                                              ▼
                                       ┌──────────────┐
                                       │prompt_assembly│
                                       └──────┬───────┘
                                              ▼
                                       ┌──────────────┐
                                       │ llm_generate  │
                                       └──────┬───────┘
                                              ▼
                                       ┌──────────────┐
                                       │ hallucination │
                                       └──────┬───────┘
                                              ▼
                                       ┌──────────────┐
                                       │ final_answer  │
                                       └──────────────┘
```

### 2.2 两条改动线

#### 改动线 A：LangGraph 状态图（新架构）

新增两个节点：

* **`clarification_node`**：处理 `needs_clarification`，生成澄清问题，中断图执行并返回前端

* **`reflection_node`**：工具执行后对结果进行 LLM 反思

#### 改动线 B：Canvas ReAct（生产引擎）

* **恢复** **`reflect_async`**：在 `_react_with_tools_streamly_async_simple` 中调用

* **增加反思开关**：通过参数 `enable_reflection` 控制是否启用（默认开启，支持降级）

* **优化反思 Prompt**：复用并精简 [reflect.md](file:///Users/renwk/workspace/data-knowledge-api/rag/prompts/reflect.md)

***

## 3. 详细设计 — 工具结果反思（Reflection）

### 3.1 LangGraph reflection\_node

#### 3.1.1 状态字段扩展

在 [state.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/state.py) 的 `AgentState` 中新增：

```python
# ========== 反思相关 ==========
reflection_result: Optional[dict]  # 反思结果
# 结构: {
#     "key_findings": list[str],      # 关键发现
#     "info_sufficient": bool,         # 信息是否充分
#     "gaps": list[str],              # 信息缺口
#     "next_action": str,             # 建议下一步
#     "reflection_text": str,         # 完整反思文本
# }
```

#### 3.1.2 节点位置

```
tool_node (rag/db/web) → reflection_node → quality_check
```

`reflection_node` 在工具执行后、质量检查前执行，将反思结果注入 state 供下游使用。

#### 3.1.3 节点实现

新建 `agent/langgraph/nodes/reflection.py`：

```python
async def reflection_node(state: AgentState) -> dict[str, Any]:
    """工具结果反思节点。
    
    对 RAG/DB/Web 工具返回的结果进行 LLM 驱动的结构化反思，
    输出关键发现、信息充分性判断和建议下一步。
    """
    # 1. 收集工具结果
    tool_results = _collect_tool_results(state)
    if not tool_results:
        return {"reflection_result": None}
    
    # 2. 构建 Prompt
    user_question = state.get("user_question", "")
    prompt = _build_reflection_prompt(user_question, tool_results)
    
    # 3. 调用 LLM（带超时和降级）
    try:
        reflection_text = await _call_llm_with_timeout(state, prompt, timeout_s=5)
        result = _parse_reflection(reflection_text)
    except Exception as e:
        logger.warning(f"[reflection] LLM 反思失败，降级为空反思: {e}")
        result = _fallback_reflection(tool_results)
    
    return {"reflection_result": result}
```

#### 3.1.4 反思 Prompt 设计

新建 `rag/prompts/reflection_v2.md`：

````markdown
**Context**:
- User question: {{ question }}
- Tool results:
{% for result in tool_results %}
### {{ result.tool }} result:
{{ result.content | truncate(2000) }}
{% endfor %}

**Task**: Analyze the tool results and provide structured reflection.

Output JSON:
```json
{
  "key_findings": ["finding 1", "finding 2"],
  "info_sufficient": true/false,
  "gaps": ["missing info 1"],
  "next_action": "proceed_to_generate" | "need_more_retrieval" | "need_web_search"
}
````

````

#### 3.1.5 降级策略

| 场景 | 降级方式 |
|------|---------|
| LLM 超时（>5s） | 返回 `info_sufficient=true`，直接进入 quality_check |
| LLM 解析失败 | 返回 `info_sufficient=true`，降级为旧版无反思 |
| 无工具结果 | 跳过反思，返回 None |

### 3.2 Canvas ReAct 恢复 reflect_async

#### 3.2.1 修改 agent_with_tools.py

在 `_react_with_tools_streamly_async_simple` 中恢复 LLM 反思：

```python
# 之前（L411）
reflection = build_observation(results)
append_user_content(hist, reflection)

# 修改后
if self._param.enable_reflection:
    # LLM 驱动反思
    try:
        reflection = await asyncio.wait_for(
            reflect_async(self.chat_mdl, hist, results, user_defined_prompt),
            timeout=10
        )
    except asyncio.TimeoutError:
        logger.warning("[ReAct] 反思超时，降级为 observation")
        reflection = build_observation(results)
    except Exception as e:
        logger.warning(f"[ReAct] 反思失败，降级: {e}")
        reflection = build_observation(results)
else:
    reflection = build_observation(results)

append_user_content(hist, reflection)
self.callback("reflection", {}, str(reflection), elapsed_time=timer()-st)
````

#### 3.2.2 参数扩展

在 `AgentParam`（[agent\_with\_tools.py:39](file:///Users/renwk/workspace/data-knowledge-api/agent/component/agent_with_tools.py#L39)）中新增：

```python
self.enable_reflection = True       # 是否启用 LLM 反思
self.reflection_timeout = 10        # 反思超时（秒）
```

#### 3.2.3 优化反思 Prompt

复用 [reflect.md](file:///Users/renwk/workspace/data-knowledge-api/rag/prompts/reflect.md) 但精简为轻量版，控制输出在 200 字以内：

```markdown
**Goal**: {{ goal }}
**Tool calls**:
{% for call in tool_calls %}
- `{{ call.name }}`: {{ call.result | truncate(1000) }}
{% endfor %}

Reflect concisely (max 200 words):
1. Did the results answer the goal?
2. What key information was found?
3. Is more retrieval needed?
```

***

## 4. 详细设计 — 主动澄清交互（Clarification）

### 4.1 核心挑战：LangGraph 中的中断与恢复

LangGraph 支持 `interrupt` 机制，可以在图执行过程中暂停，等待外部输入后恢复。利用此机制实现澄清交互。

### 4.2 状态字段扩展

在 `AgentState` 中新增：

```python
# ========== 澄清相关 ==========
clarification_request: Optional[dict]   # 待回答的澄清请求
clarification_context: Optional[str]     # 用户对澄清的回答
# clarification_request 结构:
# {
#     "question": "您是想查询供应链数据还是人力资源数据？",
#     "options": ["database", "rag", "web"],
#     "route_target_hint": "hybrid"
# }
```

### 4.3 LangGraph clarification\_node

新建 `agent/langgraph/nodes/clarification.py`：

```python
from langgraph.types import interrupt, Command

async def clarification_node(state: AgentState) -> dict[str, Any]:
    """澄清交互节点。
    
    当 intent_router 判断 needs_clarification 时进入此节点。
    通过 langgraph interrupt 机制暂停执行，等待用户回答。
    """
    route_decision = state.get("route_decision")
    if not route_decision:
        return {"route_target": "chitchat"}
    
    # 从 route_decision.metadata 提取澄清信息
    metadata = route_decision.metadata or {}
    question = metadata.get("clarification_question", "您的问题不够明确，请补充更多细节。")
    options = metadata.get("clarification_options", [])
    
    clarification_request = {
        "question": question,
        "options": options,
        "original_question": state.get("user_question", ""),
    }
    
    # 使用 langgraph interrupt 暂停执行
    # 前端收到 clarification_request 后展示给用户
    # 用户回答后，通过 Command(resume=...) 恢复执行
    user_answer = interrupt(clarification_request)
    
    # 用户回答后恢复，user_answer 为用户输入
    # 将用户回答作为新问题，重新进入 intent_router
    return {
        "user_question": user_answer,  # 用回答后的完整问题重新路由
        "clarification_context": user_answer,
        "route_target": "",  # 清空，触发重新路由
    }
```

### 4.4 图结构调整

修改 [graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py)：

```python
# 新增节点
graph.add_node("clarification", clarification_node)

# intent_router → 条件路由（新增 clarification 分支）
graph.add_conditional_edges(
    "intent_router",
    route_decision,
    {
        "rag_tool": "rag_tool",
        "db_tool": "db_tool",
        "prompt_assembly": "prompt_assembly",
        "clarification": "clarification",  # 新增
    },
)

# clarification → intent_router (用户回答后重新路由)
graph.add_edge("clarification", "intent_router")

# 新增 reflection 节点
graph.add_node("reflection", reflection_node)

# 调整边: tool → reflection → quality_check
# 之前: db_tool → quality_check
# 之后: db_tool → reflection → quality_check
graph.add_edge("rag_tool", "reflection")
graph.add_edge("db_tool", "reflection")
graph.add_edge("web_tool", "reflection")
graph.add_edge("reflection", "quality_check")
```

### 4.5 route\_decision 函数扩展

修改 [intent\_router.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/intent_router.py) 的 `route_decision`：

```python
def route_decision(state: AgentState) -> str:
    route_target = state.get("route_target", "chitchat")
    route_decision = state.get("route_decision")
    
    # 检查是否需要澄清
    if route_decision and route_decision.metadata.get("needs_clarification"):
        return "clarification"
    
    if route_target == "rag":
        return "rag_tool"
    elif route_target == "database":
        return "db_tool"
    elif route_target == "hybrid":
        return "rag_tool"
    else:
        return "prompt_assembly"
```

### 4.6 API 层改造

#### 4.6.1 新增澄清 API

在 `api/apps/` 下新建 `clarification_app.py` 或扩展 `conversation_app.py`：

```python
@manager.route("/clarify", methods=["POST"])
@login_required
async def submit_clarification():
    """提交用户对澄清问题的回答。
    
    请求体:
    {
        "thread_id": "xxx",           # 图执行线程ID
        "clarification_answer": "xxx"  # 用户回答
    }
    
    响应:
    - 流式返回后续执行结果（与 /completion 相同的 SSE 格式）
    """
    req = await request.json
    thread_id = req.get("thread_id")
    answer = req.get("clarification_answer")
    
    runner = get_runner()
    # 使用 langgraph 的 Command(resume=...) 恢复中断的图执行
    async for node_name, node_output in runner.aresume(thread_id, answer):
        yield _format_sse(node_name, node_output)
```

#### 4.6.2 Runner 扩展

在 [runner.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/runner.py) 中新增恢复方法：

```python
async def arun_with_checkpointer(
    self,
    user_question: str,
    thread_id: str = None,
    **kwargs
) -> dict[str, Any]:
    """支持 checkpointer 的执行（用于澄清中断/恢复）。
    
    使用 MemorySaver 或 PostgreSQLSaver 持久化图状态，
    支持 interrupt 后通过 aresume 恢复。
    """
    from langgraph.checkpoint.memory import MemorySaver
    checkpointer = MemorySaver()
    compiled = self._get_compiled_graph().compile(checkpointer=checkpointer)
    
    thread_id = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    
    initial_state = self._build_initial_state(user_question, **kwargs)
    final_state = await compiled.ainvoke(initial_state, config=config)
    return {"thread_id": thread_id, "final_state": final_state}

async def aresume(self, thread_id: str, user_answer: str):
    """恢复中断的图执行。
    
    Args:
        thread_id: 中断时的线程ID
        user_answer: 用户对澄清问题的回答
    """
    from langgraph.types import Command
    compiled = self._get_compiled_graph()
    config = {"configurable": {"thread_id": thread_id}}
    
    async for output in compiled.astream(
        Command(resume=user_answer), config=config
    ):
        for node_name, node_output in output.items():
            yield node_name, node_output
```

### 4.7 前端交互设计

#### 4.7.1 SSE 事件扩展

在现有 SSE 流中新增 `clarification` 事件类型：

```typescript
// SSE 事件流
type SSEEvent = 
  | { type: "node_complete", node: string, data: any }
  | { type: "clarification", data: ClarificationRequest }  // 新增
  | { type: "answer_chunk", content: string }
  | { type: "done" }

interface ClarificationRequest {
  question: string        // 澄清问题
  options: string[]       // 可选选项
  thread_id: string       // 恢复执行用的线程ID
  original_question: string  // 原始问题
}
```

#### 4.7.2 前端处理流程

```typescript
// 前端接收 SSE
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  if (data.type === "clarification") {
    // 展示澄清 UI
    showClarificationDialog(data.data);
    // 用户提交后
    submitClarification(data.data.thread_id, userAnswer);
  } else if (data.type === "answer_chunk") {
    appendAnswer(data.content);
  }
};

// 提交澄清回答（开启新的 SSE 流）
async function submitClarification(threadId: string, answer: string) {
  const eventSource = new EventSource(
    `/api/v1/clarify?thread_id=${threadId}&answer=${encodeURIComponent(answer)}`
  );
  // 继续处理后续 SSE 事件...
}
```

#### 4.7.3 前端组件

新增 `ClarificationDialog` 组件：

* 展示澄清问题

* 展示可选选项（如有）

* 提供输入框供用户补充

* 提交按钮

***

## 5. 融合策略：两套引擎如何协同

### 5.1 引擎选择

| 场景                    | 使用引擎         | 理由                     |
| --------------------- | ------------ | ---------------------- |
| 画布编排（Agent 节点）        | Canvas ReAct | 可视化编排，已有完整工具链          |
| API 直调（LangGraph API） | LangGraph    | 状态图更清晰，支持 checkpointer |
| 需要澄清交互                | LangGraph    | 利用 interrupt 机制        |

### 5.2 Canvas ReAct 中的澄清

Canvas ReAct 不使用 langgraph interrupt，而是复用现有 `UserFillUp` 组件模式：

* 当 ReAct 循环中发现需要澄清时，通过 `self.set_output("clarification_request", ...)` 输出

* 画布下游连接 `UserFillUp` 节点等待用户输入

* 用户输入后触发新一轮画布执行

### 5.3 共享组件

反思 Prompt 模板和 LLM 调用逻辑提取为共享模块：

```
api/utils/reflection.py
  ├── build_reflection_prompt()    # 构建 Prompt
  ├── call_reflection_llm()        # 调用 LLM
  └── parse_reflection_result()    # 解析结果
```

Canvas ReAct 和 LangGraph reflection\_node 共用此模块。

***

## 6. 配置项

### 6.1 Agent 配置扩展

```yaml
agent:
  reflection:
    enabled: true                    # 是否启用 LLM 反思
    timeout_s: 10                    # 反思超时
    max_reflection_tokens: 500       # 反思最大 token
    fallback_on_failure: true        # 失败时降级为 observation
  
  clarification:
    enabled: true                    # 是否启用主动澄清
    confidence_threshold: 0.6        # 触发澄清的置信度阈值
    max_clarification_rounds: 2      # 最大澄清轮次（避免无限澄清）
```

***

## 7. 实现计划

### Phase 1：工具结果反思（LangGraph）

1. 扩展 `AgentState`，新增 `reflection_result` 字段
2. 新建 `agent/langgraph/nodes/reflection.py`
3. 新建 `rag/prompts/reflection_v2.md`
4. 修改 `graph.py`，新增 reflection 节点和边
5. 新建 `api/utils/reflection.py` 共享模块
6. 编写单元测试

### Phase 2：工具结果反思（Canvas ReAct）

1. 修改 `AgentParam`，新增 `enable_reflection` 参数
2. 修改 `agent_with_tools.py`，恢复 `reflect_async` 调用
3. 精简 `rag/prompts/reflect.md`
4. 更新前端 Agent 表单，新增反思开关
5. 编写单元测试

### Phase 3：主动澄清交互（LangGraph）

1. 扩展 `AgentState`，新增澄清字段
2. 新建 `agent/langgraph/nodes/clarification.py`
3. 修改 `graph.py`，新增 clarification 节点和边
4. 修改 `intent_router.py` 的 `route_decision` 函数
5. 扩展 `runner.py`，新增 `arun_with_checkpointer` 和 `aresume`
6. 新建/扩展 API 端点 `/clarify`
7. 编写单元测试

### Phase 4：前端澄清交互

1. 新增 `ClarificationDialog` 组件
2. 扩展 SSE 事件处理，支持 `clarification` 事件
3. 实现 `/clarify` 请求逻辑
4. 国际化文案

### Phase 5：集成测试

1. LangGraph 完整流程测试（含反思+澄清）
2. Canvas ReAct 反思测试
3. 端到端澄清交互测试

***

## 8. 风险与对策

| 风险                      | 影响     | 对策               |
| ----------------------- | ------ | ---------------- |
| 反思增加延迟（+3-5s）           | 用户体验下降 | 超时降级 + 异步反思      |
| 澄清导致交互打断                | 用户反感   | 低置信度才触发 + 最大轮次限制 |
| LangGraph interrupt 兼容性 | 图执行异常  | 充分测试 + 降级为直接路由   |
| 前端 SSE 中断处理复杂           | 前端状态混乱 | 明确事件类型 + 清晰的状态机  |

***

## 9. 文件清单

### 新建文件

| 文件                                            | 说明             |
| --------------------------------------------- | -------------- |
| `agent/langgraph/nodes/reflection.py`         | LangGraph 反思节点 |
| `agent/langgraph/nodes/clarification.py`      | LangGraph 澄清节点 |
| `rag/prompts/reflection_v2.md`                | 反思 Prompt 模板   |
| `api/utils/reflection.py`                     | 反思共享模块         |
| `web/src/components/clarification-dialog.tsx` | 前端澄清组件         |

### 修改文件

| 文件                                       | 改动                                     |
| ---------------------------------------- | -------------------------------------- |
| `agent/langgraph/state.py`               | 新增 reflection\_result、clarification 字段 |
| `agent/langgraph/graph.py`               | 新增 reflection、clarification 节点和边       |
| `agent/langgraph/nodes/intent_router.py` | route\_decision 新增 clarification 分支    |
| `agent/langgraph/runner.py`              | 新增 arun\_with\_checkpointer、aresume    |
| `agent/component/agent_with_tools.py`    | 恢复 reflect\_async + 参数扩展               |
| `rag/prompts/reflect.md`                 | 精简为轻量版                                 |
| `api/apps/conversation_app.py`           | 新增 /clarify 端点                         |
| `web/src/locales/zh.ts`                  | 澄清相关中文文案                               |
| `web/src/locales/en.ts`                  | 澄清相关英文文案                               |

