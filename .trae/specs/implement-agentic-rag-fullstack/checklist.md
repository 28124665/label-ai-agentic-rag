# Agentic RAG 全栈实现检查清单

## 后端检查点

- [ ] FastAPI 应用入口 `api/v1/main.py` 创建成功，包含 CORS、异常处理、路由注册
- [ ] 配置管理 `api/v1/core/config.py` 支持环境变量加载（DATABASE_URL、REDIS_URL、RAGFLOW_BASE_URL、RAGFLOW_API_KEY、JWT_SECRET_KEY）
- [ ] 数据库连接 `api/v1/core/database.py` 使用 SQLAlchemy AsyncSession
- [ ] 用户模型 `api/v1/models/user.py` 包含 id、username、email、password_hash、role、avatar、created_at、updated_at
- [ ] 对话模型 `api/v1/models/conversation.py` 包含 id、user_id、agent_id、title、created_at、updated_at
- [ ] 消息模型 `api/v1/models/message.py` 包含 id、conversation_id、role、content、files、tool_calls、references、created_at
- [ ] 数据源模型 `api/v1/models/datasource.py` 包含 id、user_id、name、type、host、port、database、username、password（加密）、whitelist_tables、status
- [ ] Agent 模型 `api/v1/models/agent.py` 包含 id、user_id、name、description、tools、routing_strategy、fallback_strategy、status
- [ ] 审计日志模型 `api/v1/models/audit_log.py` 包含 id、user_id、action、resource_type、resource_id、details、ip_address、user_agent、created_at
- [ ] 认证路由实现 `POST /api/v1/auth/login`、`POST /api/v1/auth/register`、`GET /api/v1/auth/me`
- [ ] JWT Token 生成和验证逻辑正确
- [ ] RAGFlow 客户端封装所有必要的 API 调用（datasets、documents、retrieval、llm）
- [ ] 对话路由实现 `POST /api/v1/conversations`、`GET /api/v1/conversations`、`POST /api/v1/conversations/{id}/messages`（SSE 流式）、`GET /api/v1/conversations/{id}/messages`
- [ ] SSE 流式响应格式正确（thinking、tool_call、tool_result、text、reference、done 事件）
- [ ] 知识库代理路由正确调用 RAGFlow API 并转换响应格式
- [ ] 数据源路由实现连接测试、表结构查询、SQL 执行
- [ ] 数据源密码加密存储
- [ ] Agent 路由实现 CRUD 操作
- [ ] LangGraph 服务正确集成状态图执行
- [ ] 模型配置同步到 RAGFlow 逻辑正确（验证 API Key → 保存本地 → 同步 RAGFlow）

## 前端检查点

- [x] Next.js 14 项目初始化成功（App Router、TypeScript、Tailwind CSS、shadcn/ui）
- [x] 全局布局组件创建（TopNavBar、SideBar、PageHeader）
- [x] 登录页面实现，支持用户名密码登录
- [x] 注册页面实现，支持用户注册
- [x] Token 存储和自动刷新逻辑正确
- [x] 对话列表页面展示对话历史
- [x] 对话详情页面支持流式响应展示
- [x] 工具调用卡片正确展示 SQL/检索/Web 调用信息
- [x] 引用来源卡片正确展示文档/数据库/Web 来源
- [x] 消息输入框支持文本、文件上传
- [x] SSE 流式请求处理正确
- [x] 知识库列表页面展示知识库卡片
- [x] 知识库详情页面展示文档列表
- [x] 文档上传功能正常
- [x] 检索测试功能正常
- [x] 数据源列表页面展示数据源卡片
- [x] 数据源详情页面展示表结构
- [x] SQL 编辑器功能正常
- [x] Agent 列表页面展示 Agent 卡片
- [x] Agent 配置页面支持工具选择、路由策略、降级策略配置
- [x] 工作流可视化组件展示 LangGraph 状态图
- [x] 监控中心页面展示实时仪表盘
- [x] 质量趋势图和成本趋势图正确展示
- [x] 管理后台页面展示用户列表和审计日志
- [x] API 客户端实现请求拦截器（Token 注入）和响应拦截器（错误处理）

## 集成检查点

- [ ] 前后端联调：对话流程端到端测试通过
- [ ] 前后端联调：知识库管理端到端测试通过
- [ ] 前后端联调：数据源查询端到端测试通过
- [ ] 前后端联调：Agent 配置端到端测试通过
- [ ] LangGraph 状态图执行正确（intent_router → rag_tool/db_tool → quality_check → prompt_assembly → llm_generate → hallucination）
- [ ] 降级策略正确（RAG → Database → Web）
- [ ] 模型配置同步到 RAGFlow 后检索正常
- [ ] 所有 API 接口返回统一响应格式（code、message、data、error）
- [ ] 错误码定义正确（1001 参数错误、1002 认证失败、2001 知识库不存在等）

## 性能检查点

- [ ] SSE 流式响应延迟 ≤ 100ms（首字节）
- [ ] 知识库列表查询延迟 ≤ 500ms
- [ ] 数据源表结构查询延迟 ≤ 1s
- [ ] SQL 执行延迟 ≤ 5s（简单查询）
- [ ] 前端首屏加载时间 ≤ 2s

## 安全检查点

- [ ] 用户密码使用 bcrypt 加密存储
- [ ] 数据源密码使用 AES 加密存储
- [ ] JWT Token 过期时间配置正确（默认 24 小时）
- [ ] 所有需要认证的接口都添加了认证中间件
- [ ] SQL 注入防护（使用参数化查询）
- [ ] XSS 防护（前端输入过滤）
- [ ] CORS 配置正确
