# Label AI · Agentic RAG 数据知识问答平台

[English](./README.md) | 简体中文

这是一个面向企业的数据知识问答平台，把文档知识检索（RAG）和数据库查询（SQL）放在一套流程里，由 LangGraph agent 负责判断问题、选择工具和组织执行，再通过逐条事实核对来减少回答中的幻觉。

项目主要服务制造业场景，能够连接 MES、QMS、OMS、ERP、HR 等业务数据库，以及 SOP、制度、作业指导书等知识库。本项目基于 [RAGFlow](https://github.com/infiniflow/ragflow) 进行二次开发。

## 为什么做这个项目

业务问题往往不是单纯查文档或查数据库。例如：

> 上个月订单准时交付率为什么下降了 8%，应该怎么改善？

这个问题需要先从数据库查询交付率、延期订单和相关指标，再从知识库查询原因判断标准和改善办法。平台会自动把问题交给 DBTool、RAGTool，或者拆成多个步骤执行，最后检查答案中的数字、日期和结论是否有证据支持。

## 整体流程

```text
用户问题
   │
   ▼
意图路由：复杂度判断 → 规则判断 → LLM 判断 → 任务拆分
   │
   ├── 闲聊或问题不清楚 ───────────────► 直接回复或要求补充信息
   ├── RAGTool ─────► 证据融合
   ├── DBTool ──────► 证据融合
   └── 多步骤执行 ──► 证据融合
                          │
                          ▼
                    证据质量检查
                          │ 材料不足时重新查询
                          ▼
                组装 prompt → LLM 生成答案
                          │
                          ▼
                     幻觉检测
                    ┌─────┴─────┐
                    ▼           ▼
              输出可信内容    拒答或重写
```

## 主要模块

| 模块 | 代码位置 | 说明 |
|---|---|---|
| 意图路由 | `agent/langgraph/routers/` | 先判断问题复杂度，再通过规则和 LLM 判断走 RAG、数据库、网页、接口或多工具流程；复杂问题由计划器拆成多个步骤 |
| 主流程编排 | `agent/langgraph/graph.py` | 用 LangGraph 串起意图路由、工具查询、证据融合、质量检查、答案生成、幻觉检测和结果输出 |
| 工具调用体系 | `agent/langgraph/tools/` | 工具通过 `@register_tool` 注册，由注册表按名称查找；计时、校验和异常处理统一放在 `BaseTool` 中，新增工具不用修改分发器 |
| RAGTool | `agent/langgraph/tools/executors/rag.py` | 查询文档知识，支持父子分块召回、rerank、LLM 与 NLI 质量判断，适合回答定义、制度、流程、原因和处理办法 |
| DBTool | `agent/langgraph/tools/executors/database.py` | 把自然语言转成 SQL，查询结构化数据；支持逐步提供表和字段信息，以及 SQL Agent 失败兜底，适合回答数量、排名和趋势 |
| 其他工具 | `agent/langgraph/tools/executors/` | 包含网页查询、业务接口、报告生成等工具；GraphTool 用于图谱查询 |
| 证据融合 | `agent/langgraph/evidence/` | 将 RAG、数据库和其他工具的结果合并为同一份带编号的证据，生成、检测和引用都使用这份证据 |
| 幻觉检测 | `agent/langgraph/` | 把答案拆成一条条结论，判断每条内容是有证据、与证据冲突还是证据不足；关键数字或日期错误时拒答，材料不足时重写 |
| 监控 | `api/utils/`、`deployment/` | 记录路由结果、工具耗时、LLM 调用、幻觉检测结果和错误信息，支持 Langfuse、OpenTelemetry、Prometheus 和 Grafana |

## 项目目录

```text
agent/langgraph/   LangGraph agent：路由、节点、工具、技能和证据处理
api/               后端接口和业务服务
rag/               文档切分、检索、rerank 和质量判断
deepdoc/           文档解析和 OCR
web/               React 前端
conf/              运行配置，本地 service_conf.yaml 不提交
docker/            Docker 部署文件
deployment/        Prometheus、Grafana 和 OpenTelemetry 配置
test/              后端测试
```

## 本地启动

需要准备 Python 3.10+、uv、Node.js 18+ 和 Docker。

```bash
# 1. 启动 PostgreSQL/MySQL、Elasticsearch、Redis、MinIO 等依赖
docker compose -f docker/docker-compose-base.yml up -d

# 2. 准备本地配置，真实账号和密钥不要提交到 Git
cp docker/service_conf.yaml.template conf/service_conf.yaml
cp docker/.env.example docker/.env

# 3. 启动后端
uv sync --python 3.12 --all-extras
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh

# 4. 启动前端
cd web
npm install
npm run dev
```

## 测试与代码检查

```bash
uv run pytest
ruff check
ruff format

cd web
npm run test
npm run lint
```

## 配置安全

- `docker/.env*` 和 `conf/service_conf.yaml` 只用于本地运行，不应提交真实账号、密码或密钥。
- 仓库模板中只保留空值或示例值，部署时通过环境变量或安全配置中心注入。

## 开源协议

项目沿用 RAGFlow 的 Apache-2.0 协议，详见 [LICENSE](./LICENSE)。

## 致谢

本项目基于 InfiniFlow 开源的 [RAGFlow](https://github.com/infiniflow/ragflow) 进行二次开发，沿用了其文档解析、检索和知识库基础能力，并增加了多工具编排、数据库查询、证据融合、幻觉检测和可观测性能力。
