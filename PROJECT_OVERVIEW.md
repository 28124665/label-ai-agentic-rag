# RAGFlow 项目全景分析

> 本文档帮助你快速了解 RAGFlow 项目的功能实现、技术栈和架构设计

## 📋 项目概述

**RAGFlow** 是一个开源的 RAG（Retrieval-Augmented Generation，检索增强生成）引擎，基于深度文档理解能力，为企业提供从复杂数据到 AI 应用的完整解决方案。

### 核心定位
- **版本**: v0.24.0
- **License**: Apache-2.0
- **定位**: 企业级 RAG 引擎 + Agent 平台
- **核心价值**: 将复杂非结构化数据转化为高质量的 AI 上下文

### 主要能力
1. **深度文档理解**: 支持 PDF、Word、Excel、PPT、图片等多种格式的解析
2. **智能分块**: 基于模板的智能化文档切分
3. **多路召回**: 支持向量检索 + 全文检索 + 重排序
4. **Agent 编排**: 可视化工作流编排，支持复杂任务自动化
5. **可追溯引用**: 答案附带原文引用，减少幻觉

---

## 🛠️ 技术栈详解

### 后端技术栈

#### 核心框架
| 技术 | 版本 | 用途 |
|------|------|------|
| **Python** | 3.12+ | 主要开发语言 |
| **Quart/Flask** | 0.20.0 / - | Web 框架（异步支持） |
| **uv** | - | 依赖管理工具 |
| **Peewee** | 3.17.1+ | ORM 框架 |

#### 数据存储
| 组件 | 用途 | 配置位置 |
|------|------|----------|
| **MySQL** | 元数据存储（用户、知识库、对话等） | `conf/service_conf.yaml` |
| **PostgreSQL** | 向量存储（可选） | `conf/service_conf.yaml` |
| **Redis** | 缓存、消息队列、会话管理 | `conf/service_conf.yaml` |
| **Elasticsearch** | 全文检索 + 向量存储（默认） | `docker/.env` |
| **OpenSearch** | ES 替代方案 | `docker/.env` |
| **Infinity** | 自研向量数据库 | `docker/.env` |
| **MinIO** | 对象存储（文件、图片） | `conf/service_conf.yaml` |

#### LLM 与模型支持
项目支持 **50+ 家 LLM 厂商**，主要包括：

**聊天模型** (`rag/llm/chat_model.py`)
- OpenAI (GPT-4, GPT-5)
- Anthropic (Claude)
- Google (Gemini)
- 百度文心、阿里通义、智谱 AI
- 本地模型（Ollama、vLLM）
- LiteLLM 统一接口

**Embedding 模型** (`rag/llm/embedding_model.py`)
- OpenAI Embeddings
- 本地 BERT 模型
- Jina Embeddings
- 多语言支持

**Rerank 模型** (`rag/llm/rerank_model.py`)
- Cohere Rerank
- BGE Reranker
- Jina Reranker

**视觉模型** (`rag/llm/cv_model.py`, `ocr_model.py`)
- GPT-4V
- Qwen-VL
- OCR 识别（PaddleOCR）

#### 文档解析引擎 (`deepdoc/`)
- **PDF**: pdfplumber + 自研布局识别
- **Word**: python-docx
- **Excel**: python-calamine, openpyxl
- **PPT**: python-pptx
- **图片**: OCR + 多模态模型
- **HTML**: readability-lxml
- **Markdown**: markdown-it-py

### 前端技术栈

#### 核心框架
| 技术 | 版本 | 用途 |
|------|------|------|
| **React** | 18.2.0 | UI 框架 |
| **TypeScript** | 5.9.3 | 类型安全 |
| **Vite** | 7.2.7 | 构建工具 |
| **UmiJS** | - | 企业级 React 框架 |

#### UI 组件库
- **Ant Design**: 5.x（主组件库）
- **Ant Design Pro Components**: 高级业务组件
- **Radix UI**: 无样式基础组件
- **TailwindCSS**: 原子化 CSS

