# Tasks

- [x] Task 1: 扩展 AgentState 状态定义，新增 ReAct 和 Evidence 相关字段
  - [x] SubTask 1.1: 在 state.py 中新增 react_state、react_execution_result 字段
  - [x] SubTask 1.2: 在 state.py 中新增 evidence、evidence_fusion_result、answerability_result 字段

- [x] Task 2: 创建 Evidence 标准化模块
  - [x] SubTask 2.1: 创建 agent/langgraph/evidence/__init__.py
  - [x] SubTask 2.2: 创建 agent/langgraph/evidence/models.py，定义 Evidence 数据结构
  - [x] SubTask 2.3: 创建 agent/langgraph/evidence/normalizer.py，实现 RAG/DB/Web 结果的 Evidence 转换

- [x] Task 3: 创建 Policy Guard 策略校验模块
  - [x] SubTask 3.1: 创建 agent/langgraph/react/policy.py，实现工具白名单、权限校验、参数安全检查
  - [x] SubTask 3.2: 实现 DecisionType 枚举（allow/deny/needs_approval/needs_clarification/rewrite_arguments）
  - [x] SubTask 3.3: 实现 PolicyContext 和 PolicyResult 数据结构

- [x] Task 4: 创建 Budget Controller 预算控制模块
  - [x] SubTask 4.1: 创建 agent/langgraph/react/budget.py
  - [x] SubTask 4.2: 实现 Budget 状态检查（max_steps、max_tool_calls、max_db_queries、max_latency_ms、token_budget）
  - [x] SubTask 4.3: 实现超限自动终止逻辑

- [x] Task 5: 创建 ReAct 子图核心模块
  - [x] SubTask 5.1: 创建 agent/langgraph/react/__init__.py
  - [x] SubTask 5.2: 创建 agent/langgraph/react/models.py，定义 ReactState、ReactAction、ReactExecutionResult
  - [x] SubTask 5.3: 创建 agent/langgraph/react/step.py，实现单步推理（LLM JSON 输出协议）
  - [x] SubTask 5.4: 创建 agent/langgraph/react/executor.py，实现工具执行和 Observation 标准化
  - [x] SubTask 5.5: 创建 agent/langgraph/react/graph.py，构建 ReAct 子图状态机

- [x] Task 6: 创建 ReAct Step Prompt 模板
  - [x] SubTask 6.1: 创建 rag/prompts/react_step_v1.md，定义 ReAct Step 的 LLM Prompt
  - [x] SubTask 6.2: 强制 LLM 输出 JSON 协议

- [x] Task 7: 创建 ReAct 子图节点和 evidence_fusion/answerability_check 节点
  - [x] SubTask 7.1: 创建 agent/langgraph/nodes/react_subgraph.py，作为 LangGraph 主干节点
  - [x] SubTask 7.2: 实现 evidence_fusion_node 节点
  - [x] SubTask 7.3: 实现 answerability_check_node 节点

- [x] Task 8: 修改 LangGraph 主干图
  - [x] SubTask 8.1: 在 graph.py 中新增 react_subgraph、evidence_fusion、answerability_check 节点
  - [x] SubTask 8.2: 添加边：intent_router → react_subgraph（条件路由）
  - [x] SubTask 8.3: 添加边：react_subgraph → evidence_fusion → answerability_check → quality_check
  - [x] SubTask 8.4: 保持现有所有边不变，向后兼容

- [x] Task 9: 扩展 intent_router 路由决策
  - [x] SubTask 9.1: 修改 route_decision 函数，新增 react_subgraph 分支
  - [x] SubTask 9.2: 当 route_decision.source == "react_planner" 时返回 "react_subgraph"
  - [x] SubTask 9.3: 默认禁用 ReAct 子图（react.enabled=false 时不路由）

- [x] Task 10: 编写单元测试
  - [x] SubTask 10.1: Evidence Normalizer 测试（20 个用例全部通过）
  - [x] SubTask 10.2: Policy Guard 测试（19 个用例全部通过）
  - [x] SubTask 10.3: Budget Controller 测试（17 个用例全部通过）
  - [x] SubTask 10.4: ReAct Step JSON 解析测试（17 个用例全部通过）
  - [x] SubTask 10.5: 端到端 ReAct 子图测试（6 个用例全部通过）+ 集成测试（12 个用例全部通过）

# Task Dependencies
- Task 2 独立，无依赖
- Task 3 depends on Task 1
- Task 4 独立
- Task 5 depends on Task 1, Task 2, Task 3, Task 4
- Task 6 独立
- Task 7 depends on Task 2, Task 5
- Task 8 depends on Task 7
- Task 9 depends on Task 5
- Task 10 depends on Task 3, Task 4, Task 5, Task 7
