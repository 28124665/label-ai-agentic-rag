# RestTool Skill 前置查询设计方案

> **版本**: v1.0  
> **日期**: 2026-09-02  
> **状态**: Draft  
> **关联**: [RestTool 与 MCP 服务对接设计方案](./RestTool与MCP服务对接设计方案.md)  

---

## 1. 背景与问题

### 1.1 场景描述

RestTool 调用 ERP 接口时，某些接口需要的参数无法直接从用户问题中提取，而是需要**先查数据库拿到中间参数**，再用这些参数构造 REST 请求。

**典型场景**：

| 用户问题 | 用户能直接提供的参数 | 缺失的中间参数 | 需要先查什么 |
|---------|-------------------|---------------|-------------|
| "张三的采购订单有哪些" | employee_name="张三" | employee_id | 从员工表查 employee_id |
| "我的报销单审批到哪了" | (无，通过上下文获取用户名) | employee_id, dept_id | 从员工表查 ID 和部门 |
| "查一下A项目的预算执行情况" | project_name="A项目" | project_code, cost_center | 从项目表查项目编码和成本中心 |

### 1.2 现有架构的能力边界

当前架构中，Planner 可以通过 DAG 拆步来实现跨工具串联：

```python
# Planner 拆成两步
Step 1: database → SELECT employee_id FROM employee WHERE name='张三'
Step 2: rest → query_purchase_order(employee_id={{step_1.employee_id}})
```

但这种方式存在两个问题：

1. **Planner 负担重**：Planner 必须知道每个 Rest Skill 的前置依赖是什么，对 LLM 推理能力要求高，容易出错
2. **参数引用语法复杂**：`{{step_1.employee_id}}` 这种跨步骤参数引用需要额外的解析和传递机制

### 1.3 设计目标

让 RestSkill 配置**自包含前置查询逻辑**，对 Planner 完全透明：

```
方案一（DAG 编排）：                 方案二（Skill 前置查询）：
Planner 需要知道 DB 依赖             Planner 完全不知道 DB 依赖
  │                                    │
  ├─ Step1: DB 查 employee_id          │
  └─ Step2: REST 用 employee_id        └─ function="query_my_orders"
                                          params={"employee_name":"张三"}
                                          │
                                          RestTool 内部:
                                          ├─ 自动查 DB 拿到 employee_id
                                          └─ 用 employee_id 调 REST
```

---

## 2. 核心设计

### 2.1 RestSkillConfig 新增字段

```python
@dataclass
class RestSkillConfig:
    """RestTool 内部配置：映射 function name → REST 端点。

    新增字段用于支持前置查询（pre-query）：
    - pre_query: 前置 DB 查询配置，用于获取 REST 调用所需的中间参数
    - enhanced_payload_mapper: 增强版 payload_mapper，接收 DB 结果 + 用户参数
    """

    # ======== 原有字段 ========
    rest_endpoint: str
    rest_method: str = "GET"
    erp_domain: str = ""
    description: str = ""
    parameters_schema: dict = field(default_factory=dict)
    required_params: list[str] = field(default_factory=list)
    payload_mapper: Callable = lambda params: params
    fallback_strategy: str = "empty"  # empty / cache / default_value

    # ======== 新增字段：前置查询 ========
    pre_query: dict | None = None
    """
    前置 DB 查询配置。为 None 时表示无前置查询（与现有行为一致）。

    结构:
    {
        "sql": "SELECT employee_id FROM dim_employee WHERE employee_name = :employee_name",
        "params_mapping": {
            "employee_name": "employee_name"  # SQL参数名 → function_params 中的 key
        },
        "output_fields": ["employee_id"],     # 需要从 DB 结果中提取的字段
        "db_tool_name": "database",           # 使用的 DB 工具名（默认 "database"）
        "allow_empty": False,                 # 是否允许 DB 查不到结果（默认 False）
    }
    """

    enhanced_payload_mapper: Callable | None = None
    """
    增强版 payload_mapper，接收 DB 查询结果 + 用户参数，返回 REST payload。

    签名: (db_result: dict, function_params: dict) -> dict

    当 pre_query 不为 None 时，此字段必须提供。
    当 pre_query 为 None 时，使用原有的 payload_mapper。
    """
```

### 2.2 配置示例

