# LangGraph 流程编排重构任务清单

## Phase 1: 基础设施搭建

- [x] Task 1: 安装 LangGraph 依赖
  - [x] 1.1 在 pyproject.toml 中添加 langgraph、langchain-core、langchain 依赖
  - [x] 1.2 运行 uv sync 安装依赖
  - [x] 1.3 验证依赖安装成功（langgraph、langchain-core 已验证）

- [x] Task 2: 创建 LangGraph 目录结构
  - [x] 2.1 创建 agent/langgraph/ 目录
  - [x] 2.2 创建 agent/langgraph/__init__.py
  - [x] 2.3 创建 agent/langgraph/state.py（AgentState 定义）
  - [x] 2.4 创建 agent/langgraph/nodes/ 目录（节点实现）
  - [x] 2.5 创建 agent/langgraph/tools/ 目录（工具封装）
  - [x] 2.6 创建 agent/langgraph/graph.py（状态图构建）

- [x] Task 3: 定义 AgentState 统一状态
  - [x] 3.1 在 state.py 中定义 AgentState TypedDict
  - [x] 3.2 包含用户输入字段：user_question、query_lang
  - [x] 3.3 包含路由决策字段：route_target
  - [x] 3.4 包含 RAG Tool 输出字段：rag_docs、rag_quality_score、rag_has_relevant、rag_relevant_count
  - [x] 3.5 包含 Database Tool 输出字段：db_result、db_quality_score
  - [x] 3.6 包含 Web Tool 输出字段：web_docs
  - [x] 3.7 包含融合上下文字段：merged_context
  - [x] 3.8 包含 LLM 生成字段：generated_answer
  - [x] 3.9 包含幻觉检测字段：hallucination_score、hallucination_action
  - [x] 3.10 包含重试控制字段：retry_count、max_retries
  - [x] 3.11 包含可观测性字段：trace_id、node_timings
  - [x] 3.12 编写单元测试验证状态定义

## Phase 2: 工具封装层实现

- [x] Task 4: 实现 RAGTool 封装类（部分完成，待完善 TODO）
  - [x] 4.1 在 agent/langgraph/tools/rag_tool.py 创建 RAGTool 类
  - [x] 4.2 定义 RAGToolInput 和 RAGToolOutput TypedDict
  - [x] 4.3 实现 invoke 方法，调用现有 RAG 流程组件
  - [x] 4.4 内部调用 QueryPreprocessor 进行查询预处理
  - [ ] 4.5 内部调用 QueryRewriter 进行查询重写（TODO，待集成）
  - [x] 4.6 内部调用 HybridRetriever 进行混合检索
  - [ ] 4.7 内部调用 MultilingualReranker 进行 Rerank（TODO，待集成）
  - [ ] 4.8 内部调用 Grader 进行质量评估（TODO，待集成）
  - [ ] 4.9 实现内部重试逻辑（最多 1 次）（TODO，待集成）
  - [x] 4.10 返回结构化输出（docs、quality_score、has_relevant、relevant_count、top_score、rewrite_history）
  - [ ] 4.11 编写单元测试验证 RAGTool 独立运行

- [x] Task 5: 实现 DatabaseTool 封装类（参考原有实现）
  - [x] 5.1 在 agent/langgraph/tools/database_tool.py 创建 DatabaseTool 类
  - [x] 5.2 定义 DatabaseToolInput 和 DatabaseToolOutput TypedDict
  - [x] 5.3 实现 invoke 方法，调用现有数据库工具
  - [x] 5.4 内部调用意图路由匹配目标数据库
  - [x] 5.5 内部调用模板匹配或 NL-to-SQL 生成（占位符，待集成）
  - [x] 5.6 内部调用渐进式 Schema 发现（list_tables → describe_table）
  - [x] 5.7 内部调用 SQL 安全检查
  - [x] 5.8 内部调用 SQL 执行（带自愈重试）
  - [x] 5.9 内部调用结果格式化
  - [x] 5.10 返回结构化输出（sql、rows、row_count、source、quality_score、execution_time_ms）
  - [ ] 5.11 编写单元测试验证 DatabaseTool 独立运行

- [x] Task 6: 实现 WebTool 封装类
  - [x] 6.1 在 agent/langgraph/tools/web_tool.py 创建 WebTool 类
  - [x] 6.2 定义 WebToolInput 和 WebToolOutput TypedDict
  - [x] 6.3 实现 invoke 方法，调用现有 Web 搜索功能
  - [x] 6.4 支持 Tavily 和 DuckDuckGo 搜索引擎
  - [x] 6.5 返回结构化输出（docs、urls）
  - [ ] 6.6 编写单元测试验证 WebTool 独立运行