#### 状态管理
- **Zustand**: 轻量级状态管理
- **React Query**: 服务端状态管理
- **Immer**: 不可变数据更新

#### 可视化与图表
- **AntV G2**: 数据可视化
- **AntV G6**: 关系图/流程图
- **Recharts**: React 图表库
- **React Flow**: 工作流编辑器

#### 其他关键库
- **i18next**: 国际化
- **axios**: HTTP 请求
- **dayjs**: 日期处理
- **lodash**: 工具函数库
- **react-markdown**: Markdown 渲染
- **Monaco Editor**: 代码编辑器

---

## 🏗️ 系统架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────┐
│                    前端层 (React + TypeScript)            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ 知识库管理 │  │ 对话界面  │  │ Agent 编排│              │
│  └──────────┘  └──────────┘  └──────────┘              │
└─────────────────────────────────────────────────────────┘
                          ↓ HTTP/REST API
┌─────────────────────────────────────────────────────────┐
│                  API 层 (Quart/Flask)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ kb_app   │  │dialog_app│  │canvas_app│  ...         │
│  └──────────┘  └──────────┘  └──────────┘              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   业务逻辑层                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ RAG 引擎  │  │ Agent 系统│  │文档解析引擎│              │
│  │  (rag/)  │  │ (agent/) │  │(deepdoc/)│              │
│  └──────────┘  └──────────┘  └──────────┘              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   数据存储层                              │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐         │
│  │MySQL │ │Redis │ │  ES  │ │MinIO │ │ PG   │         │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘         │
└─────────────────────────────────────────────────────────┘
```

### 核心模块详解

#### 1. API 层 (`api/`)

**职责**: 提供 RESTful API，处理 HTTP 请求

**关键目录**:
```
api/
├── apps/              # API 蓝图（路由定义）
│   ├── kb_app.py         # 知识库管理
│   ├── dialog_app.py     # 对话管理
│   ├── canvas_app.py     # Agent 工作流
│   ├── document_app.py   # 文档管理
│   ├── chunk_app.py      # 文本块管理
│   └── ...
├── db/              # 数据库模型与服务
│   ├── db_models.py    # ORM 模型定义
│   └── services/       # 业务服务层
├── utils/           # 工具函数
└── data-knowledge-api_server.py  # 入口文件
```

**核心 API 模块**:
- `kb_app.py`: 知识库 CRUD、配置管理
- `dialog_app.py`: 对话会话、消息管理
- `canvas_app.py`: Agent 工作流编排
- `document_app.py`: 文档上传、解析状态
- `chunk_app.py`: 文本块查询、编辑

#### 2. RAG 引擎 (`rag/`)

**职责**: 实现检索增强生成的核心逻辑

**关键目录**:
```
rag/
├── llm/             # LLM 模型封装
│   ├── chat_model.py      # 聊天模型
│   ├── embedding_model.py # 向量模型
│   ├── rerank_model.py    # 重排序模型
│   └── cv_model.py        # 视觉模型
├── app/             # RAG 应用逻辑
│   ├── chunking.py      # 文档分块策略
│   ├── retrieval.py     # 检索逻辑
│   └── rag.py           # RAG 主流程
└── utils/           # 工具函数
```

**核心流程**:
1. **文档解析**: `deepdoc/` → 提取文本、表格、图片
2. **智能分块**: `rag/app/chunking.py` → 按模板切分
3. **向量化**: `rag/llm/embedding_model.py` → 生成向量
4. **存储**: 写入 ES/Infinity + MinIO
5. **检索**: `rag/app/retrieval.py` → 多路召回 + 重排序
6. **生成**: `rag/llm/chat_model.py` → LLM 生成答案

#### 3. Agent 系统 (`agent/`)

**职责**: 可视化工作流编排，支持复杂任务自动化

**关键目录**:
```
agent/
├── component/       # Agent 组件
│   ├── base.py            # 组件基类
│   ├── begin.py           # 开始节点
│   ├── llm.py             # LLM 调用
│   ├── retrieval.py       # 知识检索
│   ├── switch.py          # 条件分支
│   ├── loop.py            # 循环
│   ├── iteration.py       # 迭代
│   ├── message.py         # 消息处理
│   └── ...
├── tools/           # 工具集
│   ├── base.py            # 工具基类
│   ├── search.py          # 搜索工具
│   ├── code_executor.py   # 代码执行
│   └── ...
└── canvas.py        # 工作流引擎
```

**核心组件**:
- **LLM**: 调用大语言模型
- **Retrieval**: 知识库检索
- **Switch**: 条件分支
- **Loop/Iteration**: 循环迭代
- **Message**: 消息发送
- **Code Executor**: 代码执行（Python/JavaScript）

#### 4. 文档解析引擎 (`deepdoc/`)

**职责**: 深度文档理解，提取结构化信息

**关键目录**:
```
deepdoc/
├── parser/          # 文档解析器
│   ├── pdf_parser.py      # PDF 解析
│   ├── docx_parser.py     # Word 解析
│   ├── excel_parser.py    # Excel 解析
│   ├── ppt_parser.py      # PPT 解析
│   ├── html_parser.py     # HTML 解析
│   └── markdown_parser.py # Markdown 解析
└── vision/          # 视觉识别
    ├── ocr.py             # OCR 文字识别
    ├── layout_recognizer.py # 布局识别
    └── table_structure_recognizer.py # 表格结构识别
