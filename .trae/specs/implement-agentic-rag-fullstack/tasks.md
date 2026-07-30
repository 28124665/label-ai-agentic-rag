# Agentic RAG 全栈实现任务清单

## Phase 1: 新后端基础设施（FastAPI）

- [x] Task 1: 创建新后端项目结构
  - [x] 1.1 创建 `api/v1/` 目录结构
  - [x] 1.2 创建 `api/v1/main.py`（FastAPI 应用入口）
  - [x] 1.3 创建 `api/v1/core/config.py`（配置管理）
  - [x] 1.4 创建 `api/v1/core/database.py`（数据库连接）
  - [x] 1.5 创建 `api/v1/core/auth.py`（认证依赖）
  - [x] 1.6 创建 `api/v1/core/ragflow_client.py`（RAGFlow 客户端）

- [x] Task 2: 实现数据库模型
  - [x] 2.1 创建 `api/v1/models/user.py`（用户模型）
  - [x] 2.2 创建 `api/v1/models/conversation.py`（对话模型）
  - [x] 2.3 创建 `api/v1/models/message.py`（消息模型）
  - [x] 2.4 创建 `api/v1/models/datasource.py`（数据源模型）
  - [x] 2.5 创建 `api/v1/models/agent.py`（Agent 模型）
  - [x] 2.6 创建 `api/v1/models/audit_log.py`（审计日志模型）
  - [x] 2.7 创建 `api/v1/models/model_config.py`（模型配置模型）

- [x] Task 3: 实现认证模块
  - [x] 3.1 创建 `api/v1/schemas/auth.py`（认证 Schema）
  - [x] 3.2 创建 `api/v1/services/auth_service.py`（认证服务）
  - [x] 3.3 创建 `api/v1/routes/auth.py`（认证路由）
  - [x] 3.4 实现用户登录接口 `POST /api/v1/auth/login`
  - [x] 3.5 实现用户注册接口 `POST /api/v1/auth/register`
  - [x] 3.6 实现获取当前用户接口 `GET /api/v1/auth/me`

- [x] Task 4: 实现 RAGFlow 客户端
  - [x] 4.1 创建 `api/v1/core/ragflow_client.py`
  - [x] 4.2 实现知识库管理方法（list_datasets, create_dataset, update_dataset, delete_dataset）
  - [x] 4.3 实现文档管理方法（list_documents, upload_document, delete_document）
  - [x] 4.4 实现检索方法（retrieval）
  - [x] 4.5 实现模型管理方法（list_llm, set_llm）

- [x] Task 5: 实现对话模块
  - [x] 5.1 创建 `api/v1/schemas/conversation.py`（对话 Schema）
  - [x] 5.2 创建 `api/v1/schemas/message.py`（消息 Schema）
  - [x] 5.3 创建 `api/v1/services/conversation_service.py`（对话服务）
  - [x] 5.4 创建 `api/v1/routes/conversations.py`（对话路由）
  - [x] 5.5 实现创建对话接口 `POST /api/v1/conversations`
  - [x] 5.6 实现获取对话列表接口 `GET /api/v1/conversations`
  - [x] 5.7 实现发送消息接口 `POST /api/v1/conversations/{id}/messages`（SSE 流式）
  - [x] 5.8 实现获取对话历史接口 `GET /api/v1/conversations/{id}/messages`

- [x] Task 6: 实现知识库代理模块
  - [x] 6.1 创建 `api/v1/schemas/knowledge.py`（知识库 Schema）
  - [x] 6.2 创建 `api/v1/services/knowledge_service.py`（知识库服务）
  - [x] 6.3 创建 `api/v1/routes/knowledge.py`（知识库路由）
  - [x] 6.4 实现获取知识库列表接口 `GET /api/v1/knowledge/datasets`
  - [x] 6.5 实现创建知识库接口 `POST /api/v1/knowledge/datasets`
  - [x] 6.6 实现更新知识库接口 `PUT /api/v1/knowledge/datasets/{id}`
  - [x] 6.7 实现删除知识库接口 `DELETE /api/v1/knowledge/datasets/{id}`
  - [x] 6.8 实现上传文档接口 `POST /api/v1/knowledge/datasets/{id}/documents`
  - [x] 6.9 实现检索测试接口 `POST /api/v1/knowledge/datasets/{id}/retrieval`

