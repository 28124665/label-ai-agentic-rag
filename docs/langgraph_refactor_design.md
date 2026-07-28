# LangGraph + RAGFlow 重构落地设计文档

> **项目名称**：Agentic RAG 系统架构重构  
> **文档版本**：V1.0  
> **目标架构**：LangGraph 全局编排 + RAGFlow 作为知识库工具  
> **关联文档**：[RAGFlow 二期开发成果技术报告](./ragflow_phase2_report.md)、[Database Tool 设计文档](./dbtoolprd.md)

---

## 一、重构背景与目标

### 1.1 当前架构痛点

| 问题 | 描述 | 影响 |
|------|------|------|
| **流程编排耦合** | 所有节点（查询重写、检索、评估、重试、生成、幻觉检测）都在 RAGFlow Canvas 中硬编码 | 难以扩展新工具、难以实现复杂的多工具协同 |
| **重试逻辑混乱** | `rag_retry` 同时控制内部重试和外部降级（Web 搜索），职责不清 | 降级策略难以灵活配置 |
| **状态传递复杂** | 通过 Canvas 的 `set_state` / `get_variable_value` 传递状态，缺乏统一的状态管理 | 状态追踪困难、调试复杂 |
| **工具封装不足** | Database Tool、Web Search Tool 等直接暴露在 Canvas 中 | 工具内部逻辑泄露、接口不清晰 |

### 1.2 重构目标

| 目标 | 描述 | 验收标准 |
|------|------|----------|
| **职责分离** | LangGraph 负责"做什么"（全局决策），RAG Tool 负责"怎么做得好"（检索优化） | LangGraph 节点数 ≤ 15，RAG Tool 内部节点数 ≤ 8 |
| **接口清晰** | 每个工具对外暴露统一的输入输出接口 | 所有工具实现 `ToolBase` 接口，输入输出符合 JSON Schema |
| **状态可追踪** | LangGraph 提供统一的状态管理机制 | 所有节点状态可通过 `get_state()` 查询 |
| **降级灵活** | LangGraph 根据质量评分动态决策降级策略 | 支持至少 3 种降级路径（RAG → DB → Web） |
| **可观测性增强** | LangGraph 层统一记录全局指标，工具内部记录细粒度日志 | Prometheus 指标覆盖率 100%，结构化日志完整 |

---

## 二、架构设计原则

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph 全局编排层                       │
│  ─────────────────────────────────────────────────────────  │
│  职责：工具选择、降级策略、多源融合、最终生成、质量控制          │
│  状态：AgentState（统一状态对象）                              │
│  节点：user_question → intent_router → ... → final_answer    │
└─────────────────────────────────────────────────────────────┘
                              ↓
              ┌───────────────┼───────────────┐
              ↓               ↓               ↓
    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
    │  RAG Tool   │  │  DB Tool    │  │  Web Tool   │
    │  (内部流程) │  │  (内部流程) │  │  (内部流程) │
    └─────────────┘  └─────────────┘  └─────────────┘
