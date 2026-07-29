# Checklist

## 状态字段验证
- [x] AgentState 包含 react_state 字段
- [x] AgentState 包含 react_execution_result 字段
- [x] AgentState 包含 evidence 列表字段
- [x] AgentState 包含 evidence_fusion_result 字段
- [x] AgentState 包含 answerability_result 字段

## Evidence 模块验证
- [x] agent/langgraph/evidence/__init__.py 存在
- [x] agent/langgraph/evidence/models.py 定义了 Evidence TypedDict
- [x] agent/langgraph/evidence/normalizer.py 提供 RAG → Evidence 转换函数
- [x] agent/langgraph/evidence/normalizer.py 提供 DB → Evidence 转换函数
- [x] agent/langgraph/evidence/normalizer.py 提供 Web → Evidence 转换函数
- [x] Evidence 标准化函数在 100ms 内完成（不调用 LLM）
- [x] 包含 fuse_evidences() 融合函数（去重、排序、冲突检测）
- [x] 包含 check_answerability() 答案充分性判断

## Policy Guard 验证
- [x] agent/langgraph/react/policy.py 存在
- [x] PolicyContext 包含 tenant_id、user_id、tool_name、arguments、current_budget
- [x] PolicyResult 包含 decision、reason、sanitized_arguments、risk_level
- [x] 工具白名单校验生效（不在白名单的工具返回 deny）
- [x] DB 工具 readonly 校验生效
- [x] DB 工具危险 SQL 关键词拦截（drop/insert/update/delete/...）
- [x] DB 工具 LIMIT 检查（needs_approval）
- [x] Web 工具 URL 注入拦截
- [x] Web 工具内网 IP 拦截
- [x] RAG 工具知识库白名单

## Budget Controller 验证
- [x] agent/langgraph/react/budget.py 存在
- [x] Budget 字段包含 step_count、tool_call_count、db_query_count、deadline_ts
- [x] max_steps 限制生效（达到后返回 budget_exhausted）
- [x] max_tool_calls 限制生效
- [x] max_db_queries 限制生效
- [x] max_llm_calls 限制生效
- [x] max_latency_ms 限制生效
- [x] token_budget 限制生效

## ReAct 子图核心验证
- [x] agent/langgraph/react/__init__.py 存在
- [x] agent/langgraph/react/models.py 定义了 ReactState、ReactAction、ReactExecutionResult
- [x] agent/langgraph/react/step.py 实现了 LLM JSON 输出解析
- [x] agent/langgraph/react/executor.py 实现了工具执行
- [x] agent/langgraph/react/graph.py 构建了 ReAct 子图主循环
- [x] ReAct 子图能正常编译（通过 build_and_compile_agent_graph 验证）
- [x] 解析失败降级为 finish
- [x] 连续 policy 拒绝自动终止

## Prompt 模板验证
- [x] rag/prompts/react_step_v1.md 存在
- [x] Prompt 包含结构化 JSON 输出指令
- [x] Prompt 包含 thought_summary、action、stop 三个字段

## LangGraph 主干节点验证
- [x] agent/langgraph/nodes/react_subgraph.py 存在
- [x] agent/langgraph/nodes/evidence_fusion.py 存在
- [x] agent/langgraph/nodes/answerability_check.py 存在
- [x] graph.py 包含 react_subgraph 节点
- [x] graph.py 包含 evidence_fusion 节点
- [x] graph.py 包含 answerability_check 节点
- [x] intent_router → react_subgraph 条件边存在
- [x] react_subgraph → evidence_fusion → answerability_check → quality_check 边存在

## 路由决策验证
- [x] intent_router.py 的 route_decision 包含 react_subgraph 分支
- [x] 当 react.enabled=false 时不进入 react_subgraph
- [x] 当 route_decision.source == "react_planner" 时路由到 react_subgraph
- [x] RouteDecision.source 扩展了 "react_planner" 字面量
- [x] clarification 优先级仍高于 react_subgraph
- [x] plan_executor 路径仍工作

## 流程完整性验证
- [x] LangGraph 图能正常编译（build_and_compile_agent_graph 不报错）
- [x] 默认配置下行为不变（react.enabled=false）
- [x] 现有 reflection、clarification、plan_executor 节点不受影响
- [x] ReAct 子图失败时降级为 chitchat/finish，不阻塞主流程
- [x] ReAct 子图不直接返回最终答案，只返回 evidence 列表

## 单元测试验证
- [x] Evidence Normalizer 测试通过（20 个）
- [x] Policy Guard 测试通过（19 个）
- [x] Budget Controller 测试通过（17 个）
- [x] ReAct Step JSON 解析测试通过（17 个）
- [x] 端到端 ReAct 子图测试通过（6 个）
- [x] LangGraph 主干集成测试通过（12 个）
- [x] 总测试数：91 个新测试全部通过
- [x] 既有测试不破坏：167 个原有测试全部通过
