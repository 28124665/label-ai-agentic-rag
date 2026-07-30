# 意图路由三层递进架构改造任务清单

## Phase 1: 基础架构与第0层前置过滤

- [x] Task 1: 创建路由配置数据结构
  - [x] 1.1 创建 `agent/langgraph/routers/__init__.py`
  - [x] 1.2 创建 `agent/langgraph/routers/models.py`，定义 `RouteDecision` 数据模型
  - [x] 1.3 验证数据结构定义

- [x] Task 2: 实现第0层前置过滤器
  - [x] 2.1 创建 `agent/langgraph/routers/pre_filter.py`
  - [x] 2.2 实现安全拦截（SQL 注入特征、越权关键词检测）
  - [x] 2.3 实现显式外部搜索指令识别
  - [x] 2.4 实现问候语识别
  - [x] 2.5 实现实体格式匹配（SKU-\d+、ORD-\d+ 等）
  - [x] 2.6 编写单元测试验证各拦截场景

- [x] Task 3: 创建路由配置文件
  - [x] 3.1 创建 `config/intent_router.yaml` 配置文件模板
  - [x] 3.2 实现配置加载器 `agent/langgraph/routers/config_loader.py`
  - [x] 3.3 验证配置文件加载和热更新机制

## Phase 2: 第1层规则路由增强

- [x] Task 4: 增强规则路由器
  - [x] 4.1 创建 `agent/langgraph/routers/rule_router.py`
  - [x] 4.2 实现范围词优先判断（scope=external/internal）
  - [x] 4.3 扩充 DB 偏向词库（多少、总额、占比、排名、TOP、环比、同比等）
  - [x] 4.4 扩充 RAG 偏向词库（定义、解释、含义、区别、联系、影响、原因、背景等）
  - [x] 4.5 实现置信度打分机制
  - [x] 4.6 实现时效性检测（今天/最新/实时 vs 去年/历史/往年）
  - [x] 4.7 从配置文件加载关键词库
  - [x] 4.8 编写单元测试验证规则路由准确性

## Phase 3: 第2层 LLM 语义路由

- [x] Task 5: 实现 LLM 语义路由器
  - [x] 5.1 创建 `agent/langgraph/routers/llm_router.py`
  - [x] 5.2 实现 LLM 调用逻辑（调用 Qwen3.5-9B 或配置的小模型）
  - [x] 5.3 实现结构化 JSON 输出解析
  - [x] 5.4 实现置信度阈值判断（≥0.75 直接采纳，0.6-0.75 附标签，<0.6 触发澄清）
  - [x] 5.5 实现超时降级机制（>500ms 降级到规则路由）
  - [x] 5.6 实现连续失败熔断（3 次失败后熔断 60 秒）
  - [x] 5.7 编写单元测试（含 Mock LLM 调用）

- [x] Task 6: 实现 LLM 路由 Prompt 管理
  - [x] 6.1 创建 `config/prompts/intent_router_v1.txt` Prompt 模板
  - [x] 6.2 实现 Prompt 版本管理（支持多版本切换）
  - [x] 6.3 验证 Prompt 模板加载和变量替换

## Phase 4: 第3层 Planner 复杂任务规划

- [x] Task 7: 实现 Planner 复杂任务规划器
  - [x] 7.1 创建 `agent/langgraph/routers/planner.py`
  - [x] 7.2 实现执行计划生成（DAG 结构）
  - [x] 7.3 实现并行标记（识别无依赖子任务）
  - [x] 7.4 实现超时降级（>5s 降级到 LLM 路由结果）
  - [x] 7.5 创建 `config/prompts/planner_v1.txt` Prompt 模板
  - [x] 7.6 编写单元测试（含 Mock Planner 调用）

## Phase 5: 集成与状态扩展

- [x] Task 8: 扩展 AgentState
  - [x] 8.1 修改 `agent/langgraph/state.py`，新增 `route_decision` 字段
  - [x] 8.2 保留 `route_target` 字段（向后兼容）
  - [x] 8.3 验证状态定义

- [x] Task 9: 重构 intent_router_node
  - [x] 9.1 修改 `agent/langgraph/nodes/intent_router.py`
  - [x] 9.2 实现三层递进调用链（第0层 → 第1层 → 第2层 → 第3层）
  - [x] 9.3 集成前置过滤器、规则路由器、LLM 路由器、Planner
  - [x] 9.4 输出统一 `RouteDecision` 对象
  - [x] 9.5 记录路由决策日志（含耗时、置信度、来源）

- [x] Task 10: 更新 LangGraph 图结构
  - [x] 10.1 修改 `agent/langgraph/graph.py`，确保新状态字段正确传递
  - [x] 10.2 验证条件路由函数（`route_decision` 和 `after_rag_tool`）兼容新结构
  - [x] 10.3 编写集成测试验证完整流程

## Phase 6: 测试与验收

- [x] Task 11: 编写集成测试
  - [x] 11.1 创建 `test/test_intent_router.py`
  - [x] 11.2 测试第0层前置过滤（安全拦截、显式指令、问候语、实体格式）
  - [x] 11.3 测试第1层规则路由（范围词、词库、置信度、时效性）
  - [x] 11.4 测试第2层 LLM 路由（触发条件、结构化输出、降级机制）
  - [x] 11.5 测试第3层 Planner（任务拆解、并行标记）
  - [x] 11.6 测试 Bad Case（"数据治理是什么"、"今天天气怎么样"等）

- [x] Task 12: 性能与可观测性验证
  - [x] 12.1 验证第0层+第1层延迟 < 10ms
  - [x] 12.2 验证第2层延迟 < 300ms（Mock LLM）
  - [x] 12.3 验证路由决策日志完整（target/confidence/source/reason/complexity）
  - [x] 12.4 验证配置热更新无需重启

## Task Dependencies

- Task 2 依赖 Task 1（需要 RouteDecision 数据结构）
- Task 4 依赖 Task 1, Task 3（需要配置加载器）
- Task 5 依赖 Task 1, Task 3（需要配置和数据结构）
- Task 7 依赖 Task 1, Task 3（需要配置和数据结构）
- Task 8 依赖 Task 1（需要 RouteDecision 定义）
- Task 9 依赖 Task 2, Task 4, Task 5, Task 7, Task 8（需要所有路由器实现）
- Task 10 依赖 Task 8, Task 9（需要状态扩展和节点重构）
- Task 11 依赖 Task 10（需要完整集成）
- Task 12 依赖 Task 11（需要测试用例）
