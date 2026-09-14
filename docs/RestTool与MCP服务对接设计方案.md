# RestTool 与 MCP 服务对接设计方案

> **版本**: v1.1  
> **日期**: 2026-09-02  
> **状态**: Draft  
> **关联**: MCP 服务（供应链 / 人力 / 财务 ERP 系统调用，权限等处理在 MCP 侧完成）

### 变更记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-09-02 | 初始版本 |
| v1.1 | 2026-09-02 | 新增 5.4 节"接口信息如何提供给 LLM"；明确 Planner 根据 Tool function description 选择接口，RestSkill 为内部配置；RestToolInput 新增 function/function_params 字段；RestTool 新增 _FUNCTION_SKILL_MAP 和 get_function_declarations()；更新 StepArgs、rest_tool_node、ToolDispatcher、场景示例、设计决策 |

---

## 1. 需求概述

### 1.1 背景

需要新增一个 **RestTool**，用于对接后端的 MCP 服务。该 MCP 服务负责处理发往供应链、人力、财务等 ERP 系统的调用，权限校验、数据脱敏、接口聚合等处理均在 MCP 服务中完成。RestTool 作为 Agent 侧的轻量适配层，负责：

- 接收 Agent 的意图路由决策 → 构造 REST 请求 → 调用 MCP 服务 → 解析响应 → 标准化为 Evidence
- 不处理权限逻辑（MCP 服务负责），不处理业务聚合（MCP 服务负责），不直接访问 ERP 数据库

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| 最小侵入 | 遵循现有工具模式（TypedDict I/O + `async invoke()` + 单例工厂），复用 Evidence 标准化、幻觉检测、DAG 调度等现有基础设施 |
| 职责分离 | RestTool 只做协议适配，MCP 服务负责权限 + 业务逻辑 |
| Planner-Tool 解耦 | Planner 通过 function declaration 选择接口，不感知 endpoint 路径、MCP 协议等实现细节；RestSkill 是 RestTool 内部配置 |
| 可扩展 | 新增 ERP 接口只需在 `_FUNCTION_SKILL_MAP` 中加一条配置，函数声明自动注入 LLMRouter/Planner prompt，无需修改路由代码 |
| 可观测 | 完整的 MCP 调用追踪、Evidence 血缘、Token 预算计入 |

---

## 2. 整体架构

### 2.1 RestTool 在系统中的位置

```
用户问题
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│  intent_router (4层路由)                                     │
│    PreFilter → ComplexityGate → RuleRouter → LLMRouter       │
│    ┌──────────────────────────────────────────────────┐     │
│    │ 新增 ERP 意图识别:                                │     │
│    │   RuleRouter: erp_bias 关键词 → confidence=0.45   │     │
│    │   LLMRouter: 语义识别 erp 意图 → target="rest"    │     │
│    │   Planner: 复杂任务中可编排 rest 步骤              │     │
│    └──────────────────────────────────────────────────┘     │
│                         │                                    │
│               target="rest" / target="hybrid"                │
│                         ▼                                    │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ rest_tool_node (新增)                                 │   │
│  │   调用 RestTool.invoke()                              │   │
│  │     → MCP Client → MCP 服务 → ERP 系统                │   │
│  │   产出: rest_result → Evidence 标准化                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                         │                                    │
│                         ▼                                    │
│  evidence_fusion → reflection → quality_check                │
│                         │                                    │
│  prompt_assembly → llm_generate → hallucination              │
│                         │                                    │
│  answer_renderer → observability → END                       │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 RestTool 与 MCP 服务的交互流程

```
RestTool                     MCP Client                  MCP 服务                   ERP 系统
   │                             │                          │                          │
   │  invoke({query,            │                          │                          │
   │    endpoint, method,       │                          │                          │
   │    payload, tenant_id})    │                          │                          │
   │ ─────────────────────────► │                          │                          │
   │                             │  init_session()          │                          │
   │                             │ ───────────────────────► │                          │
   │                             │                          │  权限校验 + 租户隔离       │
   │                             │                          │ ───────────────────────► │
   │                             │                          │ ◄─────────────────────── │
   │                             │  REST Response           │                          │
   │                             │ ◄─────────────────────── │                          │
   │  RestToolOutput             │                          │                          │
   │ ◄───────────────────────── │                          │                          │
   │                             │                          │                          │
   │  → normalize_rest_evidence() → Evidence 标准化         │                          │
   │  → evidence_fusion → hallucination → answer            │                          │
```

---

## 3. RestTool 核心设计

### 3.1 文件结构（新增/修改）

```
agent/langgraph/tools/
├── rest_tool.py              # ★ 新增: RestTool 核心实现
│   ├── RestToolInput(TypedDict)
│   ├── RestToolOutput(TypedDict)
│   ├── RestTool 类
│   └── get_rest_tool() 单例
├── rest_runtime.py           # ★ 新增: RestTool 运行时（MCP 客户端封装）
│   ├── RestRuntime 类
│   ├── MCP Session 管理
│   ├── 超时重试 / 熔断
│   └── get_rest_runtime() 单例
```

### 3.2 RestToolInput / RestToolOutput

遵循现有 [RAGTool](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py) / [DatabaseTool](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/database_tool.py) 的 TypedDict 模式：

```python
# agent/langgraph/tools/rest_tool.py

class RestToolInput(TypedDict, total=False):
    """RestTool 输入参数。

    Attributes:
        query: 用户原始查询（自然语言）
        query_simplified: 简化后的查询
        query_lang: 查询语言
        tenant_id: 租户 ID
        llm_id: LLM 模型 ID
        # MCP 服务地址
        mcp_server_name: MCP 服务名（默认 "erp_mcp_server"）
        # ★ 函数调用方式（推荐）：Planner 选中的函数名及参数
        function: 函数名（如 "query_annual_leave_balance"）
        function_params: 函数参数（如 {"employee_name": "张三", "year": 2024}）
        # 直接调用方式（兼容）：显式指定 REST 端点
        rest_endpoint: REST 端点路径（如 /api/supply_chain/orders）
        rest_method: HTTP 方法（GET/POST/PUT/DELETE）
        rest_payload: 请求体（dict）
        rest_headers: 额外请求头
        # 超时控制
        timeout_ms: 超时时间（默认 30000）
        max_retries: 最大重试次数（默认 2）
        # ERP 领域
        erp_domain: ERP 领域（supply_chain/hr/financial）
        # 额外参数
        extra: 额外参数
    """
    query: str
    query_simplified: str
    query_lang: str
    tenant_id: str
    llm_id: str
    mcp_server_name: str
    function: str
    function_params: dict
    rest_endpoint: str
    rest_method: str
    rest_payload: dict
    rest_headers: dict
    timeout_ms: int
    max_retries: int
    erp_domain: str
    extra: dict