- [x] Task 7: 实现数据源模块
  - [x] 7.1 创建 `api/v1/schemas/datasource.py`（数据源 Schema）
  - [x] 7.2 创建 `api/v1/services/datasource_service.py`（数据源服务）
  - [x] 7.3 创建 `api/v1/routes/datasources.py`（数据源路由）
  - [x] 7.4 实现创建数据源接口 `POST /api/v1/datasources`
  - [x] 7.5 实现获取数据源列表接口 `GET /api/v1/datasources`
  - [x] 7.6 实现获取表结构接口 `GET /api/v1/datasources/{id}/tables`
  - [x] 7.7 实现执行 SQL 接口 `POST /api/v1/datasources/{id}/execute`

- [x] Task 8: 实现 Agent 模块
  - [x] 8.1 创建 `api/v1/schemas/agent.py`（Agent Schema）
  - [x] 8.2 创建 `api/v1/services/agent_service.py`（Agent 服务）
  - [x] 8.3 创建 `api/v1/routes/agents.py`（Agent 路由）
  - [x] 8.4 实现创建 Agent 接口 `POST /api/v1/agents`
  - [x] 8.5 实现获取 Agent 列表接口 `GET /api/v1/agents`
  - [x] 8.6 实现更新 Agent 接口 `PUT /api/v1/agents/{id}`

- [x] Task 9: 集成 LangGraph 状态图
  - [x] 9.1 创建 `api/v1/core/langgraph_integration.py`（LangGraph 集成）
  - [x] 9.2 实现 AgentState 状态定义
  - [x] 9.3 实现状态图节点（intent_router, rag_retrieval, database_query, web_search, response_generator）
  - [x] 9.4 实现条件路由逻辑

- [x] Task 10: 实现模型配置同步
  - [x] 10.1 创建 `api/v1/schemas/model_config.py`（模型 Schema）
  - [x] 10.2 创建 `api/v1/services/model_config_service.py`（模型服务）
  - [x] 10.3 创建 `api/v1/routes/model_configs.py`（模型路由）
  - [x] 10.4 实现创建模型配置接口 `POST /api/v1/model-configs`
  - [x] 10.5 实现同步配置到 RAGFlow `POST /api/v1/model-configs/sync`

## Phase 2: 新前端实现（Next.js 14）

- [x] Task 11: 创建新前端项目结构
  - [x] 11.1 创建 `web-new/` 目录
  - [x] 11.2 初始化 Next.js 14 项目（App Router）
  - [x] 11.3 配置 Tailwind CSS + shadcn/ui
  - [x] 11.4 配置 TypeScript
  - [x] 11.5 创建项目目录结构（app/, components/, lib/, services/）

- [x] Task 12: 实现全局布局和组件
  - [x] 12.1 创建 `web-new/components/layout/top-nav-bar.tsx`（顶部导航栏）
  - [x] 12.2 创建 `web-new/components/layout/side-bar.tsx`（侧边栏）
  - [x] 12.3 创建 `web-new/components/layout/page-header.tsx`（页面标题）
  - [x] 12.4 创建 `web-new/components/ui/toast.tsx`（全局提示）
  - [x] 12.5 创建 `web-new/components/ui/modal.tsx`（全局弹窗）
  - [x] 12.6 创建 `web-new/components/ui/loading.tsx`（加载状态）

- [x] Task 13: 实现认证页面
  - [x] 13.1 创建 `web-new/app/(auth)/login/page.tsx`（登录页）
  - [x] 13.2 创建 `web-new/app/(auth)/register/page.tsx`（注册页）
  - [x] 13.3 创建 `web-new/services/auth.service.ts`（认证服务）
  - [x] 13.4 创建 `web-new/lib/auth.ts`（认证工具）
  - [x] 13.5 实现 Token 存储和自动刷新

