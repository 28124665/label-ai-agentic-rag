# 性能基线测试环境配置

本文档记录二期 RAG 增强性能基线测试（Task 8 / P2-NFR-01）所需的硬件、依赖和启动方式。

## 1. 推荐硬件配置

| 组件 | 最小配置 | 推荐配置 | 说明 |
|------|---------|---------|------|
| API 服务（Go 层） | 4C8G × 2 | **8C16G × 2** | 处理 HTTP/GRPC 入口、鉴权、路由 |
| Python 服务 | 8C16G × 2 | **16C32G × 2** | 运行 Flask/Quart 后端、Agent Canvas、LLM 编排 |
| ES / OpenSearch | 8C16G × 3 | **16C32G × 3** | 文档索引与混合检索，建议 SSD |
| Redis | 4C8G × 1 | **8C16G × 1（主从）** | 运行时状态、熔断状态、检查点缓存 |
| Rerank 模型 | CPU 推理 | **GPU T4/V100 × 1** | Cross-Encoder 推理，GPU 可显著降低延迟 |
| LLM | 外部 API | 外部 API | 记录模型版本、并发限制和 RPM/TPM 配额 |

> 注：若使用外部 LLM API（如 ZHIPU-AI、OpenAI），需在报告中记录模型版本、并发上限和 Token 配额。

## 2. 测试数据集

位于 `test/perf/datasets/`：

| 文件 | 规模 | 用途 |
|------|------|------|
| `small_kb.json` | 1,000 篇 FAQ | 小规模知识库检索压力测试 |
| `medium_kb.json` | 10,000 篇技术文档 | 中规模知识库检索压力测试 |
| `queries.json` | 100 条查询 | 40% 简单 / 40% 中等 / 20% 复杂 |

数据集为合成数据，可通过 `test/perf/generate_datasets.py` 重新生成：

```bash
uv run test/perf/generate_datasets.py
```

## 3. 测试依赖

本项目使用 Python 3.12，依赖管理工具为 `uv`。测试依赖已在 `pyproject.toml` 的 `[dependency-groups] test` 中声明，主要包括：

- `requests>=2.32.2`
- `requests-toolbelt>=1.0.0`

安装方式：

```bash
uv sync --python 3.12 --group test
```

> 优先使用项目已有依赖，不额外引入 Locust 等压测框架。

## 4. 启动依赖服务

使用 Docker Compose 启动基础依赖：

```bash
docker compose -f docker/docker-compose-base.yml up -d
```

依赖服务包括：MySQL、ES/Infinity、Redis、MinIO。

## 5. 启动 RAGFlow 后端

```bash
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh
```

默认 API 地址为 `http://127.0.0.1:9380`。

## 6. 准备知识库

1. 通过 Web 或 API 创建知识库（Dataset）。
2. 上传并解析 `small_kb.json` / `medium_kb.json` 中的文档。
3. 记录生成的 `kb_id`，供压测脚本使用。

## 7. 执行基线测试

### 7.1 检索接口压测

```bash
uv run test/perf/baseline_test.py \
  --host http://127.0.0.1:9380 \
  --kb_id <KB_ID> \
  --mode retrieval \
  --dataset test/perf/datasets/queries.json
```

### 7.2 对话接口压测

```bash
uv run test/perf/baseline_test.py \
  --host http://127.0.0.1:9380 \
  --kb_id <KB_ID> \
  --mode chat \
  --chat_id <CHAT_ID> \
  --model "glm-4-flash@ZHIPU-AI" \
  --dataset test/perf/datasets/queries.json
```

### 7.3 自定义 QPS / 时长

```bash
uv run test/perf/baseline_test.py \
  --host http://127.0.0.1:9380 \
  --kb_id <KB_ID> \
  --qps 20 \
  --duration 300
```

### 7.4 生成模拟数据报告（无真实服务）

```bash
uv run test/perf/baseline_test.py --dry-run --dataset test/perf/datasets/queries.json
```

## 8. 默认并发模型

`baseline_test.py` 默认执行以下阶段：

| 阶段 | 模式 | 持续时间 | 说明 |
|------|------|---------|------|
| warm-up | 5 QPS | 2 分钟 | 缓存预热 |
| stable-10 | 10 QPS | 10 分钟 | 稳态性能 |
| stable-30 | 30 QPS | 10 分钟 | 稳态性能 |
| stable-50 | 50 QPS | 10 分钟 | 稳态性能 |
| burst | 10 → 100 QPS | 5 分钟 | 突发流量 |
| recovery | 10 QPS | 5 分钟 | 恢复能力观察 |

可通过 `--stages` 传入 JSON 列表自定义阶段。

## 9. 监控与指标

测试期间建议同时观察：

- Prometheus 指标：`rag_end_to_end_latency_seconds`、`rag_retrieval_latency_seconds`、`rag_http_5xx_rate`
- Grafana Dashboard：`deployment/grafana/dashboard.json`
- 服务端日志：`api/logs/` 或容器日志