class RestToolOutput(TypedDict, total=False):
    """RestTool 输出结果。

    Attributes:
        success: 是否成功
        error: 错误信息
        error_code: 错误码
        # REST 响应
        rest_status_code: HTTP 状态码
        rest_response: 原始响应（dict）
        rest_response_text: 原始响应文本（用于 Evidence 标准化）
        rest_latency_ms: MCP 调用耗时
        # 标准化结果
        docs: 标准化后的文档列表（兼容 Evidence 标准化）
        quality_score: 质量评分
        result_count: 结果数量
        # 元数据
        rest_endpoint: 实际调用的端点
        rest_method: 实际使用的 HTTP 方法
        erp_domain: ERP 领域
        mcp_server_name: MCP 服务名
        # 证据签名
        rest_query_signature: REST 查询签名（SHA-256）
        rest_evidence_signature: 响应数据签名
        # 熔断/降级
        circuit_breaker_open: 熔断器是否打开
        fallback_used: 是否使用了降级策略
    """
    success: bool
    error: str
    error_code: str
    rest_status_code: int
    rest_response: dict
    rest_response_text: str
    rest_latency_ms: int
    docs: list[dict]
    quality_score: float
    result_count: int
    rest_endpoint: str
    rest_method: str
    erp_domain: str
    mcp_server_name: str
    rest_query_signature: str
    rest_evidence_signature: str
    circuit_breaker_open: bool
    fallback_used: bool
```

### 3.3 RestTool 类 & invoke() 方法

```python
class RestTool:
    """RestTool：通过 MCP 服务调用 ERP 系统的 REST API。

    职责：
    1. 接收 Planner 选中的函数名和参数 → 映射到具体的 REST 端点
    2. 通过 RestRuntime（MCP Client）发送请求到 MCP 服务
    3. 解析 MCP 响应，标准化为 RestToolOutput
    4. 质量评分 + 空结果处理

    不负责：
    - 权限校验（MCP 服务负责）
    - 业务逻辑聚合（MCP 服务负责）
    - 直接访问 ERP 数据库（MCP 服务负责）
    """

    # ★ 函数名 → RestSkill 配置的映射表（对 Planner 透明）
    # Planner 只看到函数声明，不感知 endpoint 路径、MCP 协议等实现细节
    _FUNCTION_SKILL_MAP: dict[str, "RestSkillConfig"] = {
        "query_annual_leave_balance": RestSkillConfig(
            rest_endpoint="/api/hr/leave/balance",
            rest_method="GET",
            erp_domain="hr",
            description="查询员工的年假剩余天数",
            payload_mapper=lambda params: {
                "employee_name": params["employee_name"],
                "year": params.get("year", 2024),
            },
        ),
        "query_attendance": RestSkillConfig(
            rest_endpoint="/api/hr/attendance",
            rest_method="GET",
            erp_domain="hr",
            description="查询员工考勤记录（出勤/迟到/早退/缺勤）",
            payload_mapper=lambda params: {
                "employee_name": params["employee_name"],
                "month": params.get("month", ""),
            },
        ),
        "query_purchase_order": RestSkillConfig(
            rest_endpoint="/api/supply_chain/orders",
            rest_method="GET",
            erp_domain="supply_chain",
            description="查询采购订单详情",
            payload_mapper=lambda params: {"order_id": params["order_id"]},
        ),
        "query_reimbursement_status": RestSkillConfig(
            rest_endpoint="/api/financial/reimbursement",
            rest_method="GET",
            erp_domain="financial",
            description="查询报销单审批状态",
            payload_mapper=lambda params: {"reimbursement_id": params["reimbursement_id"]},
        ),
    }

    @classmethod
    def get_function_declarations(cls) -> list[dict]:
        """暴露给 Planner 和 LLMRouter 的函数声明列表。

        返回格式兼容 OpenAI function calling 规范。
        这些声明在 Planner 和 LLMRouter 的 system prompt 中动态注入。
        """
        return [
            {
                "name": name,
                "description": config.description,
                "parameters": {
                    "type": "object",
                    "properties": config.parameters_schema,
                    "required": config.required_params,
                },
            }
            for name, config in cls._FUNCTION_SKILL_MAP.items()
        ]

    async def invoke(self, input_data: dict) -> RestToolOutput:
        """执行 REST 调用。

        支持两种调用方式：
        1. 函数调用方式（推荐）：传入 function + function_params，
           RestTool 内部查 _FUNCTION_SKILL_MAP 映射到具体 endpoint
        2. 直接调用方式（兼容）：传入 rest_endpoint + rest_method + rest_payload

        流程：
        1. 参数校验与规范化
        2. 如果提供了 function，从 _FUNCTION_SKILL_MAP 解析 endpoint/payload
        3. 调用 RestRuntime.execute()
        4. 解析响应 → 标准化 docs
        5. 质量评分
        6. 返回 RestToolOutput

        Args:
            input_data: RestToolInput 字典

        Returns:
            RestToolOutput: 标准化输出
        """
        # 1. 参数校验
        input_data = self._validate_input(input_data)

        # 2. 解析调用参数：优先使用 function 方式
        function_name = input_data.get("function", "")
        if function_name:
            config = self._FUNCTION_SKILL_MAP.get(function_name)
            if not config:
                return self._empty_result(
                    error=f"Unknown function: {function_name}",
                    error_code="UNKNOWN_FUNCTION",
                    input_data=input_data,
                )
            rest_endpoint = config.rest_endpoint
            rest_method = config.rest_method
            erp_domain = config.erp_domain
            rest_payload = config.payload_mapper(
                input_data.get("function_params", {})
            )
        else:
            # 3. 直接调用方式（兼容）
            rest_endpoint = input_data.get("rest_endpoint", "")
            rest_method = input_data.get("rest_method", "GET")
            rest_payload = input_data.get("rest_payload")
            erp_domain = input_data.get("erp_domain", "")

        # 4. 调用 MCP 服务
        runtime = get_rest_runtime()
        try:
            response = await runtime.execute(
                endpoint=rest_endpoint,
                method=rest_method,
                payload=rest_payload,
                headers=input_data.get("rest_headers"),
                tenant_id=input_data["tenant_id"],
                mcp_server_name=input_data.get("mcp_server_name", "erp_mcp_server"),
                timeout_ms=input_data.get("timeout_ms", 30000),
                max_retries=input_data.get("max_retries", 2),
            )
        except Exception as e:
            logger.error(f"[RestTool] MCP 调用失败: {e}")
            return self._empty_result(
                error=str(e),
                error_code="MCP_CALL_FAILED",
                input_data=input_data,
            )

        # 5. 解析响应 → 标准化 docs
        docs = self._parse_response_to_docs(response)

        # 6. 质量评分
        quality = self._evaluate_quality(docs, response)

        # 7. 构建输出
        return {
            "success": True,
            "rest_status_code": response.get("status_code", 200),
            "rest_response": response.get("data", {}),
            "rest_response_text": response.get("text", ""),
            "rest_latency_ms": response.get("latency_ms", 0),
            "docs": docs,
            "quality_score": quality,
            "result_count": len(docs),
            "rest_endpoint": rest_endpoint,
            "rest_method": rest_method,
            "erp_domain": erp_domain,
            "mcp_server_name": input_data.get("mcp_server_name", "erp_mcp_server"),
            "rest_query_signature": self._compute_query_signature(input_data),
            "rest_evidence_signature": self._compute_evidence_signature(response),
            "circuit_breaker_open": False,
            "fallback_used": False,
        }

    def _empty_result(self, error: str, error_code: str, input_data: dict) -> RestToolOutput:
        """生成空结果（调用失败或空响应时）。"""
        return {
            "success": False,
            "error": error,
            "error_code": error_code,
            "rest_status_code": 0,
            "rest_response": {},
            "rest_response_text": "",
            "rest_latency_ms": 0,
            "docs": [],
            "quality_score": 0.0,
            "result_count": 0,
            "rest_endpoint": input_data.get("rest_endpoint", ""),
            "rest_method": input_data.get("rest_method", "GET"),
            "erp_domain": input_data.get("erp_domain", ""),
            "mcp_server_name": input_data.get("mcp_server_name", "erp_mcp_server"),
            "rest_query_signature": "",
            "rest_evidence_signature": "",
            "circuit_breaker_open": False,
            "fallback_used": False,
        }

    def _parse_response_to_docs(self, response: dict) -> list[dict]:
        """将 MCP 响应解析为标准化 docs 列表。

        策略：
        - 如果响应是列表 → 每项一个 doc
        - 如果响应是 dict → 整体一个 doc
        - 提取 title/content/structured_data 等字段
        """
        ...

    def _evaluate_quality(self, docs: list[dict], response: dict) -> float:
        """评估 REST 调用结果质量。

        质量评分策略：
        - 有有效数据：0.8 ~ 0.95（取决于数据完整度）
        - 空数据：0.1
        - MCP 返回 error：0.0
        """
        ...

    def _compute_query_signature(self, input_data: dict) -> str:
        """计算查询签名（SHA-256），用于跨轮去重和缓存。"""
        ...

    def _compute_evidence_signature(self, response: dict) -> str:
        """计算响应数据签名，用于 Evidence 稳定性判断。"""
        ...