- [x] Task 14: 实现对话界面
  - [x] 14.1 创建 `web-new/app/(main)/chat/page.tsx`（对话列表页）
  - [x] 14.2 创建 `web-new/app/(main)/chat/[id]/page.tsx`（对话详情页）
  - [x] 14.3 创建 `web-new/components/chat/conversation-list.tsx`（对话列表组件）
  - [x] 14.4 创建 `web-new/components/chat/message-item.tsx`（消息组件）
  - [x] 14.5 创建 `web-new/components/chat/tool-call-card.tsx`（工具调用卡片）
  - [x] 14.6 创建 `web-new/components/chat/reference-card.tsx`（引用来源卡片）
  - [x] 14.7 创建 `web-new/components/chat/message-input.tsx`（消息输入框）
  - [x] 14.8 创建 `web-new/services/conversation.service.ts`（对话服务）
  - [x] 14.9 实现 SSE 流式响应处理

- [x] Task 15: 实现知识库管理页面
  - [x] 15.1 创建 `web-new/app/(main)/knowledge/page.tsx`（知识库列表页）
  - [x] 15.2 创建 `web-new/app/(main)/knowledge/[id]/page.tsx`（知识库详情页）
  - [x] 15.3 创建 `web-new/components/knowledge/knowledge-card.tsx`（知识库卡片）
  - [x] 15.4 创建 `web-new/components/knowledge/document-list.tsx`（文档列表）
  - [x] 15.5 创建 `web-new/components/knowledge/upload-dialog.tsx`（上传弹窗）
  - [x] 15.6 创建 `web-new/components/knowledge/search-test.tsx`（检索测试）
  - [x] 15.7 创建 `web-new/services/knowledge.service.ts`（知识库服务）

- [x] Task 16: 实现数据源管理页面
  - [x] 16.1 创建 `web-new/app/(main)/datasource/page.tsx`（数据源列表页）
  - [x] 16.2 创建 `web-new/app/(main)/datasource/[id]/page.tsx`（数据源详情页）
  - [x] 16.3 创建 `web-new/components/datasource/datasource-card.tsx`（数据源卡片）
  - [x] 16.4 创建 `web-new/components/datasource/table-list.tsx`（表列表）
  - [x] 16.5 创建 `web-new/components/datasource/sql-editor.tsx`（SQL 编辑器）
  - [x] 16.6 创建 `web-new/services/datasource.service.ts`（数据源服务）

- [x] Task 17: 实现 Agent 配置页面
  - [x] 17.1 创建 `web-new/app/(main)/agent/page.tsx`（Agent 列表页）
  - [x] 17.2 创建 `web-new/app/(main)/agent/[id]/page.tsx`（Agent 配置页）
  - [x] 17.3 创建 `web-new/components/agent/agent-card.tsx`（Agent 卡片）
  - [x] 17.4 创建 `web-new/components/agent/tool-config.tsx`（工具配置）
  - [x] 17.5 创建 `web-new/components/agent/routing-strategy.tsx`（路由策略）
  - [x] 17.6 创建 `web-new/components/agent/workflow-visualizer.tsx`（工作流可视化）
  - [x] 17.7 创建 `web-new/services/agent.service.ts`（Agent 服务）

- [x] Task 18: 实现监控中心页面
  - [x] 18.1 创建 `web-new/app/(main)/monitor/page.tsx`（监控中心页）
  - [x] 18.2 创建 `web-new/components/monitor/dashboard.tsx`（实时仪表盘）
  - [x] 18.3 创建 `web-new/components/monitor/quality-chart.tsx`（质量趋势图）
  - [x] 18.4 创建 `web-new/components/monitor/cost-chart.tsx`（成本趋势图）
  - [x] 18.5 创建 `web-new/services/monitor.service.ts`（监控服务）

- [x] Task 19: 实现管理后台页面
  - [x] 19.1 创建 `web-new/app/(main)/admin/page.tsx`（管理后台页）
  - [x] 19.2 创建 `web-new/components/admin/user-list.tsx`（用户列表）
  - [x] 19.3 创建 `web-new/components/admin/audit-log.tsx`（审计日志）
  - [x] 19.4 创建 `web-new/services/admin.service.ts`（管理服务）

- [x] Task 20: 实现 API 服务层
  - [x] 20.1 创建 `web-new/services/api-client.ts`（API 客户端）
  - [x] 20.2 实现请求拦截器（Token 注入）
  - [x] 20.3 实现响应拦截器（错误处理）
  - [x] 20.4 实现 SSE 流式请求处理

