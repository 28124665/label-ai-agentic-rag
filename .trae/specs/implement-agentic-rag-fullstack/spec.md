# Agentic RAG 全栈实现规范

## Why

基于已完成的 [前端设计文档](docs/frontend_design.md) 和 [后端接口设计文档](docs/backend_api_design.md)，需要实现全新的 Agentic RAG 系统前后端代码。新系统采用 Next.js 14 + FastAPI + LangGraph 架构，将 RAGFlow 作为知识库工具调用，实现更灵活的 Agent 编排和更好的用户体验。

## What Changes

### 新增功能

- **新前端（Next.js 14）**
  - 对话界面：支持流式响应、工具调用可视化、引用来源展示
  - 知识库管理：代理 RAGFlow API，提供知识库 CRUD 和检索测试
  - 数据源管理：数据库连接管理、Schema 查看、SQL 执行
  - Agent 配置：工具选择、路由策略、降级策略配置
  - 监控中心：实时仪表盘、质量监控、成本监控
  - 管理后台：用户管理、权限配置、审计日志

- **新后端（FastAPI）**
  - 认证模块：用户登录、注册、Token 管理
  - 对话模块：对话管理、消息发送（SSE 流式）
  - 知识库模块：代理 RAGFlow API
  - 数据源模块：数据库连接管理、SQL 生成与执行
  - Agent 模块：Agent 配置管理
  - 监控模块：指标采集、日志查询
  - RAGFlow 客户端：封装 RAGFlow API 调用

- **LangGraph 集成**
  - 状态图编排：intent_router → rag_tool/db_tool → quality_check → prompt_assembly → llm_generate → hallucination
  - 工具封装：RAGTool、DatabaseTool、WebTool
  - 降级策略：RAG → Database → Web 多级降级

### 与 RAGFlow 的交互

- **知识库管理**：调用 RAGFlow API（`/api/v1/datasets`）
- **模型配置**：同步到 RAGFlow（`/api/v1/llm/set`）
- **检索能力**：调用 RAGFlow 检索 API（`/api/v1/datasets/{id}/retrieval`）
- **数据隔离**：新后端存储对话历史、审计日志；RAGFlow 存储知识库、文档

## Impact

### Affected specs
- `langgraph-flow-refactor`：LangGraph 状态图已部分实现，需要集成到新后端
- `implement-database-tool`：Database Tool 已实现，需要封装为 LangGraph 工具
- `implement-phase2-rag-enhancements`：RAG 增强功能已实现，需要封装为 RAGTool

### Affected code
- **新后端**：`api/v1/` 目录（FastAPI 路由）
- **新前端**：`web-new/` 目录（Next.js 应用）
- **共享模块**：`agent/langgraph/`（LangGraph 状态图）
- **配置**：`config/settings.py`（环境变量）

## ADDED Requirements

### Requirement: 新前端对话界面

系统 SHALL 提供基于 Next.js 14 的对话界面，支持流式响应、工具调用可视化、引用来源展示。

#### Scenario: 用户发送消息并查看流式响应

- **WHEN** 用户在对话界面输入问题并发送
- **THEN** 前端通过 SSE 接收流式响应
- **AND** 实时显示"思考中"、"工具调用"、"生成答案"等状态
- **AND** 展示工具调用卡片（SQL/检索/Web）
- **AND** 展示引用来源（文档/数据库/Web）

#### Scenario: 用户查看对话历史

- **WHEN** 用户点击左侧对话列表
- **THEN** 前端加载该对话的历史消息
- **AND** 展示每条消息的工具调用和引用信息

### Requirement: 新后端对话 API

系统 SHALL 提供 FastAPI 对话 API，支持对话管理、消息发送（SSE 流式）。

#### Scenario: 创建对话

- **WHEN** 前端调用 `POST /api/v1/conversations`
- **THEN** 后端创建对话记录并返回对话 ID
- **AND** 对话关联到当前用户和指定 Agent

#### Scenario: 发送消息（流式）

- **WHEN** 前端调用 `POST /api/v1/conversations/{id}/messages`
- **THEN** 后端通过 SSE 流式返回响应
- **AND** 响应包含 thinking、tool_call、tool_result、text、reference、done 等事件
- **AND** 后端调用 LangGraph 状态图执行

### Requirement: 知识库代理 API

系统 SHALL 提供知识库代理 API，调用 RAGFlow API 实现知识库管理。

#### Scenario: 获取知识库列表

- **WHEN** 前端调用 `GET /api/v1/knowledge`
- **THEN** 后端调用 RAGFlow API `GET /api/v1/datasets`
- **AND** 转换响应格式并返回给前端

#### Scenario: 创建知识库

- **WHEN** 前端调用 `POST /api/v1/knowledge`
- **THEN** 后端调用 RAGFlow API `POST /api/v1/datasets`
- **AND** 返回创建的知识库信息

### Requirement: 数据源管理

系统 SHALL 提供数据源管理功能，支持数据库连接、Schema 查看、SQL 执行。

#### Scenario: 创建数据源

- **WHEN** 前端调用 `POST /api/v1/datasources`
- **THEN** 后端测试数据库连接
- **AND** 加密存储密码
- **AND** 保存数据源配置

#### Scenario: 执行 SQL

- **WHEN** 前端调用 `POST /api/v1/datasources/{id}/query`
- **THEN** 后端执行 SQL 查询
- **AND** 返回查询结果（列名、行数据、执行时间）

### Requirement: LangGraph 状态图集成

系统 SHALL 集成 LangGraph 状态图，实现 Agent 编排。

#### Scenario: 执行 RAG 检索流程

- **WHEN** 用户问题路由到 RAG Tool
- **THEN** LangGraph 执行 rag_tool 节点
- **AND** 调用 RAGTool.invoke() 进行检索
- **AND** 执行 quality_check 节点评估质量
- **AND** 质量达标则进入 prompt_assembly

#### Scenario: 执行降级流程

- **WHEN** RAG 检索质量不达标且重试次数用尽
- **THEN** LangGraph 降级到 Database Tool 或 Web Tool
- **AND** 继续执行后续节点

### Requirement: 模型配置同步

系统 SHALL 支持模型配置同步到 RAGFlow。

#### Scenario: 配置 LLM 模型

- **WHEN** 前端调用 `POST /api/v1/models`
- **THEN** 后端验证 API Key
- **AND** 保存配置到新后端数据库
- **AND** 调用 RAGFlow API 同步配置

## MODIFIED Requirements

### Requirement: 用户认证

系统 SHALL 提供独立的用户认证，与 RAGFlow 认证解耦。

#### Scenario: 用户登录

- **WHEN** 用户提交用户名和密码
- **THEN** 后端验证用户凭证
- **AND** 生成 JWT Token
- **AND** 返回 Token 和用户信息

## REMOVED Requirements

无移除需求。新系统与 RAGFlow 并行运行，不影响现有功能。