# 单例工厂
_rest_tool_instance: RestTool | None = None

def get_rest_tool() -> RestTool:
    global _rest_tool_instance
    if _rest_tool_instance is None:
        _rest_tool_instance = RestTool()
    return _rest_tool_instance
```

### 3.4 RestRuntime（MCP 客户端封装）

参考 [db_runtime.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/db_runtime.py) 的 MCP session 管理模式：

```python
# agent/langgraph/tools/rest_runtime.py

class RestRuntime:
    """RestTool 运行时：MCP 客户端封装。

    参考 DbRuntime 的 session 管理、超时控制、熔断机制。
    与 DbRuntime 的关键区别：
    - 不执行 SQL 验证（RestTool 不需要）
    - 支持 REST 方法（GET/POST/PUT/DELETE）
    - 支持自定义请求头
    - 支持响应格式解析（JSON/XML/文本）
    """

    async def execute(
        self,
        endpoint: str,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict | None = None,
        tenant_id: str = "",
        mcp_server_name: str = "erp_mcp_server",
        timeout_ms: int = 30000,
        max_retries: int = 2,
    ) -> dict:
        """通过 MCP 服务执行 REST 调用。

        Args:
            endpoint: REST 端点路径
            method: HTTP 方法
            payload: 请求体
            headers: 额外请求头
            tenant_id: 租户 ID
            mcp_server_name: MCP 服务名
            timeout_ms: 超时时间
            max_retries: 最大重试次数

        Returns:
            dict: {status_code, data, text, latency_ms, headers}
        """
        ...
```

---

## 4. 主流程集成

### 4.1 AgentState 新增字段

在 [state.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/state.py) 中新增 RestTool 输出字段：

```python
# AgentState 新增字段（约第 162 行，Web Tool 输出之后）

# Rest Tool 输出
rest_result: dict  # {status_code, response, response_text, docs, endpoint, method, erp_domain}
rest_quality_score: float  # 0.0 ~ 1.0
```

### 4.2 ToolResult 新增字段

```python
class ToolResult(TypedDict, total=False):
    # ... 现有字段 ...
    # ★ 新增 RestTool 结果
    rest_result: dict
    rest_quality_score: float
    rest_endpoint: str
    rest_method: str
    erp_domain: str
```

### 4.3 RouteDecision 扩展

在 [routers/models.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/routers/models.py) 中扩展：

```python
class RouteDecision(BaseModel):
    target: Literal["rag", "database", "web", "hybrid", "chitchat", "rest"]  # ★ 新增 "rest"
    # ... 其余字段不变 ...

class PlanStep(BaseModel):
    tool: Literal["rag", "database", "web", "report", "rest"]  # ★ 新增 "rest"
    # ... 其余字段不变 ...

class StepArgs(BaseModel):
    # ... 现有字段 ...
    rest_endpoint: str = ""       # ★ 新增（兼容直接调用方式）
    rest_method: str = "GET"      # ★ 新增（兼容直接调用方式）
    rest_payload: dict = Field(default_factory=dict)  # ★ 新增（兼容直接调用方式）
    erp_domain: str = ""          # ★ 新增
    function: str = ""            # ★ 新增（推荐）：Planner 选中的函数名
    function_params: dict = Field(default_factory=dict)  # ★ 新增（推荐）：函数参数
```

### 4.4 Graph 新增 rest_tool_node

在 [graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py) 中新增节点：

```python
# 导入
from agent.langgraph.nodes.rest_tool_node import rest_tool_node

# build_agent_graph() 中新增节点
graph.add_node("rest_tool", rest_tool_node)

# 新增条件边: intent_router → rest_tool（当 route_decision.target == "rest" 时）
graph.add_conditional_edges(
    "intent_router",
    route_decision,
    {
        "clarification": "clarification",
        "rag_tool": "rag_tool",
        "db_tool": "db_tool",
        "react_subgraph": "react_subgraph",
        "plan_executor": "plan_executor",
        "rest_tool": "rest_tool",          # ★ 新增
        "prompt_assembly": "prompt_assembly",
    },
)

# rest_tool → evidence_fusion（与其他工具一致的路径）
graph.add_edge("rest_tool", "evidence_fusion")

# quality_check 重试路由也支持 rest_tool
graph.add_conditional_edges(
    "quality_check",
    quality_check_decision,
    {
        "prompt_assembly": "prompt_assembly",
        "rag_tool": "rag_tool",
        "db_tool": "db_tool",
        "web_tool": "web_tool",
        "rest_tool": "rest_tool",          # ★ 新增
        "fallback": "fallback",
    },
)
```

### 4.5 rest_tool_node 实现

```python
# agent/langgraph/nodes/rest_tool_node.py

