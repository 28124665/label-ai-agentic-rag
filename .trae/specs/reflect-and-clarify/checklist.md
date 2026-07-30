# Checklist

## 反思功能验证
- [x] AgentState 包含 reflection_result 字段
- [x] api/utils/reflection.py 提供 build_reflection_prompt、call_reflection_llm、parse_reflection_result
- [x] rag/prompts/reflection_v2.md 存在且包含结构化输出指令
- [x] agent/langgraph/nodes/reflection.py 存在且实现反思逻辑
- [x] graph.py 包含 reflection 节点
- [x] graph.py 中 tool 节点边指向 reflection，reflection 指向 quality_check
- [x] 反思 LLM 超时或失败时降级为空反思，不阻塞主流程
- [x] agent/component/agent_with_tools.py 中恢复 reflect_async 调用
- [x] AgentParam 包含 enable_reflection 参数
- [x] Canvas ReAct 反思有超时降级到 build_observation

## 澄清功能验证
- [x] AgentState 包含 clarification_request 和 clarification_context 字段
- [x] agent/langgraph/nodes/clarification.py 存在且使用 interrupt 机制
- [x] graph.py 包含 clarification 节点
- [x] graph.py 中 intent_router 有条件边到 clarification
- [x] graph.py 中 clarification 边回到 intent_router
- [x] intent_router.py 的 route_decision 检查 needs_clarification
- [x] runner.py 包含 arun_with_checkpointer 方法
- [x] runner.py 包含 aresume 方法
- [x] conversation_app.py 包含 /clarify 端点

## 流程完整性验证
- [x] LangGraph 图能正常编译（build_and_compile_agent_graph 不报错）
- [x] 无 needs_clarification 时正常路由到工具节点
- [x] 工具执行后经过 reflection 再进入 quality_check
- [x] 现有 Canvas ReAct 流程不受影响（enable_reflection=False 时行为不变）
