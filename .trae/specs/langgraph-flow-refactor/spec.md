# LangGraph 流程编排重构 Spec

## Why

当前 RAGFlow 的所有流程节点（查询重写、检索、评估、重试、生成、幻觉检测）都在 Canvas 中硬编码编排，导致：
1. 难以扩展新工具（Database Tool、Web Tool 等）
2. 重试逻辑混乱（内部重试和外部降级耦合）
3. 状态传递复杂（通过 Canvas 的 set_state/get_variable_value）
4. 工具封装不足（内部逻辑泄露）

引入 LangGraph 实现全局流程编排，将 RAGFlow 作为"知识库工具"被 LangGraph Agent 调用。

## What Changes

- **新增** `agent/langgraph/` 目录，包含 LangGraph 全局编排代码
- **新增** `AgentState` 统一状态定义
- **新增** LangGraph 节点实现（意图路由、质量检查、Prompt 组装、LLM 生成、幻觉检测、可观测性、最终答案）
- **新增** `RAGTool` 封装类，将现有 RAG 流程封装为独立工具
- **新增** `DatabaseTool` 封装类，将现有数据库工具封装为独立工具
- **新增** `WebTool` 封装类，将 Web 搜索封装为独立工具
- **修改** 流程入口，支持通过配置切换 Canvas 模式和 LangGraph 模式
- **保留** 所有现有组件的内部实现逻辑不变（QueryRewriter、Grader、RetryController、HallucinationDetector 等）

## Impact

- Affected specs: implement-phase2-rag-enhancements, implement-database-tool
- Affected code:
  - `agent/langgraph/` (新增)
  - `agent/tools/` (新增封装层)
  - `agent/component/` (保留原有实现，新增适配层)
  - `api/apps/conversation_app.py` (新增 LangGraph 入口)

## ADDED Requirements

### Requirement: LangGraph 基础设施
The system SHALL provide LangGraph 基础运行环境，包括依赖安装、状态定义、图构建框架。

#### Scenario: LangGraph 环境初始化
- **WHEN** 系统启动时加载 LangGraph 模块
- **THEN** 能够成功导入 langgraph、langchain_core 依赖
- **AND** AgentState 状态类正确定义

### Requirement: AgentState 统一状态
The system SHALL define a unified AgentState TypedDict that contains all fields needed for the global orchestration flow.

#### Scenario: 状态初始化
- **WHEN** LangGraph 流程启动
- **THEN** AgentState 包含 user_question、query_lang、route_target 等字段
- **AND** 各工具输出字段（rag_docs、db_result、web_docs）初始化为空

### Requirement: RAG Tool 封装
The system SHALL encapsulate the existing RAG retrieval flow (preprocessing → optimization → retrieval → rerank → grading → internal retry) into a standalone RAGTool class.

#### Scenario: RAG Tool 独立调用
- **WHEN** LangGraph 调用 RAGTool.invoke(query="测试查询", query_lang="zh_CN")
- **THEN** RAGTool 内部执行完整的检索流程
- **AND** 返回 {docs, quality_score, has_relevant, relevant_count, top_score, rewrite_history}
- **AND** 内部重试逻辑保持不变（最多 1 次内部重写重搜）

### Requirement: Database Tool 封装
The system SHALL encapsulate the existing database tools into a standalone DatabaseTool class.

#### Scenario: Database Tool 独立调用
- **WHEN** LangGraph 调用 DatabaseTool.invoke(query="查询库存", query_lang="zh_CN")
- **THEN** DatabaseTool 内部执行意图路由、Schema 发现、SQL 生成、执行查询
- **AND** 返回 {sql, rows, row_count, source, quality_score, execution_time_ms}

### Requirement: Web Tool 封装
The system SHALL encapsulate web search functionality into a standalone WebTool class.

#### Scenario: Web Tool 独立调用
- **WHEN** LangGraph 调用 WebTool.invoke(query="最新新闻", query_lang="zh_CN")
- **THEN** WebTool 执行 Web 搜索
- **AND** 返回 {docs, urls}

### Requirement: LangGraph 意图路由节点
The system SHALL implement an intent_router node that decides which tool to invoke based on query characteristics.

#### Scenario: 路由到 RAG Tool
- **WHEN** 用户查询包含概念/解释类关键词（"原因"、"如何"、"为什么"）
- **THEN** route_target 设置为 "rag"