async def rest_tool_node(state: AgentState) -> dict[str, Any]:
    """RestTool 执行节点。

    流程：
    1. 从 state 提取 REST 调用参数
    2. 调用 get_rest_tool().invoke()
    3. 将结果写入 state
    """
    start_time = time.time()

    # 从 route_decision 或 Planner PlanStep 中提取参数
    route_decision = state.get("route_decision")
    execution_plan = state.get("execution_plan")

    # 优先使用 PlanStep 参数（复杂任务场景）
    if execution_plan and execution_plan.steps:
        rest_steps = [s for s in execution_plan.steps if s.tool == "rest"]
        if rest_steps:
            step = rest_steps[0]
            query = step.args.query or state.get("user_question", "")
            # ★ 优先使用 function 方式（推荐）
            function = step.args.function or ""
            function_params = step.args.function_params or {}
            # 兼容直接调用方式
            rest_endpoint = step.args.rest_endpoint
            rest_method = step.args.rest_method or "GET"
            rest_payload = step.args.rest_payload
            erp_domain = step.args.erp_domain
        else:
            query = state.get("user_question", "")
            function = ""
            function_params = {}
            rest_endpoint = ""
            rest_method = "GET"
            rest_payload = None
            erp_domain = ""
    else:
        # 简单路由：从 route_decision.metadata 中提取
        query = state.get("user_question", "")
        metadata = route_decision.metadata if route_decision else {}
        function = metadata.get("function", "")
        function_params = metadata.get("function_params", {})
        rest_endpoint = metadata.get("rest_endpoint", "")
        rest_method = metadata.get("rest_method", "GET")
        rest_payload = metadata.get("rest_payload")
        erp_domain = metadata.get("erp_domain", "")

    input_data = {
        "query": query,
        "query_simplified": state.get("query_simplified", ""),
        "query_lang": state.get("query_lang", "zh_CN"),
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "mcp_server_name": state.get("mcp_server_name", "erp_mcp_server"),
        "function": function,
        "function_params": function_params,
        "rest_endpoint": rest_endpoint,
        "rest_method": rest_method,
        "rest_payload": rest_payload,
        "erp_domain": erp_domain,
    }

    rest_tool = get_rest_tool()
    result = await rest_tool.invoke(input_data)

    return {
        "rest_result": {
            "status_code": result.get("rest_status_code", 0),
            "response": result.get("rest_response", {}),
            "response_text": result.get("rest_response_text", ""),
            "docs": result.get("docs", []),
            "endpoint": result.get("rest_endpoint", ""),
            "method": result.get("rest_method", "GET"),
            "erp_domain": result.get("erp_domain", ""),
        },
        "rest_quality_score": result.get("quality_score", 0.0),
        "agent_iteration_count": state.get("agent_iteration_count", 1) + 1,
        "node_timings": {"rest_tool": int((time.time() - start_time) * 1000)},
    }
```

### 4.6 ToolDispatcher 扩展

在 [tool_dispatcher.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/executor/tool_dispatcher.py) 的 `dispatch()` 方法中新增：

```python
async def dispatch(self, step: PlanStep, state: "AgentState", previous_results: dict) -> "ToolResult":
    # ...
    if step.tool == "rag":
        result = await self._execute_rag(step, state)
    elif step.tool == "database":
        result = await self._execute_database(step, state)
    elif step.tool == "web":
        result = await self._execute_web(step, state)
    elif step.tool == "report":
        result = await self._execute_report(step, state, previous_results)
    elif step.tool == "rest":  # ★ 新增
        result = await self._execute_rest(step, state)
    # ...

async def _execute_rest(self, step: PlanStep, state: "AgentState") -> dict:
    """执行 REST 调用（通过 MCP 服务）。

    优先使用 step.args.function / function_params（函数调用方式），
    兼容 step.args.rest_endpoint / rest_method / rest_payload（直接调用方式）。
    支持同一计划中调用多个不同 ERP 领域。
    """
    from agent.langgraph.tools.rest_tool import get_rest_tool

    rest_tool = get_rest_tool()

    query = step.args.query or state.get("user_question", "")

    input_data = {
        "query": query,
        "query_simplified": step.args.query_simplified or state.get("query_simplified", ""),
        "query_lang": state.get("query_lang", "zh_CN"),
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "mcp_server_name": step.args.mcp_server_name or "erp_mcp_server",
        "function": step.args.function or "",
        "function_params": step.args.function_params or {},
        "rest_endpoint": step.args.rest_endpoint,
        "rest_method": step.args.rest_method or "GET",
        "rest_payload": step.args.rest_payload,
        "erp_domain": step.args.erp_domain,
    }

    if step.args.extra:
        input_data.update(step.args.extra)

    result = await rest_tool.invoke(input_data)

    return {
        "rest_result": {
            "status_code": result.get("rest_status_code", 0),
            "response": result.get("rest_response", {}),
            "response_text": result.get("rest_response_text", ""),
            "docs": result.get("docs", []),
            "endpoint": result.get("rest_endpoint", ""),
            "method": result.get("rest_method", "GET"),
            "erp_domain": result.get("erp_domain", ""),
        },
        "rest_quality_score": result.get("quality_score", 0.0),
        "rest_endpoint": result.get("rest_endpoint", ""),
        "rest_method": result.get("rest_method", "GET"),
        "erp_domain": result.get("erp_domain", ""),
    }
```

### 4.7 ResultAggregator 扩展

在 [result_aggregator.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/executor/result_aggregator.py) 中新增 REST 结果聚合：

```python
@staticmethod
def aggregate(tool_results: list[dict], max_keep: int = 20) -> dict:
    all_rag_docs = []
    all_db_rows = []
    all_web_docs = []
    all_rest_docs = []  # ★ 新增
    all_report_artifacts = []
    # ...

    for result in tool_results:
        if not result.get("success", False):
            continue

        # ... 现有聚合逻辑 ...

        # ★ 聚合 REST 结果
        rest_result = result.get("rest_result", {})
        if rest_result and rest_result.get("docs"):
            all_rest_docs.extend(rest_result["docs"])

    # ... 去重截断逻辑 ...

    return {
        # ... 现有字段 ...
        "rest_docs": all_rest_docs,  # ★ 新增
    }
```

---

## 5. 意图路由集成

### 5.1 RuleRouter 新增 ERP 关键词

在 [rule_router.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/routers/rule_router.py) 中新增 ERP 领域关键词匹配：

```python
# 在 keywords 配置中新增 erp_bias 关键词列表
# 这些关键词命中后，confidence=0.45，封顶 0.55，强制走 LLM 确认