```

### 2.2 设计原则

| 原则 | 描述 | 示例 |
|------|------|------|
| **单一职责** | 每个节点只负责一个明确的职责 | `intent_router` 只做路由决策，不做检索 |
| **接口隔离** | 工具对外暴露最小接口，内部逻辑封装 | RAG Tool 只返回 `{docs, quality_score}`，不暴露 Rerank 细节 |
| **依赖倒置** | LangGraph 依赖工具接口，不依赖具体实现 | LangGraph 调用 `tool.invoke()`，不关心是 RAG 还是 DB |
| **开闭原则** | 新增工具不修改 LangGraph 代码 | 新增 Report Tool 只需实现 `ToolBase` 接口 |

---

## 三、整体架构设计

### 3.1 LangGraph 状态图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         LangGraph State Graph                        │
│                                                                       │
│  ┌──────────────┐                                                    │
│  │ user_question│                                                    │
│  │ (入口节点)   │                                                    │
│  └──────┬───────┘                                                    │
│         ↓                                                             │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      │
│  │intent_router │────→│  rag_tool    │────→│  db_tool     │      │
│  │ (路由决策)   │      │ (知识库检索) │      │ (数据库查询) │      │
│  └──────┬───────┘      └──────┬───────┘      └──────┬───────┘      │
│         │                     │                     │               │
│         │                     ↓                     ↓               │
│         │              ┌──────────────┐      ┌──────────────┐      │
│         │              │quality_check │      │quality_check │      │
│         │              │ (质量评估)   │      │ (质量评估)   │      │
│         │              └──────┬───────┘      └──────┬───────┘      │
│         │                     │                     │               │
│         │                     ↓                     ↓               │
│         │              ┌──────────────┐      ┌──────────────┐      │
│         │              │ should_retry │      │ should_retry │      │
│         │              │ (重试决策)   │      │ (重试决策)   │      │
│         │              └──────┬───────┘      └──────┬───────┘      │
│         │                     │                     │               │
│         │                     ↓                     ↓               │
│         │              ┌──────────────┐      ┌──────────────┐      │
│         │              │  web_tool    │      │  web_tool    │      │
│         │              │ (Web 搜索)   │      │ (Web 搜索)   │      │
│         │              └──────┬───────┘      └──────┬───────┘      │
│         │                     │                     │               │
│         └─────────────────────┴─────────────────────┘               │
│                               ↓                                     │
│                      ┌──────────────┐                              │
│                      │prompt_assembly│                              │
│                      │ (Prompt 组装)│                              │
│                      └──────┬───────┘                              │
│                             ↓                                       │
│                      ┌──────────────┐                              │
│                      │ llm_generate │                              │
│                      │ (LLM 生成)   │                              │
│                      └──────┬───────┘                              │
│                             ↓                                       │
│                      ┌──────────────┐                              │
│                      │hallucination │                              │
│                      │ (幻觉检测)   │                              │
│                      └──────┬───────┘                              │
│                             ↓                                       │
│                      ┌──────────────┐                              │
│                      │observability │                              │
│                      │ (可观测性)   │                              │
│                      └──────┬───────┘                              │
│                             ↓                                       │
│                      ┌──────────────┐                              │
│                      │ final_answer │                              │
│                      │ (返回答案)   │                              │
│                      └──────────────┘                              │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 LangGraph 状态定义

```python
from typing import TypedDict, Annotated, Literal
from langgraph.graph import StateGraph
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    """LangGraph 全局状态"""
    
    # 用户输入
    user_question: str
    query_lang: Literal["zh_CN", "zh_TW", "en"]
    
    # 路由决策
    route_target: Literal["rag", "database", "hybrid", "web", "chitchat"]
    
    # RAG Tool 输出
    rag_docs: list[dict]  # [{content, score, source}]
    rag_quality_score: float  # 0.0 ~ 1.0
    rag_has_relevant: bool
    rag_relevant_count: int
    
    # Database Tool 输出
    db_result: dict  # {sql, rows, row_count, source}
    db_quality_score: float
    
    # Web Tool 输出
    web_docs: list[dict]  # [{content, url, title}]
    
    # 融合后的上下文
    merged_context: str
    
    # LLM 生成
    generated_answer: str
    
    # 幻觉检测
    hallucination_score: float  # 0.0 ~ 1.0
    hallucination_action: Literal["pass", "filter", "regenerate", "reject"]
    
    # 重试控制
    retry_count: int
    max_retries: int
    
    # 可观测性
    trace_id: str
    node_timings: dict  # {node_name: latency_ms}
```

---

## 四、RAG Tool 内部流程设计

### 4.1 RAG Tool 接口定义

```python
from agent.tools.base import ToolBase, ToolParamBase
from typing import TypedDict

class RAGToolInput(TypedDict):
    """RAG Tool 输入"""
    query: str  # 用户查询（已繁简转换）
    query_lang: Literal["zh_CN", "zh_TW", "en"]
    top_k: int = 5
    enable_rewrite: bool = True
    enable_rerank: bool = True

class RAGToolOutput(TypedDict):
    """RAG Tool 输出"""
    docs: list[dict]  # [{content, score, source, chunk_id}]
    quality_score: float  # 0.0 ~ 1.0，Grader 评估结果
    has_relevant: bool  # 是否有相关文档
    relevant_count: int  # 相关文档数量
    top_score: float  # 最高分
    rewrite_history: list[str]  # 重写历史（用于调试）

class RAGTool(ToolBase):
    """RAG 知识库检索工具"""
    
    component_name = "RAGTool"
    
    def _invoke(self, **kwargs) -> RAGToolOutput:
        """
        主执行流程
        
        内部流程：
        1. 查询预处理（语言检测、繁简转换）
        2. 查询优化（复杂度分析、策略选择、重写）
        3. 混合检索（BM25 + 向量）
        4. Rerank 精排
        5. 检索质量检测（Grader）
        6. 内部重试（最多 1 次，回到步骤 2）
        7. 返回结果
        """
        # 解析输入
        input_data = self._parse_input(kwargs)
        
        # 执行内部流程
        result = self._execute_internal_flow(input_data)
        
        # 格式化输出
        return self._format_output(result)
