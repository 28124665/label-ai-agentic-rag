# 意图路由三层递进架构改造检查清单

## 基础架构检查
- [ ] `agent/langgraph/routers/` 目录已创建
- [ ] `agent/langgraph/routers/__init__.py` 已创建并导出所有路由器
- [ ] `agent/langgraph/routers/models.py` 已定义 `RouteDecision` 数据模型
- [ ] `RouteDecision` 包含字段：target, confidence, source, reason, complexity, metadata
- [ ] `config/intent_router.yaml` 配置文件已创建
- [ ] 配置加载器支持热更新（无需重启服务）

## 第0层前置过滤检查
- [ ] `agent/langgraph/routers/pre_filter.py` 已实现
- [ ] 安全拦截：检测 SQL 注入特征（`'; DROP`、`--`、`UNION SELECT`）
- [ ] 安全拦截：检测越权关键词（"删库"、"所有用户密码"）
- [ ] 安全拦截：拦截后记录审计日志
- [ ] 显式外部搜索：同时命中动作词和外部范围词时路由到 web
- [ ] 问候语识别：匹配"你好/谢谢/嗨/hello"等模式
- [ ] 实体格式匹配：匹配 `SKU-\d+`、`ORD-\d+`、`工单号：\d+` 等格式
- [ ] 前置过滤器返回 `RouteDecision` 对象或 None

## 第1层规则路由检查
- [ ] `agent/langgraph/routers/rule_router.py` 已实现
- [ ] 范围词优先判断：检测"网上/百度/谷歌"标记为 external
- [ ] DB 偏向词库扩充：包含"多少、总额、占比、排名、TOP、环比、同比、最大值、最小值"
- [ ] RAG 偏向词库扩充：包含"定义、解释、含义、区别、联系、影响、原因、背景"
- [ ] 置信度打分：每个规则命中输出 0-1 的 confidence
- [ ] 多条规则冲突时取最高置信度
- [ ] 时效性检测：命中"今天/最新/实时"且未命中"去年/历史/往年"时标记 freshness_required
- [ ] 从配置文件加载关键词库
- [ ] 返回 `RouteDecision` 对象，source="rule"

## 第2层 LLM 语义路由检查
- [ ] `agent/langgraph/routers/llm_router.py` 已实现
- [ ] 触发条件：仅当第1层 confidence < 0.7 时调用
- [ ] LLM 调用逻辑：调用配置的模型（Qwen3.5-9B 或同等）
- [ ] 结构化输出：强制 JSON 格式，包含 primary_intent, confidence, reason, complexity, sub_intents, entities, needs_clarification, clarification_question
- [ ] JSON 解析成功率 > 99%
- [ ] 置信度阈值：≥0.75 直接采纳
- [ ] 置信度阈值：0.6-0.75 采纳但附标签
- [ ] 置信度阈值：<0.6 触发用户澄清
- [ ] 超时降级：>500ms 降级到第1层规则路由
- [ ] 连续失败熔断：3 次失败后熔断 60 秒
- [ ] 返回 `RouteDecision` 对象，source="llm"
- [ ] Prompt 模板 `config/prompts/intent_router_v1.txt` 已创建
- [ ] Prompt 版本管理：支持多版本切换

## 第3层 Planner 检查
- [ ] `agent/langgraph/routers/planner.py` 已实现
- [ ] 触发条件：仅当第2层 complexity="complex" 时调用
- [ ] 执行计划生成：输出 DAG 结构，包含 plan_id, steps, estimated_tokens, fallback_strategy
- [ ] 每个 step 包含：id, tool, args, depends_on
- [ ] 并行标记：识别无依赖子任务
- [ ] 超时降级：>5s 降级到第2层路由结果
- [ ] Prompt 模板 `config/prompts/planner_v1.txt` 已创建
- [ ] 返回 `RouteDecision` 对象，source="planner"，metadata 包含 plan

## AgentState 扩展检查
- [ ] `agent/langgraph/state.py` 已修改
- [ ] 新增 `route_decision: Optional[RouteDecision]` 字段
- [ ] 保留 `route_target: Optional[str]` 字段（向后兼容）

## intent_router_node 重构检查
- [ ] `agent/langgraph/nodes/intent_router.py` 已重构
- [ ] 实现三层递进调用链：第0层 → 第1层 → 第2层 → 第3层
- [ ] 集成前置过滤器、规则路由器、LLM 路由器、Planner
- [ ] 输出统一 `RouteDecision` 对象到 state.route_decision
- [ ] 同时更新 state.route_target（向后兼容）
- [ ] 记录路由决策日志（含耗时、置信度、来源、原因）

## LangGraph 图结构检查
- [ ] `agent/langgraph/graph.py` 已更新
- [ ] 条件路由函数 `route_decision` 兼容新结构
- [ ] 条件路由函数 `after_rag_tool` 兼容新结构
- [ ] 新状态字段正确传递

## 测试检查
- [ ] `test/test_intent_router.py` 已创建
- [ ] 测试第0层：安全拦截（SQL 注入、越权关键词）
- [ ] 测试第0层：显式外部搜索指令
- [ ] 测试第0层：问候语识别
- [ ] 测试第0层：实体格式匹配
- [ ] 测试第1层：范围词判断
- [ ] 测试第1层：DB 偏向词命中
- [ ] 测试第1层：RAG 偏向词命中
- [ ] 测试第1层：置信度打分
- [ ] 测试第1层：时效性检测
- [ ] 测试第2层：触发条件（confidence < 0.7）
- [ ] 测试第2层：结构化 JSON 输出
- [ ] 测试第2层：置信度阈值判断
- [ ] 测试第2层：超时降级
- [ ] 测试第2层：连续失败熔断
- [ ] 测试第3层：任务拆解（DAG 生成）
- [ ] 测试第3层：并行标记
- [ ] 测试 Bad Case："数据治理是什么" → rag
- [ ] 测试 Bad Case："今天天气怎么样" → web
- [ ] 测试 Bad Case："最近质量异常有改善吗" → database
- [ ] 测试 Bad Case："对比 A 产线和 B 产线的 OEE" → hybrid

## 性能与可观测性检查
- [ ] 第0层+第1层延迟 < 10ms（P95）
- [ ] 第2层延迟 < 300ms（P95，Mock LLM）
- [ ] 路由决策日志完整：target/confidence/source/reason/complexity
- [ ] 配置热更新无需重启服务
- [ ] 100% 路由决策可追溯（审计日志）

## 验收标准
- [ ] 规则路由准确率从 ~70% 提升至 ≥ 80%
- [ ] 整体路由准确率 ≥ 90%（基于黄金测试集）
- [ ] 第2层触发率占整体请求的 20%-30%
- [ ] 第3层触发率占整体请求的 5%-10%
- [ ] 低置信度触发澄清的比例 < 5%
- [ ] 所有 Bad Case 已修复并加入测试集