ERP_BIAS_KEYWORDS = [
    # 供应链
    "供应商", "采购", "订单", "库存", "入库", "出库", "物流", "供应链",
    "供应商管理", "采购订单", "库存查询", "入库单", "出库单",
    # 人力资源
    "员工", "薪资", "考勤", "请假", "入职", "离职", "部门", "组织架构",
    "加班", "调休", "年假", "社保", "公积金", "工资条", "绩效",
    # 财务
    "报销", "发票", "付款", "收款", "账单", "预算", "成本", "利润",
    "应收", "应付", "凭证", "记账", "对账", "税务", "财务报表",
    # 通用 ERP
    "审批", "流程", "工单", "申请单", "审批单",
]
```

在 `_match_xxx_bias()` 方法中新增 `_match_erp_bias()`：

```python
def _match_erp_bias(self, query: str, keywords: dict) -> tuple[float, list[str]]:
    """匹配 ERP 偏向词（Tier 2 弱关键词，置信度封顶 0.55）。"""
    erp_bias_keywords = keywords.get("erp_bias", [])
    matched = [kw for kw in erp_bias_keywords if kw in query]
    if not matched:
        return 0.0, []
    bonus = min((len(matched) - 1) * self.WEAK_BONUS_PER_KEYWORD, self.WEAK_BONUS_CAP)
    score = self.WEAK_BASE_SCORE + bonus
    return score, matched
```

### 5.2 LLMRouter 新增语义识别

在 LLMRouter 的 prompt 中新增 ERP 意图识别指令，并动态注入 RestTool 的函数声明列表：

```python
# LLMRouter 构造 prompt 时：
from agent.langgraph.tools.rest_tool import RestTool

def _build_rest_tool_functions_summary() -> str:
    """从 RestTool 获取函数声明，注入路由 prompt。"""
    declarations = RestTool.get_function_declarations()
    if not declarations:
        return ""
    lines = ["## rest 工具支持的函数："]
    for func in declarations:
        lines.append(f"  - {func['name']}: {func['description']}")
        lines.append(f"    参数: {func['parameters']}")
    return "\n".join(lines)
```

LLMRouter 的完整 prompt 示例：

```
你是一个意图路由分类器。根据用户问题，判断应路由到哪个工具：

- rag: 知识库检索（文档、手册、制度等）
- database: 数据库查询（结构化数据、统计报表）
- web: 互联网搜索（实时信息、外部新闻）
- rest: ERP 业务系统查询（供应链、人力、财务等）
- hybrid: 需要多个工具组合
- chitchat: 闲聊

## rest 工具支持的函数：
  - query_annual_leave_balance: 查询员工的年假剩余天数
    参数: {"employee_name": "string", "year": "integer"}
  - query_attendance: 查询员工考勤记录（出勤/迟到/早退/缺勤）
    参数: {"employee_name": "string", "month": "string"}
  - query_purchase_order: 查询采购订单详情
    参数: {"order_id": "string"}
  - query_reimbursement_status: 查询报销单审批状态
    参数: {"reimbursement_id": "string"}

当用户问题涉及 ERP 查询时，路由到 rest，并在 metadata 中指定 function 和 function_params。
```

### 5.3 路由决策流程

```
用户问题："查询张三的2024年年假剩余天数"
     │
     ▼
PreFilter: 非敏感关键词 → 通过
     │
     ▼
ComplexityGate: simple → 通过
     │
     ▼
RuleRouter:
  - erp_bias 匹配: ["员工", "年假", "请假"] → score=0.45+0.10=0.55
  - 封顶 0.55 < 0.7 阈值 → 不短路，进入 LLM
     │
     ▼
LLMRouter:
  - 语义分析: "员工" + "年假" + "查询" → target="rest", erp_domain="hr"
  - confidence=0.85
     │
     ▼
RouteDecision: target="rest", confidence=0.85, source="llm"
     │
     ▼
rest_tool_node → RestTool.invoke() → MCP 服务 → HR 系统
     │
     ▼
evidence_fusion → ... → hallucination → answer
```

### 5.4 接口信息如何提供给 LLM（两级路由详解）

#### 核心设计原则

**Planner 根据 Tool 暴露的 function description 选择调用哪个接口，而非根据 Skill。**
RestSkill 是 RestTool 内部的配置层，对 Planner 完全透明。

```
┌─────────────────────────────────────────────────────────────────┐
│ 职责边界                                                          │
│                                                                   │
│  Planner 负责:  "选哪个函数 + 填什么参数"                          │
│    ↕ 通过 function declaration 解耦                               │
│  RestTool 负责: "把函数调用翻译成 REST 请求"                       │
│    ↕ 通过 _FUNCTION_SKILL_MAP 映射                                │
│  RestSkill 负责: "定义 endpoint / method / payload 映射规则"      │
│                                                                   │
│  Planner 永远不需要知道 endpoint 路径、MCP 协议等实现细节           │
└─────────────────────────────────────────────────────────────────┘
```

#### 第一级：意图路由（决定走哪个工具）

RuleRouter 命中 ERP 关键词 → LLMRouter 确认意图为 `target="rest"`。此时 LLMRouter 的 prompt 中已包含 RestTool 的函数声明摘要，LLM 可以同时选出 `function` 名称。

#### 第二级：函数选择（决定调用哪个接口）

**LLMRouter 和 Planner 的 system prompt 中动态注入 RestTool 的函数声明列表**：

```python
# 在 LLMRouter / Planner 构造 prompt 时：

def _build_rest_tool_functions_summary() -> str:
    """从 RestTool 获取函数声明，注入路由 prompt。"""
    from agent.langgraph.tools.rest_tool import RestTool

    declarations = RestTool.get_function_declarations()
    if not declarations:
        return ""

    lines = ["## rest 工具支持的函数："]
    for func in declarations:
        lines.append(f"  - {func['name']}: {func['description']}")
        lines.append(f"    参数: {func['parameters']}")
    return "\n".join(lines)
```

LLMRouter 的 prompt 示例：

```
你是一个意图路由分类器。根据用户问题，判断应路由到哪个工具：

- rag: 知识库检索（文档、手册、制度等）
- database: 数据库查询（结构化数据、统计报表）
- web: 互联网搜索（实时信息、外部新闻）
- rest: ERP 业务系统查询
- hybrid: 需要多个工具组合
- chitchat: 闲聊

## rest 工具支持的函数：
  - query_annual_leave_balance: 查询员工的年假剩余天数
    参数: {"employee_name": "string", "year": "integer"}
  - query_attendance: 查询员工考勤记录（出勤/迟到/早退/缺勤）
    参数: {"employee_name": "string", "month": "string"}
  - query_purchase_order: 查询采购订单详情
    参数: {"order_id": "string"}
  - query_reimbursement_status: 查询报销单审批状态
    参数: {"reimbursement_id": "string"}

当用户问题涉及 ERP 查询时，路由到 rest，并在 metadata 中指定 function 和 function_params。
```

#### 函数声明来源

函数声明由 RestTool 的 `_FUNCTION_SKILL_MAP` 自动生成，**新增一个 ERP 接口只需在映射表中加一条配置**：

```python
# 新增一个接口只需在 RestTool._FUNCTION_SKILL_MAP 中加一条：
_FUNCTION_SKILL_MAP = {
    # ... 已有配置 ...
    "query_inventory": RestSkillConfig(  # ← 只需加这一条
        rest_endpoint="/api/supply_chain/inventory",
        rest_method="GET",
        erp_domain="supply_chain",
        description="查询库存数量",
        payload_mapper=lambda params: {"product_id": params["product_id"]},
    ),
}
```

LLMRouter 和 Planner 的 prompt 中自动出现 `query_inventory` 函数声明，无需修改任何路由代码。

#### 完整数据流

```
系统启动时:
  RestTool._FUNCTION_SKILL_MAP
    → RestTool.get_function_declarations()
    → 注入 LLMRouter / Planner 的 system prompt