```

### 4.2 RAG Tool 内部流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│                      RAG Tool 内部流程                               │
│                                                                       │
│  ┌──────────────┐                                                    │
│  │ preprocessing│  语言检测 + 繁简转换                                │
│  │ (查询预处理) │  输入：query, query_lang                            │
│  └──────┬───────┘  输出：query_simplified                             │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ optimization │  复杂度分析 + 策略选择 + 重写                       │
│  │ (查询优化)   │  输入：query_simplified                             │
│  └──────┬───────┘  输出：rewritten_query, complexity                  │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ rag_retrieval│  BM25 + 向量检索                                    │
│  │ (混合检索)   │  输入：rewritten_query                              │
│  └──────┬───────┘  输出：candidate_docs                               │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ rag_rerank   │  Rerank 精排                                        │
│  │ (精排)       │  输入：candidate_docs                               │
│  └──────┬───────┘  输出：ranked_docs                                  │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ rag_grader   │  检索质量检测（LLM/NLI/Rerank 降级）                │
│  │ (质量评估)   │  输入：ranked_docs                                  │
│  └──────┬───────┘  输出：quality_score, has_relevant, relevant_count  │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ internal_retry│  内部重试判断                                      │
│  │ (内部重试)   │  条件：has_relevant=False 且 retry_count < 1        │
│  └──────┬───────┘  决策：→ optimization（重试）或 → 返回结果          │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ return_result│  返回结果给 LangGraph                              │
│  └──────────────┘  输出：{docs, quality_score, has_relevant, ...}     │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.3 RAG Tool 关键实现

```python
class RAGToolInternal:
    """RAG Tool 内部流程实现"""
    
    def __init__(self, canvas, param):
        self.canvas = canvas
        self.param = param
        
        # 初始化内部组件
        self.preprocessor = QueryPreprocessor()
        self.rewriter = QueryRewriter()
        self.retriever = HybridRetriever()
        self.reranker = MultilingualReranker()
        self.grader = Grader()
    
    def execute(self, input_data: RAGToolInput) -> RAGToolOutput:
        """执行内部流程"""
        
        # 1. 查询预处理
        preprocessed = self.preprocessor.process(
            query=input_data["query"],
            query_lang=input_data["query_lang"]
        )
        
        # 2. 查询优化（可重试）
        retry_count = 0
        max_internal_retries = 1
        
        while retry_count <= max_internal_retries:
            # 2.1 查询重写
            rewritten = self.rewriter.rewrite(
                query=preprocessed["query_simplified"],
                retry_count=retry_count
            )
            
            # 2.2 混合检索
            candidates = self.retriever.retrieve(
                query=rewritten["rewritten_query"],
                top_k=input_data["top_k"]
            )
            
            # 2.3 Rerank 精排
            ranked = self.reranker.rerank(
                query=rewritten["rewritten_query"],
                docs=candidates
            )
            
            # 2.4 质量评估
            quality = self.grader.evaluate(
                query=rewritten["rewritten_query"],
                docs=ranked
            )
            
            # 2.5 内部重试判断
            if quality["has_relevant"] or retry_count >= max_internal_retries:
                break
            
            retry_count += 1
        
        # 3. 返回结果
        return {
            "docs": ranked,
            "quality_score": quality["score"],
            "has_relevant": quality["has_relevant"],
            "relevant_count": quality["relevant_count"],
            "top_score": quality["top_score"],
            "rewrite_history": rewritten.get("history", [])
        }
```

---

## 五、Database Tool 封装设计

### 5.1 Database Tool 接口定义

```python
class DatabaseToolInput(TypedDict):
    """Database Tool 输入"""
    query: str  # 用户查询（自然语言）
    query_lang: Literal["zh_CN", "zh_TW", "en"]
    db_id: str = None  # 可选：指定数据库
    enable_template: bool = True  # 是否优先使用模板

class DatabaseToolOutput(TypedDict):
    """Database Tool 输出"""
    sql: str  # 生成的 SQL
    rows: list[dict]  # 查询结果
    row_count: int
    source: str  # 数据来源描述
    quality_score: float  # SQL 语义校验评分
    execution_time_ms: int