```

**解析能力**:
- **PDF**: 文本提取 + 布局分析 + 表格识别
- **Word**: 段落、表格、图片提取
- **Excel**: 多 Sheet、公式解析
- **图片**: OCR + 多模态理解
- **HTML**: 网页正文提取

---

## 🚀 快速上手指南

### 环境要求

**硬件要求**:
- CPU >= 4 核
- RAM >= 16 GB
- Disk >= 50 GB

**软件要求**:
- Docker >= 24.0.0
- Docker Compose >= v2.26.1
- Python 3.12+
- Node.js >= 18.20.4

### 方式一：Docker 部署（推荐）

```bash
# 1. 克隆代码
git clone https://github.com/infiniflow/ragflow.git
cd ragflow/docker

# 2. 启动服务
docker compose -f docker-compose.yml up -d

# 3. 查看日志
docker logs -f docker-ragflow-cpu-1

# 4. 访问界面
# 浏览器打开 http://localhost
```

### 方式二：源码开发

#### 后端启动

```bash
# 1. 安装依赖
uv sync --python 3.12 --all-extras
uv run download_deps.py

# 2. 启动基础设施
docker compose -f docker/docker-compose-base.yml up -d

# 3. 配置 hosts
# 编辑 /etc/hosts，添加：
# 127.0.0.1 es01 infinity mysql minio redis

# 4. 启动后端
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh
```

#### 前端启动

```bash
# 1. 安装依赖
cd web
npm install

# 2. 启动开发服务器
npm run dev
# 访问 http://localhost:8000
```

### 关键配置文件

| 文件 | 用途 |
|------|------|
| `docker/.env` | Docker 环境变量（端口、密码等） |
| `docker/service_conf.yaml.template` | 服务配置模板 |
| `conf/service_conf.yaml` | 本地开发配置 |
| `conf/llm_factories.json` | LLM 厂商配置 |

### 常用命令

```bash
# 后端测试
uv run pytest

# 前端测试
cd web && npm run test

# 代码格式化
ruff check
ruff format