运行时:
  用户: "张三的年假还剩多少天"
    │
    ▼
  LLMRouter（prompt 中已有函数声明）:
    → target="rest"
    → metadata={
        "function": "query_annual_leave_balance",  ← 从函数声明中选出
        "function_params": {"employee_name": "张三", "year": 2024}  ← 从用户问题中提取
      }
    │
    ▼
  rest_tool_node → RestTool.invoke({
      function: "query_annual_leave_balance",
      function_params: {"employee_name": "张三", "year": 2024},
    })
    │
    ▼
  RestTool 内部:
    _FUNCTION_SKILL_MAP["query_annual_leave_balance"]
      → endpoint="/api/hr/leave/balance", method="GET"
      → payload_mapper → {"employee_name": "张三", "year": 2024}
      → MCP 服务 → HR 系统
    │
    ▼
  返回 {balance: 5}
```

#### 复杂任务场景（Planner 编排）

当用户问题复杂度高时，Planner 生成 ExecutionPlan，同样通过函数声明选择接口：

```python
ExecutionPlan(
    steps=[
        PlanStep(
            step_id="step1",
            tool="rest",
            function="query_purchase_costs",  # ← Planner 从函数声明中选出
            args=StepArgs(
                query="Q1采购成本",
                function="query_purchase_costs",
                function_params={"quarter": "Q1", "year": 2024},
            ),
        ),
        PlanStep(
            step_id="step2",
            tool="rest",
            function="query_purchase_costs",
            args=StepArgs(
                query="Q2采购成本",
                function="query_purchase_costs",
                function_params={"quarter": "Q2", "year": 2024},
            ),
        ),
    ]
)
```

---

## 6. RestSkill 配置（RestTool 内部映射层）

> **RestSkill 是 RestTool 内部的配置层，不暴露给 Planner。** Planner 只看到 function declaration，不感知 RestSkill 的存在。

### 6.1 RestSkillConfig 定义

```python
# agent/langgraph/tools/rest_tool.py

@dataclass
class RestSkillConfig:
    """RestTool 内部配置：映射 function name → REST 端点。

    对 Planner 完全透明。Planner 只看到 function declaration，
    不知道 endpoint 路径、MCP 协议等实现细节。

    Attributes:
        rest_endpoint: REST 端点路径
        rest_method: HTTP 方法
        erp_domain: ERP 领域
        description: 功能描述（用于生成 function declaration）
        parameters_schema: 参数 JSON Schema（用于生成 function declaration）
        required_params: 必填参数列表
        payload_mapper: 将 function_params 映射为 REST payload 的函数
        fallback_strategy: 降级策略
    """
    rest_endpoint: str
    rest_method: str = "GET"
    erp_domain: str = ""
    description: str = ""
    parameters_schema: dict = field(default_factory=dict)
    required_params: list[str] = field(default_factory=list)
    payload_mapper: Callable = lambda params: params
    fallback_strategy: str = "empty"  # empty / cache / default_value
```

### 6.2 配置示例

```python
# RestTool._FUNCTION_SKILL_MAP 中已有的配置示例：

# HR - 年假查询
"query_annual_leave_balance": RestSkillConfig(
    rest_endpoint="/api/hr/leave/balance",
    rest_method="GET",
    erp_domain="hr",
    description="查询员工的年假剩余天数",
    parameters_schema={
        "employee_name": {"type": "string", "description": "员工姓名"},
        "year": {"type": "integer", "description": "查询年份，默认当年"},
    },
    required_params=["employee_name"],
    payload_mapper=lambda params: {
        "employee_name": params["employee_name"],
        "year": params.get("year", 2024),
    },
),

# 供应链 - 采购订单查询
"query_purchase_order": RestSkillConfig(
    rest_endpoint="/api/supply_chain/orders",
    rest_method="GET",
    erp_domain="supply_chain",
    description="查询采购订单详情",
    parameters_schema={
        "order_id": {"type": "string", "description": "订单编号"},
    },
    required_params=["order_id"],
    payload_mapper=lambda params: {"order_id": params["order_id"]},
),

# 财务 - 报销单查询
"query_reimbursement_status": RestSkillConfig(
    rest_endpoint="/api/financial/reimbursement",
    rest_method="GET",
    erp_domain="financial",
    description="查询报销单审批状态",
    parameters_schema={
        "reimbursement_id": {"type": "string", "description": "报销单编号"},
    },
    required_params=["reimbursement_id"],
    payload_mapper=lambda params: {"reimbursement_id": params["reimbursement_id"]},
),
```

### 6.3 新增接口流程

新增一个 ERP 接口只需三步，无需修改任何路由或 Planner 代码：

```python
# 1. 在 RestTool._FUNCTION_SKILL_MAP 中加一条配置
_FUNCTION_SKILL_MAP["query_inventory"] = RestSkillConfig(
    rest_endpoint="/api/supply_chain/inventory",
    rest_method="GET",
    erp_domain="supply_chain",
    description="查询库存数量",
    parameters_schema={
        "product_id": {"type": "string", "description": "产品编号"},
    },
    required_params=["product_id"],
    payload_mapper=lambda params: {"product_id": params["product_id"]},
)

# 2. 重启服务（或热加载配置）
# 3. LLMRouter / Planner 的 prompt 中自动出现 query_inventory 函数声明
```

### 6.4 与 Skill 系统的关系（可选扩展）

如果未来需要更复杂的 Skill 能力（如 HardFilterChain 关键词匹配、HybridRecaller 语义召回），可以在 P2 Skill 系统中注册 RestSkill 作为 `SkillBase` 的子类，但当前设计优先保持简单：**RestSkillConfig 是 RestTool 的内部 dataclass，不依赖 Skill 系统**。

---

## 7. Evidence 标准化集成

### 7.1 新增 source_type "rest"

在 [evidence/models.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/evidence/models.py) 中扩展：

```python
class Evidence(TypedDict, total=False):
    source_type: Literal["rag", "db", "web", "report", "rest"]  # ★ 新增 "rest"
    # ... 其余字段不变 ...

# 工具到 source_type 映射
TOOL_SOURCE_TYPE_MAP = {
    "rag_search": "rag",
    "rag": "rag",
    "db_query": "db",
    "database": "db",
    "web_search": "web",
    "web": "web",
    "report": "report",
    "rest_call": "rest",    # ★ 新增
    "rest": "rest",         # ★ 新增
}