class DatabaseTool(ToolBase):
    """数据库查询工具"""
    
    component_name = "DatabaseTool"
    
    def _invoke(self, **kwargs) -> DatabaseToolOutput:
        """
        主执行流程
        
        内部流程：
        1. 意图路由（匹配目标数据库）
        2. 混合模式路由（模板匹配 / NL-to-SQL）
        3. 渐进式 Schema 发现
        4. SQL 生成与安全检查
        5. 执行查询（带自愈重试）
        6. 结果格式化
        """
        # 解析输入
        input_data = self._parse_input(kwargs)
        
        # 执行内部流程
        result = self._execute_internal_flow(input_data)
        
        # 格式化输出
        return self._format_output(result)
```

### 5.2 Database Tool 内部流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Database Tool 内部流程                            │
│                                                                       │
│  ┌──────────────┐                                                    │
│  │ intent_route │  意图路由（匹配目标数据库）                         │
│  │ (意图路由)   │  输入：query                                        │
│  └──────┬───────┘  输出：db_id                                        │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ template_match│  模板匹配（高频场景）                              │
│  │ (模板匹配)   │  输入：query, db_id                                 │
│  └──────┬───────┘  输出：sql, params（或 None）                       │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ schema_discovery│  渐进式 Schema 发现                              │
│  │ (Schema 发现)│  输入：query, db_id                                 │
│  └──────┬───────┘  输出：schema                                       │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ sql_generate │  NL-to-SQL 生成（如未匹配模板）                     │
│  │ (SQL 生成)   │  输入：query, schema                                │
│  └──────┬───────┘  输出：sql                                          │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ sql_validate │  SQL 安全检查（只读、黑名单、权限）                 │
│  │ (安全检查)   │  输入：sql                                          │
│  └──────┬───────┘  输出：validated_sql                                │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ sql_execute  │  执行查询（带自愈重试）                             │
│  │ (执行查询)   │  输入：validated_sql                                │
│  └──────┬───────┘  输出：rows, execution_time_ms                      │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ result_format│  结果格式化（自然语言 + Markdown）                  │
│  │ (结果格式化) │  输入：rows                                         │
│  └──────┬───────┘  输出：formatted_result                             │
│         ↓                                                             │
│  ┌──────────────┐                                                    │
│  │ return_result│  返回结果给 LangGraph                              │
│  └──────────────┘  输出：{sql, rows, row_count, source, ...}          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 六、LangGraph 节点实现

### 6.1 意图路由节点

```python
from langgraph.graph import StateGraph

def intent_router(state: AgentState) -> AgentState:
    """
    意图路由节点
    
    决策逻辑：
    1. 含数字/统计/聚合词 → database
    2. 含精确实体/ID → database
    3. 模糊/概念/解释类 → rag
    4. 混合（实体+概念） → hybrid
    5. 闲聊/问候 → chitchat
    """
    query = state["user_question"]
    
    # 规则匹配（可替换为 LLM 分类）
    if contains_aggregation_keywords(query):
        route_target = "database"
    elif contains_precise_entity(query):
        route_target = "database"
    elif contains_concept_keywords(query):
        route_target = "rag"
    elif contains_mixed_intent(query):
        route_target = "hybrid"
    else:
        route_target = "chitchat"
    
    return {**state, "route_target": route_target}
```

### 6.2 质量评估与重试决策节点

```python
def quality_check_and_retry_decision(state: AgentState) -> AgentState:
    """
    质量评估与重试决策节点
    
    决策逻辑：
    1. 质量评分高（≥ 0.7）→ 进入 prompt_assembly
    2. 质量评分低（< 0.7）且重试配额未用尽 → 再次调用工具
    3. 质量评分低且配额用尽 → 降级到 web_tool
    """
    quality_score = state.get("rag_quality_score", 0.0)
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)
    
    if quality_score >= 0.7:
        # 质量合格，进入下一步
        return state
    
    if retry_count < max_retries:
        # 重试配额未用尽，再次调用工具
        return {**state, "retry_count": retry_count + 1}
    
    # 配额用尽，降级到 Web 搜索
    return {**state, "route_target": "web"}