```python
# ============================================================
# 示例 1：姓名 → 员工ID → 查采购订单
# ============================================================
"query_my_purchase_orders": RestSkillConfig(
    rest_endpoint="/api/supply_chain/orders/by-employee",
    rest_method="GET",
    erp_domain="supply_chain",
    description="查询指定员工的采购订单列表",
    parameters_schema={
        "employee_name": {"type": "string", "description": "员工姓名"},
        "year": {"type": "integer", "description": "查询年份"},
    },
    required_params=["employee_name"],

    # ★ 前置查询：姓名 → employee_id
    pre_query={
        "sql": "SELECT employee_id FROM dim_employee WHERE employee_name = :employee_name",
        "params_mapping": {"employee_name": "employee_name"},
        "output_fields": ["employee_id"],
        "allow_empty": False,  # 查不到员工就报错
    },

    # ★ 增强 payload_mapper：融合 DB 结果 + 用户参数
    enhanced_payload_mapper=lambda db_result, fn_params: {
        "employee_id": db_result["employee_id"],
        "year": fn_params.get("year", 2024),
    },
)

# ============================================================
# 示例 2：项目名 → 项目编码 + 成本中心 → 查预算
# ============================================================
"query_project_budget": RestSkillConfig(
    rest_endpoint="/api/financial/budget/by-project",
    rest_method="GET",
    erp_domain="financial",
    description="查询项目的预算执行情况",
    parameters_schema={
        "project_name": {"type": "string", "description": "项目名称"},
    },
    required_params=["project_name"],

    # ★ 前置查询：项目名 → 项目编码 + 成本中心
    pre_query={
        "sql": (
            "SELECT project_code, cost_center "
            "FROM dim_project WHERE project_name = :project_name"
        ),
        "params_mapping": {"project_name": "project_name"},
        "output_fields": ["project_code", "cost_center"],
        "allow_empty": False,
    },

    # ★ 增强 payload_mapper
    enhanced_payload_mapper=lambda db_result, fn_params: {
        "project_code": db_result["project_code"],
        "cost_center": db_result["cost_center"],
    },
)

# ============================================================
# 示例 3：报销单号 → 报销单详情（含 employee_id）→ 查审批状态
# ============================================================
"query_reimbursement_approval_detail": RestSkillConfig(
    rest_endpoint="/api/financial/reimbursement/approval-chain",
    rest_method="GET",
    erp_domain="financial",
    description="查询报销单的完整审批链路（含当前处理人信息）",
    parameters_schema={
        "reimbursement_id": {"type": "string", "description": "报销单编号"},
    },
    required_params=["reimbursement_id"],

    # ★ 前置查询：先查报销单的 employee_id 和金额
    pre_query={
        "sql": (
            "SELECT employee_id, amount, dept_id "
            "FROM fact_reimbursement WHERE reimbursement_id = :reimbursement_id"
        ),
        "params_mapping": {"reimbursement_id": "reimbursement_id"},
        "output_fields": ["employee_id", "amount", "dept_id"],
        "allow_empty": False,
    },

    enhanced_payload_mapper=lambda db_result, fn_params: {
        "reimbursement_id": fn_params["reimbursement_id"],
        "employee_id": db_result["employee_id"],
        "amount": db_result["amount"],
        "dept_id": db_result["dept_id"],
    },
)

# ============================================================
# 示例 4：无前置查询（与现有行为一致）
# ============================================================
"query_annual_leave_balance": RestSkillConfig(
    rest_endpoint="/api/hr/leave/balance",
    rest_method="GET",
    erp_domain="hr",
    description="查询员工的年假剩余天数",
    parameters_schema={
        "employee_name": {"type": "string", "description": "员工姓名"},
        "year": {"type": "integer", "description": "查询年份"},
    },
    required_params=["employee_name"],
    # pre_query 为 None，使用原有的 payload_mapper
    payload_mapper=lambda params: {
        "employee_name": params["employee_name"],
        "year": params.get("year", 2024),
    },
)
```

---

## 3. RestTool 内部流程

### 3.1 完整流程图