# 默认权威性
DEFAULT_AUTHORITY_SCORE = {
    "db": 0.95,
    "rag": 0.80,
    "report": 0.85,
    "web": 0.50,
    "rest": 0.90,  # ★ 新增: ERP 系统数据权威性仅次于 DB
}
```

### 7.2 normalize_rest_evidence()

```python
def normalize_rest_evidence(
    rest_result: dict,
    tenant_id: str = "",
    query: str = "",
) -> list[Evidence]:
    """将 REST 调用结果标准化为 Evidence 列表。

    Args:
        rest_result: RestTool 输出 {status_code, response, response_text, docs, endpoint, method, erp_domain}
        tenant_id: 租户 ID
        query: 用户查询

    Returns:
        list[Evidence]: 标准化后的 Evidence 列表
    """
    evidences: list[Evidence] = []
    docs = rest_result.get("docs", []) or []
    endpoint = rest_result.get("endpoint", "")
    erp_domain = rest_result.get("erp_domain", "")

    for doc in docs:
        content_raw = doc.get("content") or doc.get("text") or ""
        content, truncated = _truncate_content(content_raw, max_chars=2000)
        content = _scrub_sensitive(content)

        source_uri = f"rest://{erp_domain}{endpoint}/{doc.get('id', '')}" if erp_domain else f"rest://{endpoint}/{doc.get('id', '')}"

        ev: Evidence = {
            "evidence_id": _make_evidence_id(source_uri, content_raw),
            "source_type": "rest",
            "title": doc.get("title", "") or f"ERP 查询结果 ({erp_domain})",
            "content": content,
            "structured_data": doc.get("structured_data", {}),
            "source_uri": source_uri,
            "tenant_id": tenant_id,
            "confidence": max(0.0, min(1.0, doc.get("score", 0.9))),
            "relevance_score": max(0.0, min(1.0, doc.get("score", 0.9))),
            "authority_score": DEFAULT_AUTHORITY_SCORE["rest"],
            "freshness_score": 1.0,  # ERP 系统数据实时
            "created_at": _now_iso(),
            "metadata": {
                "endpoint": endpoint,
                "method": rest_result.get("method", "GET"),
                "erp_domain": erp_domain,
                "status_code": rest_result.get("status_code", 0),
                "truncated": truncated,
                "query": query,
            },
        }
        evidences.append(ev)
    return evidences
```

### 7.3 normalize_tool_result() 扩展

```python
def normalize_tool_result(tool_name: str, result: dict, tenant_id: str = "", query: str = "") -> list[Evidence]:
    source_type = TOOL_SOURCE_TYPE_MAP.get(tool_name, "")

    # ... 现有分支 ...

    if source_type == "rest":
        return normalize_rest_evidence(result, tenant_id=tenant_id, query=query)

    return []
```

### 7.4 evidence_fusion_node 扩展

在 [evidence_fusion.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/evidence_fusion.py) 的 `_normalize_current_tool_results()` 中新增：

```python
def _normalize_current_tool_results(state, tenant_id, query):
    evidences = []

    # ... 现有 RAG/DB/Web 标准化 ...

    # ★ Rest
    rest_result = state.get("rest_result") or {}
    if rest_result and rest_result.get("docs"):
        evidences.extend(
            normalize_tool_result(
                tool_name="rest_call",
                result=rest_result,
                tenant_id=tenant_id,
                query=query,
            )
        )

    return evidences
```

在 `_detect_active_tools()` 中新增：

```python
def _detect_active_tools(state: dict) -> list[str]:
    active = []
    # ... 现有检测 ...
    if state.get("rest_result", {}).get("docs"):
        active.append("rest")
    return active
```

在 `DEFAULT_SOURCE_QUOTA` 中新增：

```python
DEFAULT_SOURCE_QUOTA = {
    "rag": 10,
    "db": 10,
    "web": 5,
    "report": 5,
    "rest": 10,  # ★ 新增
}
```

---

## 8. 幻觉检测集成

RestTool 产出的 Evidence 通过 `evidence_fusion` 进入不可变 `EvidenceSnapshot` 后，**自动进入现有的幻觉检测管道**，无需额外修改：

```
RestTool → normalize_rest_evidence() → Evidence
  → evidence_fusion → EvidenceSnapshot
  → prompt_assembly → LLM 生成答案
  → hallucination → PairVerifier (Rule + NLI + LLM 三层)
    → VerdictMatrix → PolicyEngine (pass/filter/regenerate)
```

RestTool 的 Evidence 具有 `authority_score=0.90`（仅次于 DB 的 0.95），在幻觉检测中享有**高权威性**：

- **Rule 层**：REST 返回的具体数值（如"年假余额 5 天"）与 LLM 生成的声明进行精确匹配
- **NLI 层**：REST 返回的结构化数据与声明进行语义蕴含判断
- **LLM 层**：REST 证据作为高置信度参考，优先采纳

---

## 9. 安全与权限

### 9.1 权限模型

```
用户 → Agent → RestTool → MCP 服务 → ERP 系统
                                  │
                          ┌───────┴───────┐
                          │ 权限校验       │
                          │ 租户隔离       │
                          │ 数据脱敏       │
                          │ 接口聚合       │
                          └───────────────┘
```

**RestTool 不处理权限逻辑**，所有权限校验由 MCP 服务完成。RestTool 仅传递：
- `tenant_id`：租户标识
- `user_id`（可选）：用户标识（从 state 中透传）
- `rest_endpoint` + `rest_payload`：业务请求参数

### 9.2 安全措施

| 层级 | 措施 |
|------|------|
| 传输层 | MCP 服务内部通信（非公网暴露） |
| 认证层 | MCP 服务校验 tenant_id + token |
| 授权层 | MCP 服务按用户角色限制 ERP 数据访问范围 |
| 数据脱敏 | MCP 服务在返回前对敏感字段脱敏 |
| 审计 | RestTool 记录每次调用的 tenant_id / endpoint / latency_ms |

---

## 10. 场景示例

### 场景 1: 简单查询（HR 年假）

```
用户: "查一下张三的2024年年假还剩多少天"

1. intent_router:
   - RuleRouter: erp_bias 命中 ["员工", "年假"] → confidence=0.50
   - LLMRouter（prompt 中已有函数声明）:
     - 识别为 HR 查询 → target="rest"
     - 从函数声明中选出 function="query_annual_leave_balance"
     - 从用户问题中提取 function_params={"employee_name": "张三", "year": 2024}
   - RouteDecision: target="rest", confidence=0.85, metadata={
       "function": "query_annual_leave_balance",
       "function_params": {"employee_name": "张三", "year": 2024},
       "erp_domain": "hr"
     }

2. rest_tool_node:
   - RestTool.invoke({
       query: "查一下张三的2024年年假还剩多少天",
       function: "query_annual_leave_balance",
       function_params: {"employee_name": "张三", "year": 2024},
     })
   - RestTool 内部: _FUNCTION_SKILL_MAP["query_annual_leave_balance"]
       → endpoint="/api/hr/leave/balance", method="GET"
       → payload={"employee_name": "张三", "year": 2024}
   - MCP 服务 → HR 系统 → 返回 {"employee_name": "张三", "year": 2024, "balance": 5}
   - RestToolOutput: docs=[{title: "张三 年假余额", content: "剩余年假: 5天", ...}]