```

### 6.3 Prompt 组装节点

```python
def prompt_assembly(state: AgentState) -> AgentState:
    """
    Prompt 组装节点
    
    整合来自 DB、RAG、Web 等多源结果，构建最终上下文
    """
    context_parts = []
    
    # RAG 文档
    if state.get("rag_docs"):
        rag_context = "\n\n".join([
            f"[文档 {i+1}] {doc['content']}\n来源：{doc['source']}"
            for i, doc in enumerate(state["rag_docs"])
        ])
        context_parts.append(f"=== 知识库检索结果 ===\n{rag_context}")
    
    # Database 结果
    if state.get("db_result"):
        db_context = f"SQL: {state['db_result']['sql']}\n\n{state['db_result'].get('markdown_table', '')}"
        context_parts.append(f"=== 数据库查询结果 ===\n{db_context}")
    
    # Web 搜索结果
    if state.get("web_docs"):
        web_context = "\n\n".join([
            f"[网页 {i+1}] {doc['content']}\n来源：{doc['url']}"
            for i, doc in enumerate(state["web_docs"])
        ])
        context_parts.append(f"=== Web 搜索结果 ===\n{web_context}")
    
    merged_context = "\n\n".join(context_parts)
    
    return {**state, "merged_context": merged_context}
```

### 6.4 幻觉检测节点

```python
def hallucination_detection(state: AgentState) -> AgentState:
    """
    幻觉检测节点
    
    检测 LLM 生成内容的事实性，决策是否重新生成或拒答
    """
    answer = state["generated_answer"]
    context = state["merged_context"]
    
    # 调用幻觉检测器
    detector = HallucinationDetector()
    result = detector.detect(answer=answer, context=context)
    
    # 分级处置
    if result["score"] >= 0.85:
        action = "pass"
    elif result["score"] >= 0.6:
        action = "filter"
    elif result["score"] >= 0.3:
        action = "regenerate"
    else:
        action = "reject"
    
    return {
        **state,
        "hallucination_score": result["score"],
        "hallucination_action": action
    }
```

---

## 七、LangGraph 状态图实现

### 7.1 状态图构建

```python
from langgraph.graph import StateGraph, END

def build_agent_graph() -> StateGraph:
    """构建 Agent 状态图"""
    
    # 创建状态图
    workflow = StateGraph(AgentState)
    
    # 添加节点
    workflow.add_node("user_question", user_question_node)
    workflow.add_node("intent_router", intent_router)
    workflow.add_node("rag_tool", rag_tool_node)
    workflow.add_node("db_tool", db_tool_node)
    workflow.add_node("web_tool", web_tool_node)
    workflow.add_node("quality_check", quality_check_and_retry_decision)
    workflow.add_node("prompt_assembly", prompt_assembly)
    workflow.add_node("llm_generate", llm_generate_node)
    workflow.add_node("hallucination", hallucination_detection)
    workflow.add_node("observability", observability_node)
    workflow.add_node("final_answer", final_answer_node)
    
    # 设置入口
    workflow.set_entry_point("user_question")
    
    # 添加边
    workflow.add_edge("user_question", "intent_router")
    
    # 条件路由
    workflow.add_conditional_edges(
        "intent_router",
        route_decision,
        {
            "rag": "rag_tool",
            "database": "db_tool",
            "hybrid": "rag_tool",  # 混合模式先走 RAG
            "web": "web_tool",
            "chitchat": "final_answer"
        }
    )
    
    workflow.add_edge("rag_tool", "quality_check")
    workflow.add_edge("db_tool", "quality_check")
    workflow.add_edge("web_tool", "prompt_assembly")
    
    # 质量检查后的条件路由
    workflow.add_conditional_edges(
        "quality_check",
        retry_decision,
        {
            "pass": "prompt_assembly",
            "retry_rag": "rag_tool",
            "retry_db": "db_tool",
            "fallback_web": "web_tool"
        }
    )
    
    workflow.add_edge("prompt_assembly", "llm_generate")
    workflow.add_edge("llm_generate", "hallucination")
    
    # 幻觉检测后的条件路由
    workflow.add_conditional_edges(
        "hallucination",
        hallucination_decision,
        {
            "pass": "observability",
            "filter": "observability",
            "regenerate": "llm_generate",
            "reject": "final_answer"
        }
    )
    
    workflow.add_edge("observability", "final_answer")
    workflow.add_edge("final_answer", END)
    
    return workflow
```

### 7.2 条件路由函数

```python
def route_decision(state: AgentState) -> str:
    """路由决策函数"""
    return state["route_target"]

def retry_decision(state: AgentState) -> str:
    """重试决策函数"""
    quality_score = state.get("rag_quality_score", 0.0)
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)
    route_target = state.get("route_target", "rag")
    
    if quality_score >= 0.7:
        return "pass"
    
    if retry_count < max_retries:
        return f"retry_{route_target}"
    
    return "fallback_web"