# 前端代码检查
cd web && npm run lint
```

---

## 📊 核心功能模块

### 1. 知识库管理

**功能**:
- 创建/删除知识库
- 上传文档（支持批量）
- 配置解析策略（模板选择）
- 查看解析进度
- 手动调整分块

**关键文件**:
- API: `api/apps/kb_app.py`
- Service: `api/db/services/knowledgebase_service.py`
- Model: `api/db/db_models.py` (Knowledgebase 类)

### 2. 对话系统

**功能**:
- 创建对话助手
- 关联知识库
- 配置 LLM 参数（温度、Top P 等）
- 多轮对话
- 引用展示

**关键文件**:
- API: `api/apps/dialog_app.py`, `api/apps/conversation_app.py`
- Service: `api/db/services/dialog_service.py`
- RAG: `rag/app/rag.py`

### 3. Agent 工作流

**功能**:
- 可视化画布编辑
- 组件拖拽编排
- 条件分支、循环
- 工具调用（搜索、代码执行）
- 调试与测试

**关键文件**:
- API: `api/apps/canvas_app.py`
- Engine: `agent/canvas.py`
- Components: `agent/component/*.py`

### 4. 文档解析

**功能**:
- 多格式文档解析
- 智能布局分析
- 表格结构识别
- OCR 文字识别
- 分块策略配置

**关键文件**:
- Parser: `deepdoc/parser/*.py`
- Vision: `deepdoc/vision/*.py`
- Chunking: `rag/app/chunking.py`

### 5. 检索与生成

**功能**:
- 向量检索
- 全文检索
- 混合检索
- 重排序
- LLM 生成

**关键文件**:
- Retrieval: `rag/app/retrieval.py`
- LLM: `rag/llm/chat_model.py`
- Rerank: `rag/llm/rerank_model.py`

---

## 🔍 开发建议

### 代码阅读顺序

1. **入门**: 
   - `README.md` → 项目概述
   - `api/data-knowledge-api_server.py` → 启动流程

2. **API 层**:
   - `api/apps/kb_app.py` → 知识库 API
   - `api/db/services/knowledgebase_service.py` → 业务逻辑

3. **RAG 核心**:
   - `rag/app/chunking.py` → 分块策略
   - `rag/app/retrieval.py` → 检索逻辑
   - `rag/llm/chat_model.py` → LLM 调用

4. **Agent 系统**:
   - `agent/canvas.py` → 工作流引擎
   - `agent/component/base.py` → 组件基类
   - `agent/component/llm.py` → LLM 组件

5. **文档解析**:
   - `deepdoc/parser/pdf_parser.py` → PDF 解析
   - `deepdoc/vision/ocr.py` → OCR 识别

### 调试技巧

```bash
# 查看后端日志
docker logs -f docker-ragflow-cpu-1

# 查看 ES 数据
curl -X GET "http://localhost:9200/_cat/indices?v"

# 查看 Redis 缓存
docker exec -it docker-redis-1 redis-cli

# 前端调试
cd web && npm run dev  # 开启 source map
```

### 常见问题

**Q: 文档解析失败？**
- 检查 `deepdoc/` 目录下的解析器日志
- 确认文档格式是否支持
- 查看 MinIO 文件是否上传成功

**Q: 检索效果不好？**
- 调整分块策略（`rag/app/chunking.py`）
- 检查 Embedding 模型配置
- 尝试调整重排序参数

**Q: LLM 调用失败？**
- 检查 `conf/llm_factories.json` 配置
- 确认 API Key 是否正确
- 查看网络是否可访问 LLM 服务

---

## 📚 扩展资源

- **官方文档**: https://ragflow.io/docs/dev/
- **GitHub**: https://github.com/infiniflow/ragflow
- **Demo**: https://demo.ragflow.io
- **Discord**: https://discord.gg/NjYzJD3GM3

---

## 🎯 总结

RAGFlow 是一个功能完整、架构清晰的企业级 RAG 系统，具有以下特点：

✅ **功能全面**: 覆盖文档解析、知识管理、检索生成、Agent 编排全流程  
✅ **技术先进**: 支持最新 LLM、多种向量数据库、可视化工作流  
✅ **架构清晰**: 模块化设计，易于扩展和维护  
✅ **生产就绪**: 完善的 Docker 部署方案、监控日志、错误处理  

适合以下场景：
- 企业知识库建设
- 智能客服系统
- 文档问答应用
- 数据分析助手
- AI Agent 开发平台

---

**文档生成时间**: 2026-07-07  
**适用版本**: v0.24.0
