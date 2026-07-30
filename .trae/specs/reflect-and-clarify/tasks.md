# Tasks

- [x] Task 1: 扩展 AgentState 状态定义，新增反思和澄清相关字段
  - [x] SubTask 1.1: 在 state.py 中新增 reflection_result 字段
  - [x] SubTask 1.2: 在 state.py 中新增 clarification_request 和 clarification_context 字段

- [x] Task 2: 创建反思共享模块 api/utils/reflection.py
  - [x] SubTask 2.1: 实现 build_reflection_prompt() 构建 Prompt
  - [x] SubTask 2.2: 实现 call_reflection_llm() 调用 LLM（带超时）
  - [x] SubTask 2.3: 实现 parse_reflection_result() 解析结果
  - [x] SubTask 2.4: 实现 fallback_reflection() 降级逻辑

- [x] Task 3: 创建反思 Prompt 模板 rag/prompts/reflection_v2.md
  - [x] SubTask 3.1: 编写结构化反思 Prompt（输出 JSON）

- [x] Task 4: 实现 LangGraph reflection_node
  - [x] SubTask 4.1: 创建 agent/langgraph/nodes/reflection.py
  - [x] SubTask 4.2: 实现工具结果收集逻辑
  - [x] SubTask 4.3: 实现反思执行和降级

- [x] Task 5: 实现 LangGraph clarification_node
  - [x] SubTask 5.1: 创建 agent/langgraph/nodes/clarification.py
  - [x] SubTask 5.2: 使用 langgraph interrupt 机制实现中断/恢复

- [x] Task 6: 修改 LangGraph 图结构 graph.py
  - [x] SubTask 6.1: 新增 reflection 和 clarification 节点
  - [x] SubTask 6.2: 调整边连接（tool→reflection→quality_check, intent_router→clarification→intent_router）

- [x] Task 7: 修改 intent_router route_decision 函数
  - [x] SubTask 7.1: 新增 clarification 路由分支

- [x] Task 8: 扩展 LangGraphRunner 支持 checkpointer
  - [x] SubTask 8.1: 新增 arun_with_checkpointer 方法
  - [x] SubTask 8.2: 新增 aresume 方法

- [x] Task 9: 恢复 Canvas ReAct 的 reflect_async
  - [x] SubTask 9.1: AgentParam 新增 enable_reflection 参数
  - [x] SubTask 9.2: 在 _react_with_tools_streamly_async_simple 中恢复 reflect_async 调用
  - [x] SubTask 9.3: 添加超时降级逻辑

- [x] Task 10: 新增 /clarify API 端点
  - [x] SubTask 10.1: 在 conversation_app.py 新增 clarify 接口
  - [x] SubTask 10.2: 新增 /langgraph_completion 入口端点

- [x] Task 11: 编写单元测试
  - [x] SubTask 11.1: reflection_node 测试（11 个用例全部通过）
  - [x] SubTask 11.2: clarification_node 测试（4 个用例全部通过）

# Task Dependencies
- Task 4 depends on Task 1, Task 2, Task 3
- Task 5 depends on Task 1
- Task 6 depends on Task 4, Task 5
- Task 7 depends on Task 5
- Task 8 depends on Task 6
- Task 9 depends on Task 2
- Task 10 depends on Task 8
- Task 11 depends on Task 4, Task 5, Task 9