```
RestTool.invoke(input_data)
  │
  ├─ 1. _validate_input(input_data) → 参数校验 + 填充默认值
  │
  ├─ 2. function_name = input_data.get("function", "")
  │     │
  │     ├─ 有 function_name？
  │     │   YES → 3a. 查 _FUNCTION_SKILL_MAP 获取 config
  │     │         │
  │     │         ├─ config 存在？
  │     │         │   NO → _empty_result("UNKNOWN_FUNCTION")
  │     │         │
  │     │         │   YES → 4a. config.pre_query 不为 None？
  │     │         │         │
  │     │         │         ├─ 是 → 5. _execute_pre_query(config, function_params, input_data)
  │     │         │         │       │
  │     │         │         │       ├─ 构造 SQL 参数
  │     │         │         │       ├─ 调用 DatabaseTool.query()
  │     │         │         │       ├─ 提取 output_fields
  │     │         │         │       │
  │     │         │         │       ├─ 有结果？
  │     │         │         │       │   YES → db_result = {...}
  │     │         │         │       │   NO → allow_empty？
  │     │         │         │       │         YES → db_result = {}
  │     │         │         │       │         NO → _empty_result("PRE_QUERY_EMPTY")
  │     │         │         │       │
  │     │         │         │       └─ 返回 db_result
  │     │         │         │
  │     │         │         │   6. rest_payload = config.enhanced_payload_mapper(db_result, function_params)
  │     │         │         │
  │     │         │         └─ 否 → 6b. rest_payload = config.payload_mapper(function_params)
  │     │         │
  │     │         │   rest_endpoint = config.rest_endpoint
  │     │         │   rest_method = config.rest_method
  │     │         │   erp_domain = config.erp_domain
  │     │         │
  │     │   NO → 3b. 直接调用方式（兼容）
  │     │         rest_endpoint = input_data.rest_endpoint
  │     │         rest_method = input_data.rest_method
  │     │         rest_payload = input_data.rest_payload
  │     │         erp_domain = input_data.erp_domain
  │     │
  │     ├─ rest_endpoint 为空？
  │     │   YES → _empty_result("MISSING_ENDPOINT")
  │     │
  │  ├─ 7. RestRuntime.execute(endpoint, method, payload, ...)
  │  │     → MCP 服务 → ERP 系统
  │  │     ← response
  │  │
  │  ├─ 8. _parse_response_to_docs(response) → docs
  │  ├─ 9. _evaluate_quality(docs, response) → quality_score
  │  └─ 10. 返回 RestToolOutput
```

### 3.2 invoke() 方法变更（伪代码）

```python
async def invoke(self, input_data: dict) -> RestToolOutput:
    start_time = time.time()

    # 1. 参数校验
    input_data = self._validate_input(input_data)

    # 2. 解析调用参数
    function_name = input_data.get("function", "")
    function_params = input_data.get("function_params", {})
    pre_query_used = False  # 标记是否使用了前置查询
    pre_query_latency_ms = 0  # 前置查询耗时

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

        # ★ 前置查询分支
        if config.pre_query and config.enhanced_payload_mapper:
            pre_query_start = time.time()
            try:
                db_result = await self._execute_pre_query(
                    config.pre_query, function_params, input_data
                )
            except Exception as e:
                logger.error(f"[RestTool] 前置查询失败: {e}")
                return self._empty_result(
                    error=f"Pre-query failed: {e}",
                    error_code="PRE_QUERY_FAILED",
                    input_data=input_data,
                )

            pre_query_latency_ms = int((time.time() - pre_query_start) * 1000)
            pre_query_used = True

            if db_result is None:
                return self._empty_result(
                    error=f"No matching record found in pre-query for function: {function_name}",
                    error_code="PRE_QUERY_EMPTY",
                    input_data=input_data,
                )

            try:
                rest_payload = config.enhanced_payload_mapper(db_result, function_params)
            except Exception as e:
                logger.error(f"[RestTool] enhanced_payload_mapper 执行失败: {e}")
                return self._empty_result(
                    error=f"Enhanced payload mapper failed: {e}",
                    error_code="PAYLOAD_MAPPER_ERROR",
                    input_data=input_data,
                )
        else:
            # 原有逻辑：普通 payload_mapper
            try:
                rest_payload = config.payload_mapper(function_params)
            except Exception as e:
                logger.error(f"[RestTool] payload_mapper 执行失败: {e}")
                return self._empty_result(
                    error=f"Payload mapper failed: {e}",
                    error_code="PAYLOAD_MAPPER_ERROR",
                    input_data=input_data,
                )
    else:
        # 直接调用方式（兼容）
        rest_endpoint = input_data.get("rest_endpoint", "")
        rest_method = input_data.get("rest_method", "GET")
        rest_payload = input_data.get("rest_payload")
        erp_domain = input_data.get("erp_domain", "")

    if not rest_endpoint:
        return self._empty_result(
            error="No REST endpoint specified",
            error_code="MISSING_ENDPOINT",
            input_data=input_data,
        )

    # 3. 调用 MCP 服务
    runtime = get_rest_runtime()
    try:
        response = await runtime.execute(
            endpoint=rest_endpoint,
            method=rest_method,
            payload=rest_payload,
            headers=input_data.get("rest_headers"),
            tenant_id=input_data.get("tenant_id", ""),
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

    # 4. 解析响应 → 标准化 docs
    docs = self._parse_response_to_docs(response)

    # 5. 质量评分
    quality = self._evaluate_quality(docs, response)

    latency_ms = int((time.time() - start_time) * 1000)

    # 6. 构建输出
    return RestToolOutput(
        success=True,
        rest_status_code=response.get("status_code", 200),
        rest_response=response.get("data", {}),
        rest_response_text=response.get("text", ""),
        rest_latency_ms=response.get("latency_ms", latency_ms),
        docs=docs,
        quality_score=quality,
        result_count=len(docs),
        rest_endpoint=rest_endpoint,
        rest_method=rest_method,
        erp_domain=erp_domain,
        mcp_server_name=input_data.get("mcp_server_name", "erp_mcp_server"),
        rest_query_signature=self._compute_query_signature(input_data),
        rest_evidence_signature=self._compute_evidence_signature(response),
        circuit_breaker_open=False,
        fallback_used=False,
        # ★ 新增：前置查询元数据
        pre_query_used=pre_query_used,
        pre_query_latency_ms=pre_query_latency_ms,
    )
```

