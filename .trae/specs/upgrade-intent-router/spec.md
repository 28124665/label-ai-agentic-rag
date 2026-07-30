# 意图路由三层递进架构改造 Spec

## Why

当前意图路由完全由关键词规则驱动（`agent/langgraph/nodes/intent_router.py`），存在以下问题：
- 无法处理语义歧义（"数据治理是什么"被误判为 hybrid）
- 无法识别时效性需求（"今天天气怎么样"被误判为 chitchat）
- 无法拆解复合任务（"对比 A 产线和 B 产线的 OEE"只能走单一路由）
- 缺少置信度评估和低置信度澄清机制

需要将单一关键词路由升级为"规则优先 + LLM 语义路由 + Planner 复杂任务拆解"的三层递进架构。

## What Changes

### 新增功能
- **第0层：前置过滤** - 安全拦截、显式指令识别、问候语识别、实体格式匹配
- **第1层：规则路由增强** - 范围词判断、词库扩充、置信度打分、时效性检测、配置化
- **第2层：LLM 语义路由** - 结构化输出、置信度阈值、降级机制、Prompt 管理
- **第3层：Planner 复杂任务规划** - 执行计划生成（DAG）、并行标记、依赖识别
- **统一输出结构** - `RouteDecision` 对象，包含 target/confidence/source/reason/complexity/metadata

### 修改功能
- **AgentState 扩展** - 新增 `route_decision` 字段，存储完整的路由决策信息
- **intent_router_node 重构** - 从单一规则路由改为三层递进调用链
- **路由配置外部化** - 关键词库、Prompt 模板、阈值等通过配置文件管理

### 删除功能
- 无（保留原有规则路由作为第1层）

## Impact

- Affected specs: langgraph-flow-refactor（路由节点升级）
- Affected code:
  - `agent/langgraph/nodes/intent_router.py`（核心改造）
  - `agent/langgraph/state.py`（状态扩展）
  - `agent/langgraph/graph.py`（图结构调整）
  - 新增 `agent/langgraph/routers/` 目录（分层路由实现）
  - 新增 `config/intent_router.yaml`（配置文件）

## ADDED Requirements

### Requirement: 第0层前置过滤
系统 SHALL 在进入规则路由前执行前置过滤，按优先级依次检测：
1. **安全拦截**：SQL 注入特征（`'; DROP`、`--`、`UNION SELECT`）和越权关键词（"删库"、"所有用户密码"），直接驳回并记录审计日志
2. **显式外部搜索指令**：同时命中"动作词（搜索/查一下/上网找）"和"外部范围词（网上/百度/谷歌/外部/全网）"，直接路由 `web_tool`
3. **问候语识别**：匹配"你好/谢谢/嗨/hello"等问候模式，直接路由 `chitchat`
4. **实体格式匹配**：匹配预定义实体格式（如 `SKU-\d+`、`ORD-\d+`、`工单号：\d+`），直接路由 `db_tool`

#### Scenario: 安全拦截
- **WHEN** 用户输入包含 SQL 注入特征或越权关键词
- **THEN** 直接返回拒绝响应，记录审计日志，不进入后续路由层

#### Scenario: 显式外部搜索
- **WHEN** 用户输入同时包含动作词和外部范围词（如"百度搜索最新新闻"）
- **THEN** 直接路由到 `web_tool`，跳过后续路由层

### Requirement: 第1层规则路由增强
系统 SHALL 保留并增强现有规则路由，增加以下能力：
1. **范围词优先判断**：先判断查询范围（`scope=external` 或 `scope=internal`）
2. **词库扩充**：
   - DB 偏向词：增加"多少、总额、占比、排名、TOP、环比、同比、最大值、最小值"
   - RAG 偏向词：增加"定义、解释、含义、区别、联系、影响、原因、背景"
3. **置信度打分**：每个规则命中输出 `confidence` 分数（0-1），多条规则冲突时取最高分
4. **时效性检测**：命中"今天/最新/实时/current/latest/trending"且未命中"去年/历史/往年/过去"时，标记 `freshness_required=true`，路由建议为 `web`
5. **配置化**：所有关键词库、优先级、置信度阈值通过 YAML 配置文件管理

#### Scenario: 规则路由置信度评估
- **WHEN** 用户输入"Q3 华东区总产量"
- **THEN** 规则路由识别为 `database`，置信度 0.85，原因："命中聚合词'总'和实体'Q3'"

#### Scenario: 时效性检测
- **WHEN** 用户输入"今天天气怎么样"
- **THEN** 规则路由识别为 `web`，标记 `freshness_required=true`，置信度 0.75

### Requirement: 第2层 LLM 语义路由
系统 SHALL 在第1层规则路由置信度 < 0.7 时，调用 LLM 进行语义路由：
1. **触发条件**：仅当第1层 `confidence < 0.7` 或规则无法判定时触发
2. **模型选型**：使用 Qwen3.5-9B（或同等能力模型），单次推理延迟 < 300ms
3. **结构化输出**：强制 LLM 输出 JSON，包含：
   - `primary_intent`: database | rag | web | hybrid | chitchat
   - `confidence`: 0.0-1.0
   - `reason`: 路由理由
   - `complexity`: simple | moderate | complex
   - `sub_intents`: 子意图列表
   - `entities`: 提取的关键实体
   - `needs_clarification`: true | false
   - `clarification_question`: 如需澄清时的追问话术