def hallucination_decision(state: AgentState) -> str:
    """幻觉检测决策函数"""
    return state["hallucination_action"]
```

---

## 八、迁移策略与实施步骤

### 8.1 分阶段迁移

| 阶段 | 周期 | 任务 | 验收标准 |
|------|------|------|----------|
| **Phase 1：基础设施** | 1 周 | 搭建 LangGraph 环境，定义 AgentState，实现基础节点框架 | LangGraph 能运行空流程 |
| **Phase 2：RAG Tool 封装** | 2 周 | 将现有 RAG 流程封装为 RAG Tool，实现内部重试逻辑 | RAG Tool 能独立运行，返回质量评分 |
| **Phase 3：LangGraph 编排** | 2 周 | 实现 LangGraph 全局编排，接入 RAG Tool | 完整流程能运行，支持 RAG 检索 |
| **Phase 4：Database Tool 封装** | 2 周 | 将 Database Tool 封装为独立工具，接入 LangGraph | 支持 Database 查询和混合模式 |
| **Phase 5：Web Tool 接入** | 1 周 | 实现 Web Tool，接入 LangGraph 作为降级工具 | 支持 Web 搜索兜底 |
| **Phase 6：测试与优化** | 2 周 | 端到端测试、性能优化、可观测性完善 | 所有指标达标，无回归问题 |

### 8.2 兼容性保障

| 策略 | 描述 |
|------|------|
| **双轨运行** | 新旧流程并行运行，通过配置切换 |
| **接口兼容** | 新 Tool 接口兼容旧 Canvas 调用方式 |
| **数据迁移** | 提供数据迁移脚本，确保历史数据可用 |

---

## 九、风险评估与应对

| 风险 | 影响 | 概率 | 应对措施 |
|------|------|------|----------|
| **LangGraph 学习成本** | 开发效率下降 | 中 | 提供 LangGraph 培训，参考官方文档 |
| **性能退化** | 响应延迟增加 | 中 | 性能基准测试，优化节点执行 |
| **状态管理复杂** | 调试困难 | 高 | 实现状态快照，提供调试工具 |
| **工具接口不清晰** | 集成困难 | 中 | 严格接口评审，编写接口文档 |
| **回归问题** | 功能异常 | 高 | 端到端测试覆盖，灰度发布 |

---

## 十、成功标准

| 维度 | 指标 | 目标值 |
|------|------|--------|
| **功能完整性** | 流程覆盖率 | 100%（所有旧流程都能在新架构运行） |
| **性能** | P95 延迟 | ≤ 5s（简单查询）、≤ 15s（复杂查询） |
| **质量** | 检索准确率 | ≥ 90%（与旧架构持平或更优） |
| **可观测性** | 指标覆盖率 | 100%（所有节点都有指标） |
| **可维护性** | 代码行数 | LangGraph 层 ≤ 2000 行，Tool 层 ≤ 5000 行 |

---

## 十一、附录

### 11.1 关键接口定义汇总

| 接口 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `RAGTool.invoke()` | `{query, query_lang, top_k}` | `{docs, quality_score, has_relevant}` | RAG 检索 |
| `DatabaseTool.invoke()` | `{query, query_lang, db_id}` | `{sql, rows, row_count, source}` | 数据库查询 |
| `WebTool.invoke()` | `{query, query_lang}` | `{docs, urls}` | Web 搜索 |

### 11.2 LangGraph 节点清单

| 节点 ID | 节点名称 | 职责 | 控制主体 |
|---------|---------|------|----------|
| `user_question` | 用户提问 | 全局入口 | LangGraph |
| `intent_router` | 意图路由 | 工具选择 | LangGraph |
| `rag_tool` | RAG 工具 | 知识库检索 | RAG Tool |
| `db_tool` | 数据库工具 | 数据库查询 | Database Tool |
| `web_tool` | Web 工具 | Web 搜索 | Web Tool |
| `quality_check` | 质量检查 | 重试决策 | LangGraph |
| `prompt_assembly` | Prompt 组装 | 上下文融合 | LangGraph |
| `llm_generate` | LLM 生成 | 答案生成 | LangGraph |
| `hallucination` | 幻觉检测 | 质量控制 | LangGraph |
| `observability` | 可观测性 | 指标记录 | LangGraph |
| `final_answer` | 返回答案 | 最终响应 | LangGraph |

---

*本文档为 LangGraph + RAGFlow 重构项目的落地设计方案，具体实现细节请参考各模块的详细设计文档。*