### 3.3 _execute_pre_query() 实现

```python
async def _execute_pre_query(
    self,
    pre_query: dict,
    function_params: dict,
    input_data: dict,
) -> dict | None:
    """执行前置 DB 查询，返回结果的第一行或 None。

    流程：
    1. 从 pre_query.params_mapping 构造 SQL 参数
    2. 调用 DatabaseTool 执行查询
    3. 提取 output_fields 指定的字段
    4. 返回 dict 或 None（无结果时）

    Args:
        pre_query: 前置查询配置
        function_params: 用户提供的函数参数
        input_data: RestTool 输入

    Returns:
        dict | None: 提取的字段字典，或 None（无结果且 allow_empty=False）
    """
    from agent.langgraph.tools.database_tool import get_database_tool

    # 1. 构造 SQL 参数
    sql_params = {}
    for sql_param, fn_key in pre_query["params_mapping"].items():
        value = function_params.get(fn_key)
        if value is None:
            logger.warning(
                f"[RestTool] pre_query 参数缺失: "
                f"SQL 参数 '{sql_param}' 需要 function_params['{fn_key}']，"
                f"但 function_params 中不存在"
            )
            continue
        sql_params[sql_param] = value

    # 2. 调用 DatabaseTool
    db_tool = get_database_tool()
    db_tool_name = pre_query.get("db_tool_name", "database")

    db_result = await db_tool.query(
        sql=pre_query["sql"],
        params=sql_params,
        tenant_id=input_data.get("tenant_id", ""),
    )

    # 3. 提取 output_fields
    rows = db_result.get("rows", [])
    if not rows:
        if pre_query.get("allow_empty", False):
            return {}
        return None

    row = rows[0]
    output_fields = pre_query.get("output_fields", [])
    result = {field: row.get(field) for field in output_fields}

    logger.debug(
        f"[RestTool] pre_query 完成: "
        f"SQL 返回 {len(rows)} 行，"
        f"提取字段: {list(result.keys())}"
    )

    return result
```

### 3.4 RestToolOutput 新增字段

```python
class RestToolOutput(TypedDict, total=False):
    # ... 原有字段 ...

    # ★ 新增：前置查询元数据
    pre_query_used: bool          # 本次调用是否使用了前置查询
    pre_query_latency_ms: int     # 前置查询耗时（毫秒）
```

### 3.5 _empty_result() 更新

```python
def _empty_result(
    self,
    error: str = "",
    error_code: str = "",
    input_data: dict = None,
) -> RestToolOutput:
    # ... 原有逻辑 ...
    return RestToolOutput(
        # ... 原有字段 ...
        pre_query_used=False,      # ★ 新增
        pre_query_latency_ms=0,    # ★ 新增
    )
```

---

## 4. 错误处理

### 4.1 错误码定义

| 错误码 | 触发条件 | 用户可见信息 |
|--------|---------|-------------|
| `PRE_QUERY_FAILED` | DB 查询本身失败（连接超时、SQL 语法错误等） | "前置查询失败，请稍后重试" |
| `PRE_QUERY_EMPTY` | DB 查询成功但无匹配记录，且 `allow_empty=False` | "未找到匹配记录，请检查输入信息" |
| `PAYLOAD_MAPPER_ERROR` | `enhanced_payload_mapper` 执行时抛出异常 | "参数构造失败" |

### 4.2 降级策略