## Phase 3: LangGraph 节点实现

- [ ] Task 7: 实现 user_question 入口节点
  - [ ] 7.1 在 agent/langgraph/nodes/user_question.py 创建节点函数
  - [ ] 7.2 接收用户输入，初始化 AgentState
  - [ ] 7.3 设置 trace_id、初始 retry_count=0
  - [ ] 7.4 编写单元测试

- [ ] Task 8: 实现 intent_router 意图路由节点
  - [ ] 8.1 在 agent/langgraph/nodes/intent_router.py 创建节点函数
  - [ ] 8.2 实现关键词匹配逻辑（聚合词、精确实体、概念词、混合意图）
  - [ ] 8.3 设置 route_target 字段（rag/database/hybrid/chitchat）
  - [ ] 8.4 编写单元测试验证各种路由场景

- [ ] Task 9: 实现 rag_tool 节点
  - [ ] 9.1 在 agent/langgraph/nodes/rag_tool_node.py 创建节点函数
  - [ ] 9.2 调用 RAGTool.invoke()
  - [ ] 9.3 将结果写入 AgentState（rag_docs、rag_quality_score 等）
  - [ ] 9.4 记录节点耗时到 node_timings
  - [ ] 9.5 编写单元测试

- [ ] Task 10: 实现 db_tool 节点
  - [ ] 10.1 在 agent/langgraph/nodes/db_tool_node.py 创建节点函数
  - [ ] 10.2 调用 DatabaseTool.invoke()
  - [ ] 10.3 将结果写入 AgentState（db_result、db_quality_score）
  - [ ] 10.4 记录节点耗时到 node_timings
  - [ ] 10.5 编写单元测试

- [ ] Task 11: 实现 web_tool 节点
  - [ ] 11.1 在 agent/langgraph/nodes/web_tool_node.py 创建节点函数
  - [ ] 11.2 调用 WebTool.invoke()
  - [ ] 11.3 将结果写入 AgentState（web_docs）
  - [ ] 11.4 记录节点耗时到 node_timings
  - [ ] 11.5 编写单元测试

- [ ] Task 12: 实现 quality_check 质量检查节点
  - [ ] 12.1 在 agent/langgraph/nodes/quality_check.py 创建节点函数
  - [ ] 12.2 实现质量评分判断逻辑（>= 0.7 通过）
  - [ ] 12.3 实现重试决策逻辑（retry_count < max_retries）
  - [ ] 12.4 实现降级决策逻辑（配额用尽 → web_tool）
  - [ ] 12.5 返回路由决策（pass/retry_rag/retry_db/fallback_web）
  - [ ] 12.6 编写单元测试验证各种决策场景

- [ ] Task 13: 实现 prompt_assembly Prompt 组装节点
  - [ ] 13.1 在 agent/langgraph/nodes/prompt_assembly.py 创建节点函数
  - [ ] 13.2 整合 rag_docs、db_result、web_docs 到 merged_context
  - [ ] 13.3 根据 query_lang 注入语言输出指令
  - [ ] 13.4 调用现有 PromptBuilder 构建最终 Prompt
  - [ ] 13.5 编写单元测试验证多源融合

- [ ] Task 14: 实现 llm_generate LLM 生成节点
  - [ ] 14.1 在 agent/langgraph/nodes/llm_generate.py 创建节点函数
  - [ ] 14.2 调用现有 LLM 组件生成答案
  - [ ] 14.3 支持流式输出
  - [ ] 14.4 将结果写入 generated_answer 字段
  - [ ] 14.5 记录节点耗时到 node_timings
  - [ ] 14.6 编写单元测试

- [ ] Task 15: 实现 hallucination 幻觉检测节点
  - [ ] 15.1 在 agent/langgraph/nodes/hallucination.py 创建节点函数
  - [ ] 15.2 调用现有 HallucinationDetector 进行检测
  - [ ] 15.3 实现分级处置逻辑（pass/filter/regenerate/reject）
  - [ ] 15.4 将结果写入 hallucination_score 和 hallucination_action
  - [ ] 15.5 编写单元测试验证各种处置场景

- [ ] Task 16: 实现 observability 可观测性节点
  - [ ] 16.1 在 agent/langgraph/nodes/observability.py 创建节点函数
  - [ ] 16.2 调用现有 Metrics 组件记录全局指标
  - [ ] 16.3 调用现有 StructuredLogger 记录结构化日志
  - [ ] 16.4 记录 e2e_latency、各节点耗时、工具调用次数
  - [ ] 16.5 编写单元测试