3. evidence_fusion: 标准化为 source_type="rest" 的 Evidence

4. hallucination: PairVerifier 验证 LLM 生成的"张三2024年年假还剩5天"
   - Rule层: 精确匹配 "5" → supported
   - NLI层: 语义蕴含 → supported
   - PolicyEngine: pass

5. answer_renderer: 输出 "张三2024年年假还剩5天"
```

### 场景 2: 复杂任务（供应链 + 财务）

```
用户: "对比2024年Q1和Q2的采购成本，并分析成本上升的原因"

1. intent_router:
   - LLMRouter: 复杂任务 → target="hybrid", complexity="complex"
   - Planner（prompt 中已有函数声明）: 生成 ExecutionPlan
     - Step 1: rest, function="query_purchase_costs", params={quarter: "Q1", year: 2024}
     - Step 2: rest, function="query_purchase_costs", params={quarter: "Q2", year: 2024}
     - Step 3: rag → 检索"采购成本控制"知识库文档

2. plan_executor → ToolDispatcher:
   - Step 1: _execute_rest() → Q1 采购成本数据
   - Step 2: _execute_rest() → Q2 采购成本数据
   - Step 3: _execute_rag() → 成本控制最佳实践

3. ResultAggregator: 合并 REST + RAG 结果

4. evidence_fusion: 多源 Evidence 融合
   - REST Evidence (authority=0.90): Q1 成本=120万, Q2 成本=150万
   - RAG Evidence (authority=0.80): 原材料价格上涨、物流成本增加

5. hallucination: 验证 LLM 生成的对比分析
   - 数值声明（120万/150万）: REST Evidence 直接支持 → pass
   - 原因分析: RAG Evidence 支持 → pass

6. answer: "2024年Q1采购成本为120万，Q2为150万，增长25%。主要原因是..."
```

---

## 11. 实施路线

### Phase 1（核心功能，2-3 天）

| 任务 | 文件 | 说明 |
|------|------|------|
| RestTool 核心实现 | `tools/rest_tool.py` | RestToolInput / RestToolOutput / RestTool 类 / invoke() |
| RestRuntime 实现 | `tools/rest_runtime.py` | MCP 客户端封装、session 管理、超时重试、熔断 |
| rest_tool_node | `nodes/rest_tool_node.py` | LangGraph 节点实现 |
| AgentState 扩展 | `state.py` | 新增 rest_result / rest_quality_score |
| Graph 集成 | `graph.py` | 新增 rest_tool 节点 + 边 |
| 单元测试 | `test/agent/langgraph/tools/test_rest_tool.py` | RestTool 单元测试 |

### Phase 2（路由与调度，1-2 天）

| 任务 | 文件 | 说明 |
|------|------|------|
| RouteDecision 扩展 | `routers/models.py` | 新增 "rest" target |
| RuleRouter 扩展 | `routers/rule_router.py` | 新增 ERP 关键词匹配 |
| LLMRouter 扩展 | `routers/llm_router.py` | 新增 ERP 意图语义识别 |
| ToolDispatcher 扩展 | `executor/tool_dispatcher.py` | 新增 _execute_rest() |
| ResultAggregator 扩展 | `executor/result_aggregator.py` | 新增 REST 结果聚合 |

### Phase 3（Evidence 与幻觉检测，1 天）

| 任务 | 文件 | 说明 |
|------|------|------|
| Evidence 扩展 | `evidence/models.py` | 新增 "rest" source_type + normalize_rest_evidence() |
| Evidence Fusion 扩展 | `nodes/evidence_fusion.py` | REST 证据进入融合管道 |
| 幻觉检测验证 | `nodes/hallucination.py` | 验证 REST Evidence 在 PairVerifier 中的表现 |

### Phase 4（RestSkill 配置管理，1 天）

| 任务 | 文件 | 说明 |
|------|------|------|
| RestSkillConfig dataclass | `tools/rest_tool.py` | 函数名 → endpoint 映射配置 |
| 配置热加载 | `tools/rest_tool.py` | 支持运行时动态添加/更新 _FUNCTION_SKILL_MAP |
| 配置验证 | `tools/rest_tool.py` | 启动时校验所有 RestSkillConfig 的 endpoint 格式和必填字段 |

### Phase 5（生产就绪，1 天）

| 任务 | 说明 |
|------|------|
| 集成测试 | 端到端 REST 调用 + Evidence 标准化 + 幻觉检测 |
| 可观测性 | 新增 MCP 调用耗时 / 成功率 / 熔断状态指标 |
| 配置文档 | RestTool 配置参数说明 |
| 降级策略 | MCP 不可用时的 fallback 行为 |

---

## 12. 附录

### 12.1 与 DatabaseTool 的区别

| 维度 | DatabaseTool | RestTool |
|------|-------------|----------|
| 数据源 | 数据库直连（通过 MCP） | ERP 系统 REST API（通过 MCP） |
| 查询语言 | SQL | REST 端点 + HTTP 方法 |
| 权限 | MCP 服务 + DB 权限 | MCP 服务（一体处理） |
| 结果格式 | 行列表（rows） | 结构化 JSON（docs） |
| 权威性 | 0.95 | 0.90 |
| 使用场景 | 结构化数据查询、统计报表 | 业务系统操作、ERP 数据查询 |

### 12.2 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 权限处理位置 | MCP 服务 | 避免 Agent 侧维护复杂的 ERP 权限模型 |
| 数据聚合位置 | MCP 服务 | 跨系统聚合逻辑复杂，不应由 Agent 承担 |
| RestTool 职责 | 纯协议适配 | 遵循单一职责原则，与 DatabaseTool 一致 |
| 路由方式 | RuleRouter + LLMRouter | 与现有架构一致，RuleRouter 做低成本关键词过滤，LLMRouter 做语义确认 |
| Evidence 权威性 | 0.90 | 介于 DB(0.95) 和 RAG(0.80) 之间，ERP 数据权威但非原始数据库 |
| **接口信息暴露方式** | **Function Declaration（类似 OpenAI function calling）** | Planner 只看到函数声明（name + description + parameters），不感知 endpoint 路径、MCP 协议等实现细节；与现有 RAG/DB/Web tool 的集成方式一致 |
| **接口选择机制** | **Planner 根据 Tool 的 function description 选择** | Planner 的职责是"选哪个函数 + 填什么参数"，RestTool 的职责是"把函数调用翻译成 REST 请求"。RestSkill 是内部配置，对 Planner 透明 |
| **新增接口成本** | **在 _FUNCTION_SKILL_MAP 中加一条配置** | 无需修改 Planner、LLMRouter、ToolDispatcher 或任何路由代码，函数声明自动注入 prompt |