```
pre_query 查询失败
  │
  ├─ DB 连接超时 / 异常 → 错误码 PRE_QUERY_FAILED → 返回空结果
  │   （不降级，因为缺失关键参数无法调用 REST）
  │
  ├─ 查询结果为空 + allow_empty=False → 错误码 PRE_QUERY_EMPTY → 返回空结果
  │   （如"张三"在员工表中不存在，无法继续）
  │
  └─ 查询结果为空 + allow_empty=True → db_result={} → 继续执行
      （如允许部分参数缺失的场景）
```

---

## 5. 与方案一（DAG 编排）的协作

两种方案不是互斥的，而是**互补**的：

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| 查一行取几个字段 | 方案二（Skill 前置查询） | Planner 无需感知，配置自包含 |
| 多表关联、条件分支 | 方案一（DAG 编排） | 需要 Planner 的推理能力 |
| LLM 理解中间结果再决策 | 方案一（DAG 编排） | 需要 LLM 参与中间步骤 |
| 固定 SQL → 固定字段提取 | 方案二（Skill 前置查询） | 确定性逻辑，不应让 LLM 处理 |

**协同示例**：Planner 生成 DAG 步骤，其中某个 Rest Step 内部自带 pre_query。

```
Planner 生成的 ExecutionPlan:
  │
  ├─ Step 1: database（复杂查询，LLM 生成 SQL）
  │     SELECT ... FROM ... JOIN ... WHERE ...
  │
  ├─ Step 2: rest, function="query_my_orders"
  │     depends_on: [step_1]
  │     │
  │     └─ RestTool 内部:
  │         ├─ pre_query: SELECT employee_id FROM employee WHERE name=?
  │         │   （从 step_1 结果中取 employee_name）
  │         └─ REST: GET /api/supply_chain/orders?employee_id=xxx
  │
  └─ Step 3: rag
```

---

## 6. 实施清单

### 6.1 代码变更

| 文件 | 变更内容 |
|------|---------|
| `agent/langgraph/tools/rest_tool.py` | `RestSkillConfig` 新增 `pre_query` 和 `enhanced_payload_mapper` 字段 |
| | `RestToolOutput` 新增 `pre_query_used` 和 `pre_query_latency_ms` 字段 |
| | `invoke()` 新增前置查询分支 |
| | 新增 `_execute_pre_query()` 方法 |
| | `_empty_result()` 新增 pre_query 字段 |
| | `_validate_single_config()` 新增 pre_query 与 enhanced_payload_mapper 一致性校验 |

### 6.2 配置校验增强

在 `_validate_single_config()` 中新增：

```python
# 7. pre_query 与 enhanced_payload_mapper 一致性校验
if config.pre_query is not None:
    if not config.enhanced_payload_mapper:
        errors.append("配置了 pre_query 但未提供 enhanced_payload_mapper")
    if not callable(config.enhanced_payload_mapper):
        errors.append("enhanced_payload_mapper 不可调用")
    if not config.pre_query.get("sql"):
        errors.append("pre_query.sql 不能为空")
    if not config.pre_query.get("output_fields"):
        errors.append("pre_query.output_fields 不能为空")
```

### 6.3 测试用例

| 测试类 | 测试内容 |
|--------|---------|
| `TestPreQuery` | pre_query 正常执行，DB 结果正确传入 enhanced_payload_mapper |
| `TestPreQueryEmpty` | DB 无结果 → allow_empty=False → PRE_QUERY_EMPTY |
| `TestPreQueryEmptyAllowed` | DB 无结果 → allow_empty=True → db_result={} |
| `TestPreQueryDBFailure` | DB 连接失败 → PRE_QUERY_FAILED |
| `TestPreQueryMapperError` | enhanced_payload_mapper 抛异常 → PAYLOAD_MAPPER_ERROR |
| `TestPreQueryBackwardCompat` | 无 pre_query 的 Skill 不受影响 |
| `TestPreQueryOutputFields` | 验证 output_fields 提取正确 |
| `TestPreQueryConfigValidation` | 配置校验：pre_query 无 enhanced_payload_mapper 应报错 |

---

## 7. 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 前置查询位置 | RestTool 内部 | 对 Planner 透明，配置自包含 |
| 前置查询工具 | 复用 DatabaseTool | 不重复造轮子，利用现有 MCP 连接和 SQL 能力 |
| 结果提取方式 | `output_fields` 白名单 | 显式声明，避免 DB 字段泄漏到 REST 请求 |
| 空结果处理 | `allow_empty` 开关 | 不同场景容忍度不同 |
| 与方案一的关系 | 互补而非替代 | 简单场景用方案二，复杂场景用方案一 |
| DB 工具选择 | 通过 `db_tool_name` 指定 | 未来可能区分不同 DB 实例（如 `database` vs `analytics_db`） |