#### Scenario: 路由到 Database Tool
- **WHEN** 用户查询包含数字/统计/聚合词（"多少"、"总计"、"平均"）或精确实体/ID
- **THEN** route_target 设置为 "database"

#### Scenario: 路由到混合模式
- **WHEN** 用户查询同时包含实体和概念
- **THEN** route_target 设置为 "hybrid"

#### Scenario: 路由到闲聊
- **WHEN** 用户查询为闲聊/问候
- **THEN** route_target 设置为 "chitchat"

### Requirement: LangGraph 质量检查节点
The system SHALL implement a quality_check node that decides whether to retry or proceed based on tool output quality.

#### Scenario: 质量合格
- **WHEN** quality_score >= 0.7
- **THEN** 流程进入 prompt_assembly 节点

#### Scenario: 质量不合格且可重试
- **WHEN** quality_score < 0.7 且 retry_count < max_retries
- **THEN** retry_count 加 1，流程回到对应工具节点

#### Scenario: 质量不合格且配额用尽
- **WHEN** quality_score < 0.7 且 retry_count >= max_retries
- **THEN** 流程降级到 web_tool 节点

### Requirement: LangGraph Prompt 组装节点
The system SHALL implement a prompt_assembly node that merges results from multiple tools into a unified context.

#### Scenario: 多源结果融合
- **WHEN** 存在 rag_docs、db_result、web_docs 中的一个或多个
- **THEN** 将所有结果按格式组装为 merged_context
- **AND** 根据 query_lang 注入语言输出指令

### Requirement: LangGraph LLM 生成节点
The system SHALL implement an llm_generate node that calls the LLM model to generate answers.

#### Scenario: LLM 生成答案
- **WHEN** 接收到 merged_context 和 user_question
- **THEN** 调用 LLM 模型生成答案
- **AND** 支持流式输出
- **AND** 将结果存入 generated_answer 字段

### Requirement: LangGraph 幻觉检测节点
The system SHALL implement a hallucination node that validates the generated answer against the context.

#### Scenario: 幻觉检测通过
- **WHEN** hallucination_score >= 0.85
- **THEN** hallucination_action 设置为 "pass"，流程进入 observability

#### Scenario: 幻觉检测需要过滤
- **WHEN** 0.6 <= hallucination_score < 0.85
- **THEN** hallucination_action 设置为 "filter"，过滤不支持的论断

#### Scenario: 幻觉检测需要重新生成
- **WHEN** 0.3 <= hallucination_score < 0.6
- **THEN** hallucination_action 设置为 "regenerate"，流程回到 llm_generate

#### Scenario: 幻觉检测严重失败
- **WHEN** hallucination_score < 0.3
- **THEN** hallucination_action 设置为 "reject"，直接拒答

### Requirement: LangGraph 可观测性节点
The system SHALL implement an observability node that records global metrics and audit logs.

#### Scenario: 记录全局指标
- **WHEN** 流程执行完成
- **THEN** 记录 e2e_latency、各节点耗时、工具调用次数等指标
- **AND** 记录结构化日志

### Requirement: LangGraph 状态图构建
The system SHALL build a complete StateGraph with all nodes and conditional edges.

#### Scenario: 状态图正确构建
- **WHEN** 调用 build_agent_graph()
- **THEN** 返回包含所有节点的 StateGraph
- **AND** 条件路由正确配置（intent_router → 工具选择、quality_check → 重试/降级、hallucination → 处置）

### Requirement: 双轨运行支持
The system SHALL support running both Canvas mode and LangGraph mode, controlled by configuration.

#### Scenario: 切换到 LangGraph 模式
- **WHEN** 环境变量 RAGFLOW_USE_LANGGRAPH=true
- **THEN** 对话流程使用 LangGraph 编排
- **AND** 原有 Canvas 流程保持不变

#### Scenario: 保持 Canvas 模式
- **WHEN** 环境变量 RAGFLOW_USE_LANGGRAPH=false 或未设置
- **THEN** 对话流程使用原有 Canvas 编排

## MODIFIED Requirements

### Requirement: 对话入口适配
The conversation_app.py SHALL support both Canvas and LangGraph entry points.

[Complete modified requirement]
- 新增 LangGraph 对话处理函数
- 根据配置选择 Canvas 或 LangGraph 模式
- 保持 API 接口兼容

## REMOVED Requirements

无移除。原有 Canvas 流程保留，新增 LangGraph 流程作为可选替代。
