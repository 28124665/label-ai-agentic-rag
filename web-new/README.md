# Agentic RAG 前端

基于 Next.js 14 + TypeScript + Tailwind CSS + shadcn/ui 的智能问答系统前端。

## 技术栈

- **框架**: Next.js 14 (App Router)
- **语言**: TypeScript
- **样式**: Tailwind CSS
- **组件库**: shadcn/ui (Radix UI)
- **图表**: Recharts
- **HTTP 客户端**: Axios

## 开发

```bash
# 安装依赖
npm install

# 启动开发服务器
npm run dev

# 构建生产版本
npm run build

# 启动生产服务器
npm start
```

## 项目结构

```
web-new/
├── app/                    # Next.js App Router
│   ├── (auth)/            # 认证页面（登录、注册）
│   ├── (main)/            # 主应用页面
│   │   ├── chat/          # 对话界面
│   │   ├── knowledge/     # 知识库管理
│   │   ├── datasource/    # 数据源管理
│   │   ├── agent/         # Agent 配置
│   │   ├── monitor/       # 监控中心
│   │   └── admin/         # 管理后台
│   ├── layout.tsx         # 根布局
│   └── globals.css        # 全局样式
├── components/            # 可复用组件
│   ├── ui/               # UI 基础组件（shadcn/ui）
│   ├── layout/           # 布局组件
│   ├── chat/             # 对话相关组件
│   ├── knowledge/        # 知识库相关组件
│   ├── datasource/       # 数据源相关组件
│   └── agent/            # Agent 相关组件
├── lib/                  # 工具函数
│   ├── utils.ts          # 通用工具
│   └── auth.ts           # 认证工具
├── services/             # API 服务层
│   ├── api-client.ts     # API 客户端
│   ├── auth.service.ts   # 认证服务
│   ├── conversation.service.ts  # 对话服务
│   ├── knowledge.service.ts     # 知识库服务
│   ├── datasource.service.ts    # 数据源服务
│   └── agent.service.ts         # Agent 服务
└── types/                # TypeScript 类型定义
```

## 功能模块

1. **对话界面** - 流式响应、工具调用可视化、引用来源展示
2. **知识库管理** - 知识库 CRUD、文档管理、检索测试
3. **数据源管理** - 数据库连接、Schema 查看、SQL 执行
4. **Agent 配置** - 工具选择、路由策略、降级策略
5. **监控中心** - 实时仪表盘、质量监控、成本监控
6. **管理后台** - 用户管理、权限配置、审计日志

## API 接口

前端通过 `/api` 代理请求到后端 `http://localhost:8000`。

主要接口：
- `/api/v1/auth/*` - 认证相关
- `/api/v1/conversations/*` - 对话管理
- `/api/v1/knowledge/*` - 知识库管理
- `/api/v1/datasources/*` - 数据源管理
- `/api/v1/agents/*` - Agent 配置
- `/api/v1/model-configs/*` - 模型配置

## 环境变量

复制 `.env.example` 为 `.env.local` 并配置：

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_APP_NAME=Agentic RAG
NEXT_PUBLIC_APP_VERSION=1.0.0
```