4. **置信度阈值**：
   - `confidence ≥ 0.75`：直接采纳
   - `0.6 ≤ confidence < 0.75`：采纳但附带低置信度标签
   - `confidence < 0.6`：触发用户澄清
5. **降级机制**：LLM 超时（> 500ms）或连续失败 3 次时，降级到第1层规则路由

#### Scenario: LLM 语义路由
- **WHEN** 第1层规则路由置信度为 0.65，用户输入"统计学原理是什么"
- **THEN** 触发第2层 LLM 路由，LLM 输出 `{"primary_intent": "rag", "confidence": 0.92, "reason": "询问概念定义"}`，最终路由到 `rag_tool`

#### Scenario: 低置信度澄清
- **WHEN** LLM 路由输出 `confidence < 0.6`
- **THEN** 不直接路由，返回澄清问题给用户（如"您是想查询数据还是了解概念定义？"）

### Requirement: 第3层 Planner 复杂任务规划
系统 SHALL 在第2层 LLM 路由输出 `complexity = "complex"` 时，调用 Planner 进行任务拆解：
1. **触发条件**：仅当第2层 `complexity = "complex"` 时触发
2. **模型选型**：使用 Qwen3.6-27B（或同等能力模型），单次推理延迟 < 3s
3. **执行计划生成**：输出结构化 DAG，包含：
   - `plan_id`: 计划唯一标识
   - `steps`: 步骤列表，每步包含 `tool`、`args`、`depends_on`
   - `estimated_tokens`: 预估 Token 消耗
   - `fallback_strategy`: 降级方案
4. **并行标记**：识别无依赖关系的子任务，标记为可并行执行

#### Scenario: 复杂任务拆解
- **WHEN** 用户输入"对比 A 产线和 B 产线的 OEE，并分析差异原因"
- **THEN** Planner 生成执行计划：
  ```json
  {
    "plan_id": "plan_001",
    "steps": [
      {"id": "step1", "tool": "db_tool", "args": {"query": "A产线OEE"}, "depends_on": []},
      {"id": "step2", "tool": "db_tool", "args": {"query": "B产线OEE"}, "depends_on": []},
      {"id": "step3", "tool": "rag_tool", "args": {"query": "OEE差异原因"}, "depends_on": ["step1", "step2"]}
    ]
  }
  ```

### Requirement: 路由结果统一输出
系统 SHALL 将三层路由的输出统一为 `RouteDecision` 对象：
- `target`: rag | db | web | hybrid | chitchat
- `confidence`: 0.0-1.0
- `source`: rule | llm | planner
- `reason`: 决策理由
- `complexity`: simple | moderate | complex
- `metadata`: 附加信息（entities、sub_intents、plan 等）

#### Scenario: 统一输出结构
- **WHEN** 第1层规则路由决策完成
- **THEN** 输出 `RouteDecision(target="database", confidence=0.85, source="rule", reason="命中聚合词", complexity="simple", metadata={"entities": {"metric": "产量", "time": "Q3"}})`

### Requirement: 路由配置外部化
系统 SHALL 将路由相关配置通过 YAML 文件管理，支持热加载：
- 关键词库（DB 偏向词、RAG 偏向词、范围词、时效性词）
- 置信度阈值（规则路由触发 LLM 的阈值、LLM 触发澄清的阈值）
- Prompt 模板（LLM 路由 Prompt、Planner Prompt）
- 超时配置（LLM 超时、Planner 超时）

#### Scenario: 配置热加载
- **WHEN** 修改 `config/intent_router.yaml` 中的关键词库
- **THEN** 无需重启服务，新配置立即生效

## MODIFIED Requirements

### Requirement: AgentState 扩展
**原状态**：
```python
class AgentState(TypedDict):
    route_target: Optional[str]  # 仅存储路由目标
```

**新状态**：
```python
class AgentState(TypedDict):
    route_target: Optional[str]  # 保留，用于向后兼容
    route_decision: Optional[RouteDecision]  # 新增，完整的路由决策信息
```

**RouteDecision 数据结构**：
```python
class RouteDecision(BaseModel):
    target: str  # rag | db | web | hybrid | chitchat
    confidence: float  # 0.0-1.0
    source: str  # rule | llm | planner
    reason: str  # 决策理由
    complexity: str  # simple | moderate | complex
    metadata: Dict[str, Any]  # 附加信息
```

### Requirement: intent_router_node 重构
**原逻辑**：
```python
def intent_router_node(state: AgentState) -> dict[str, Any]:
    route_target = _route_intent(user_question)  # 单一规则路由
    return {"route_target": route_target}
```

**新逻辑**：
```python
def intent_router_node(state: AgentState) -> dict[str, Any]:
    # 第0层：前置过滤
    decision = pre_filter(user_question)
    if decision:
        return {"route_target": decision.target, "route_decision": decision}
    
    # 第1层：规则路由
    decision = rule_router(user_question)
    if decision.confidence >= 0.7:
        return {"route_target": decision.target, "route_decision": decision}
    
    # 第2层：LLM 语义路由
    decision = llm_router(user_question, rule_decision=decision)
    if decision.complexity != "complex":
        return {"route_target": decision.target, "route_decision": decision}
    
    # 第3层：Planner 复杂任务规划
    plan = planner(user_question, llm_decision=decision)
    decision.metadata["plan"] = plan
    return {"route_target": decision.target, "route_decision": decision}
```

## REMOVED Requirements

无（保留原有规则路由作为第1层）