## Phase 3: 前端问题修复

- [x] Task 21: 修复 Token 自动刷新逻辑
  - [x] 21.1 在 api-client.ts 响应拦截器中添加 401 时自动刷新 token 逻辑
  - [x] 21.2 实现刷新 token 失败时清除登录状态并跳转登录页

- [x] Task 22: 集成工具调用卡片和引用来源卡片到消息展示
  - [x] 22.1 修改 message-item.tsx，使用 ToolCallCard 组件替代 JSON.stringify 展示工具调用
  - [x] 22.2 修改 message-item.tsx，使用 ReferenceCard 组件替代 JSON.stringify 展示引用来源

- [x] Task 23: 实现消息输入框文件上传功能
  - [x] 23.1 在 message-input.tsx 中添加文件上传按钮和文件选择器
  - [x] 23.2 实现已选文件列表展示和移除功能
  - [x] 23.3 修改 conversation.service.ts 的 sendMessage 方法支持文件上传

- [x] Task 24: 修复监控中心幻觉率计算
  - [x] 24.1 在 MonitorMetrics 接口中添加 faithfulness_score 字段
  - [x] 24.2 修改 dashboard.tsx 使用 faithfulness_score 计算幻觉率

- [x] Task 25: 修复成本趋势图时间范围切换
  - [x] 25.1 修改 CostChart 组件，时间范围切换时通过回调通知父组件
  - [x] 25.2 修改 monitor/page.tsx，根据时间范围重新请求数据

- [x] Task 26: 优化工作流可视化组件布局
  - [x] 26.1 修改 workflow-visualizer.tsx，动态计算 SVG 宽度防止工具节点溢出

## Phase 4: 集成测试与优化

- [ ] Task 27: 后端单元测试
  - [ ] 27.1 编写认证模块测试
  - [ ] 27.2 编写对话模块测试
  - [ ] 27.3 编写知识库模块测试
  - [ ] 27.4 编写数据源模块测试
  - [ ] 27.5 编写 Agent 模块测试

- [ ] Task 28: 前端单元测试
  - [ ] 28.1 编写对话界面组件测试
  - [ ] 28.2 编写知识库管理组件测试
  - [ ] 28.3 编写数据源管理组件测试
  - [ ] 28.4 编写 Agent 配置组件测试

- [ ] Task 29: 端到端测试
  - [ ] 29.1 编写对话流程 E2E 测试
  - [ ] 29.2 编写知识库管理 E2E 测试
  - [ ] 29.3 编写数据源查询 E2E 测试
  - [ ] 29.4 编写 Agent 配置 E2E 测试

- [ ] Task 30: 性能优化
  - [ ] 30.1 优化 SSE 流式响应性能
  - [ ] 30.2 优化前端渲染性能
  - [ ] 30.3 优化数据库查询性能
  - [ ] 30.4 优化 RAGFlow API 调用性能

- [ ] Task 31: 部署配置
  - [ ] 31.1 创建 Docker Compose 配置
  - [ ] 31.2 创建 Nginx 配置
  - [ ] 31.3 创建环境变量配置
  - [ ] 31.4 编写部署文档

## Task Dependencies

- Task 2 依赖 Task 1（需要先创建项目结构）
- Task 3 依赖 Task 1, Task 2（需要项目结构和数据库模型）
- Task 4 依赖 Task 1（需要项目结构）
- Task 5 依赖 Task 2, Task 3, Task 4（需要数据库模型、认证、RAGFlow 客户端）
- Task 6 依赖 Task 4（需要 RAGFlow 客户端）
- Task 7 依赖 Task 2（需要数据库模型）
- Task 8 依赖 Task 2（需要数据库模型）
- Task 9 依赖 Task 5（需要对话模块）
- Task 10 依赖 Task 4（需要 RAGFlow 客户端）
- Task 12-20 依赖 Task 11（需要前端项目结构）
- Task 21 依赖 Task 3-10（需要后端模块完成）
- Task 22 依赖 Task 12-20（需要前端组件完成）
- Task 23 依赖 Task 21, Task 22（需要单元测试完成）
- Task 24 依赖 Task 23（需要 E2E 测试完成）
- Task 25 依赖 Task 24（需要性能优化完成）
