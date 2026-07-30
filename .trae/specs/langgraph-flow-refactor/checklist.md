# LangGraph 流程编排重构验证清单

## 基础设施验证

- [ ] LangGraph 依赖正确安装（langgraph、langchain-core、langchain）
- [ ] agent/langgraph/ 目录结构正确创建
- [ ] AgentState TypedDict 定义完整，包含所有必需字段

## 工具封装验证

- [ ] RAGTool 能够独立运行，返回结构化输出（docs、quality_score、has_relevant、relevant_count、top_score、rewrite_history）
- [ ] RAGTool 内部流程保持原有逻辑（查询预处理 → 查询重写 → 混合检索 → Rerank → 质量评估 → 内部重试）
- [ ] RAGTool 内部重试逻辑正确（最多 1 次内部重写重搜）
- [ ] DatabaseTool 能够独立运行，返回结构化输出（sql、rows、row_count、source、quality_score、execution_time_ms）
- [ ] DatabaseTool 内部流程保持原有逻辑（意图路由 → 模板匹配 → Schema 发现 → SQL 生成 → 安全检查 → 执行查询 → 结果格式化）
- [ ] WebTool 能够独立运行，返回结构化输出（docs、urls）

## LangGraph 节点验证

- [ ] user_question 节点正确初始化 AgentState
- [ ] intent_router 节点正确路由到不同工具（rag/database/hybrid/chitchat）
- [ ] rag_tool 节点正确调用 RAGTool 并更新状态
- [ ] db_tool 节点正确调用 DatabaseTool 并更新状态
- [ ] web_tool 节点正确调用 WebTool 并更新状态
- [ ] quality_check 节点正确决策（pass/retry_rag/retry_db/fallback_web）
- [ ] quality_check 节点重试逻辑正确（retry_count < max_retries）
- [ ] prompt_assembly 节点正确融合多源结果（rag_docs、db_result、web_docs）
- [ ] prompt_assembly 节点根据 query_lang 注入语言输出指令
- [ ] llm_generate 节点正确调用 LLM 生成答案
- [ ] llm_generate 节点支持流式输出
- [ ] hallucination 节点正确检测幻觉并决策（pass/filter/regenerate/reject）
- [ ] hallucination 节点分级处置逻辑正确（≥0.85 pass、0.6-0.85 filter、0.3-0.6 regenerate、<0.3 reject）
- [ ] observability 节点正确记录全局指标和结构化日志
- [ ] final_answer 节点正确返回最终答案

## 状态图验证

- [ ] build_agent_graph() 正确构建状态图
- [ ] 所有节点正确添加到状态图
- [ ] 条件路由正确配置（intent_router、quality_check、hallucination）
- [ ] 状态图能够正确编译和执行

## 配置与集成验证

- [ ] RAGFLOW_USE_LANGGRAPH 配置项正确添加到 settings.py
- [ ] 配置项在 docker/.env* 文件中正确设置（默认 false）
- [ ] conversation_app.py 支持双轨运行（Canvas 和 LangGraph）
- [ ] LangGraph 模式能够正确处理对话请求
- [ ] Canvas 模式不受影响，功能正常

## 端到端测试验证

- [ ] RAG 检索端到端测试通过
- [ ] Database 查询端到端测试通过
- [ ] 混合模式端到端测试通过
- [ ] Web 搜索降级端到端测试通过
- [ ] 幻觉检测端到端测试通过

## 性能验证

- [ ] LangGraph 模式 P95 延迟 ≤ 5s（简单查询）
- [ ] LangGraph 模式 P95 延迟 ≤ 15s（复杂查询）
- [ ] 性能与 Canvas 模式持平或更优

## 回归测试验证

- [ ] Canvas 模式所有功能正常
- [ ] LangGraph 模式所有功能正常
- [ ] 无回归问题

## 代码质量验证

- [ ] 所有代码通过 ruff check
- [ ] 所有代码通过 ruff format
- [ ] 所有单元测试通过
- [ ] 代码注释完整，逻辑清晰

## 文档验证

- [ ] spec.md 文档完整，描述清晰
- [ ] tasks.md 任务清单完整，依赖关系正确
- [ ] checklist.md 验证清单完整，覆盖所有关键功能
- [ ] 代码中的文档字符串完整