- [ ] Task 17: 实现 final_answer 最终答案节点
  - [ ] 17.1 在 agent/langgraph/nodes/final_answer.py 创建节点函数
  - [ ] 17.2 返回最终答案给用户
  - [ ] 17.3 支持流式输出
  - [ ] 17.4 编写单元测试

## Phase 4: 状态图构建

- [ ] Task 18: 构建 LangGraph 状态图
  - [ ] 18.1 在 agent/langgraph/graph.py 创建 build_agent_graph() 函数
  - [ ] 18.2 添加所有节点到 StateGraph
  - [ ] 18.3 设置入口点为 user_question
  - [ ] 18.4 添加 user_question → intent_router 边
  - [ ] 18.5 添加 intent_router 条件边（route_decision）
  - [ ] 18.6 添加 rag_tool → quality_check 边
  - [ ] 18.7 添加 db_tool → quality_check 边
  - [ ] 18.8 添加 web_tool → prompt_assembly 边
  - [ ] 18.9 添加 quality_check 条件边（retry_decision）
  - [ ] 18.10 添加 prompt_assembly → llm_generate 边
  - [ ] 18.11 添加 llm_generate → hallucination 边
  - [ ] 18.12 添加 hallucination 条件边（hallucination_decision）
  - [ ] 18.13 添加 observability → final_answer 边
  - [ ] 18.14 添加 final_answer → END 边
  - [ ] 18.15 实现 route_decision、retry_decision、hallucination_decision 函数
  - [ ] 18.16 编写单元测试验证状态图构建

## Phase 5: 配置与集成

- [ ] Task 19: 添加双轨运行配置
  - [ ] 19.1 在 api/settings.py 中添加 RAGFLOW_USE_LANGGRAPH 配置项
  - [ ] 19.2 在 docker/.env* 文件中添加配置项（默认 false）
  - [ ] 19.3 编写配置加载测试

- [ ] Task 20: 修改对话入口支持 LangGraph
  - [ ] 20.1 在 api/apps/conversation_app.py 中添加 LangGraph 对话处理函数
  - [ ] 20.2 根据 RAGFLOW_USE_LANGGRAPH 配置选择 Canvas 或 LangGraph 模式
  - [ ] 20.3 保持 API 接口兼容
  - [ ] 20.4 编写集成测试验证双轨运行

- [ ] Task 21: 实现 LangGraph 运行器
  - [ ] 21.1 在 agent/langgraph/runner.py 创建 LangGraphRunner 类
  - [ ] 21.2 实现 run() 方法，编译并执行状态图
  - [ ] 21.3 支持流式输出
  - [ ] 21.4 支持状态查询（get_state()）
  - [ ] 21.5 编写集成测试

## Phase 6: 测试与验证

- [ ] Task 22: 端到端测试
  - [ ] 22.1 编写 RAG 检索端到端测试
  - [ ] 22.2 编写 Database 查询端到端测试
  - [ ] 22.3 编写混合模式端到端测试
  - [ ] 22.4 编写 Web 搜索降级端到端测试
  - [ ] 22.5 编写幻觉检测端到端测试

- [ ] Task 23: 性能测试
  - [ ] 23.1 对比 Canvas 模式和 LangGraph 模式的 P95 延迟
  - [ ] 23.2 优化节点执行性能
  - [ ] 23.3 验证性能指标达标（简单查询 ≤ 5s，复杂查询 ≤ 15s）

- [ ] Task 24: 回归测试
  - [ ] 24.1 验证 Canvas 模式不受影响
  - [ ] 24.2 验证所有现有功能在 LangGraph 模式下正常工作
  - [ ] 24.3 修复发现的回归问题

## Task Dependencies

- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4, 5, 6 depends on Task 3
- Task 7, 8 depends on Task 3
- Task 9 depends on Task 4, Task 7
- Task 10 depends on Task 5, Task 7
- Task 11 depends on Task 6, Task 7
- Task 12 depends on Task 9, Task 10
- Task 13 depends on Task 9, Task 10, Task 11
- Task 14 depends on Task 13
- Task 15 depends on Task 14
- Task 16 depends on Task 15
- Task 17 depends on Task 16
- Task 18 depends on Task 7-17
- Task 19 depends on Task 18
- Task 20 depends on Task 19
- Task 21 depends on Task 20
- Task 22 depends on Task 21
- Task 23 depends on Task 22
- Task 24 depends on Task 22
