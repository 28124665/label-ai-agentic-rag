# AgenticRAG 可观测性升级落地方案

> **版本**: v1.0
> **日期**: 2026-08-09
> **目标**: 按 P0→P1→P2 分阶段落地生产级高可用可观测性,覆盖 Metrics / Logging / Tracing / 错误上报 / LLM 可观测五大支柱
> **适用项目**: data-knowledge-api(主应用 Quart 异步框架,`api/v1/` 为 FastAPI 副服务)

---

## 一、现状评估

### 1.1 当前已实现

| 支柱 | 实现 | 文件 |
|---|---|---|
| Metrics | 14 个 Prometheus 指标 + 内置兼容层 + `/metrics` endpoint | [api/utils/metrics.py](file:///Users/renwk/workspace/data-knowledge-api/api/utils/metrics.py) |
| Logging | JSON 结构化日志(PII 脱敏)+ 传统 RotatingFile 双轨 | [api/utils/structured_logger.py](file:///Users/renwk/workspace/data-knowledge-api/api/utils/structured_logger.py) + [common/log_utils.py](file:///Users/renwk/workspace/data-knowledge-api/common/log_utils.py) |
| 日志采集 | Promtail → Loki(BOE/PROD 双环境) | [deployment/promtail-config-*.yaml](file:///Users/renwk/workspace/data-knowledge-api/deployment) |
| 可视化 | Grafana 9 面板 + 9 条告警 | [deployment/grafana/dashboard.json](file:///Users/renwk/workspace/data-knowledge-api/deployment/grafana/dashboard.json) + [deployment/prometheus/alert_rules.yml](file:///Users/renwk/workspace/data-knowledge-api/deployment/prometheus/alert_rules.yml) |
| 终态聚合 | observability 节点(所有终态必经) | [agent/langgraph/nodes/observability.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/observability.py) |
| 错误处理 | FastAPI 全局异常 + Quart errorhandler | [api/v1/main.py](file:///Users/renwk/workspace/data-knowledge-api/api/v1/main.py) + [api/apps/__init__.py](file:///Users/renwk/workspace/data-knowledge-api/api/apps/__init__.py) |

### 1.2 核心缺失

| 缺失项 | 影响 | 严重度 |
|---|---|---|
| observability 调用 metrics.py 不存在的函数 | E2E 延迟、幻觉分数从未进 Prometheus | **致命** |
| `/metrics` 无鉴权 | 可能暴露租户/成本数据 | 高 |
| 无分布式 Tracing | 无法看到 HTTP→Agent→RAG→LLM 调用链 | 高 |
| trace_id 不跨 HTTP 边界 | 日志/Sentry/Prometheus 无法关联 | 高 |
| 中间态无可观测性 | 重试次数、clarification 触发数不可见 | 中 |
| 无 Sentry 错误聚合 | 异常无去重/影响面分析 | 中 |
| Langfuse 仅 API Key 管理 | LLM 调用级 trace 缺失 | 中 |
| docker-compose 无监控栈 | 本地无法一键拉起 | 中 |
| 无 SLO 体系 | 无法衡量可用性是否达标 | 中 |

---

## 二、目标架构

```
┌─────────────────────────────────────────────────────────────┐
│                        应用层                                │
│  Quart API Server  │  Agent LangGraph  │  RAG / LLM 调用     │
└──────┬──────────────────┬──────────────────┬────────────────┘
       │ 信号采集          │                  │
┌──────▼──────────────────▼──────────────────▼────────────────┐
│                        采集层                                │
│  Prometheus Client  │  Structured Logger  │  OTel SDK(新)  │
│                     │                     │  Sentry SDK(新)│
│                     │                     │  Langfuse(新) │
└──────┬──────────────────┬──────────────────┬────────────────┘
       │ 信号传输          │                  │
┌──────▼──────────────────▼──────────────────▼────────────────┐
│                        存储层                                │
│  Prometheus  │  Loki  │  OTel Collector → Jaeger(新)       │
│              │        │  Sentry Backend(新,自建)            │
└──────┬──────────────────┬──────────────────┬────────────────┘
       │ 可视化            │                  │
┌──────▼──────────────────▼──────────────────▼────────────────┐
│                        展示层                                │
│  Grafana 仪表盘  │  AlertManager  │  Jaeger UI(新)         │
│                 │                │  Sentry 看板(新)       │
│                 │                │  Langfuse 看板(新)     │
└─────────────────────────────────────────────────────────────┘
```

**核心原则**: 单一 `trace_id` 贯穿四系(日志 / Sentry / Prometheus / Jaeger),OTel 自动注入,排障时一处定位全链路。

---

## 三、技术栈与授权

| 组件 | 协议 | 商用费用 | 部署方式 |
|---|---|---|---|
| Prometheus / AlertManager | Apache 2.0 | 免费 | 自建容器 |
| Grafana / Loki / Promtail | AGPLv3 | 内部使用免费 | 自建容器 |
| OpenTelemetry SDK / Collector | Apache 2.0 | 免费 | 自建容器 |
| Jaeger | Apache 2.0 | 免费 | 自建容器 |
| Sentry SDK | MIT | SDK 免费 | — |
| Sentry Backend | BSL → Apache 2.0 | 自建免费 | self-hosted |
| Langfuse | MIT(核心) | 自建免费 | 自建容器 |

**结论**: 全部开源免费,自建路径零授权费用。

---

## 四、P0:紧急修复(1-2 天)

### P0-1 修复 observability 调用不存在的 metrics 函数

**问题**: [observability.py#L253](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/observability.py#L253) 调用 `metrics.record_e2e_latency()`,[L257](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/observability.py#L257) 调用 `metrics.record_hallucination_score()`,但 [metrics.py](file:///Users/renwk/workspace/data-knowledge-api/api/utils/metrics.py) 中这两个函数不存在,被 try/except 静默吞掉,导致 E2E 延迟和幻觉分数从未进入 Prometheus。

**改造位置**: [api/utils/metrics.py](file:///Users/renwk/workspace/data-knowledge-api/api/utils/metrics.py)

**新增指标定义**(在现有指标定义后追加):

```python
# metrics.py 在 rag_faithfulness_score 定义后追加

# 幻觉检测分数(全局,无 kb_id 维度,适配 observability 节点调用)
rag_hallucination_score = Gauge(
    "rag_hallucination_score",
    "Hallucination verification score (0-1), from PolicyEngine.",
)

# 幻觉检测 action 分布
rag_hallucination_action_total = Counter(
    "rag_hallucination_action_total",
    "Total count of hallucination actions by type.",
    ("action",),  # pass / filter / regenerate / reject / exhausted
)
```

**新增便捷函数**(在 `record_faithfulness_score` 后追加):

```python
def record_e2e_latency(seconds: float) -> None:
    """记录端到端延迟(供 observability 节点调用)。

    复用已有 rag_e2e_latency_seconds Histogram,无 kb_id 时用 "global"。
    """
    rag_e2e_latency_seconds.labels(kb_id="global").observe(seconds)


def record_hallucination_score(score: float, action: str) -> None:
    """记录幻觉检测分数与 action(供 observability 节点调用)。

    Args:
        score: PolicyEngine 加权忠实度分数 (0-1)
        action: 最终决策 pass/filter/regenerate/reject/exhausted
    """
    rag_hallucination_score.set(score)
    rag_hallucination_action_total.labels(action=action or "unknown").inc()
```

**验证**: 启动后访问 `/metrics`,确认出现 `rag_e2e_latency_seconds_bucket{kb_id="global"}` 和 `rag_hallucination_score`。

**验收标准**:
- [ ] `metrics.py` 新增 `record_e2e_latency` 和 `record_hallucination_score` 函数
- [ ] observability 节点调用不再触发 warning 日志
- [ ] `/metrics` endpoint 输出包含上述两个指标
- [ ] Grafana 仪表盘 E2E 延迟面板有数据

---

### P0-2 /metrics endpoint 加鉴权

**问题**: [system_app.py#L186](file:///Users/renwk/workspace/data-knowledge-api/api/apps/system_app.py#L186) `/metrics` 无鉴权装饰器,可能向公网暴露。

**改造位置**: [api/apps/system_app.py](file:///Users/renwk/workspace/data-knowledge-api/api/apps/system_app.py#L185-L201)

**改造代码**:

```python
import ipaddress

def _is_metrics_authorized() -> bool:
    """校验 /metrics 访问权限:IP 白名单 或 Bearer token 二选一。"""
    # 方案 A: IP 白名单(逗号分隔,支持 CIDR)
    allowed_ips = os.getenv("METRICS_SCRAPE_IPS", "")
    if allowed_ips:
        client_ip = request.remote_addr or ""
        for entry in allowed_ips.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                if "/" in entry:
                    if ipaddress.ip_address(client_ip) in ipaddress.ip_network(entry, strict=False):
                        return True
                elif client_ip == entry:
                    return True
            except ValueError:
                continue
        # IP 白名单配置后,必须命中才放行
        return False

    # 方案 B: Bearer token
    expected_token = os.getenv("METRICS_TOKEN", "")
    if expected_token:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()
        return token == expected_token

    # 未配置任何鉴权时,仅允许内网(10.x / 172.16-31.x / 192.168.x / 127.x)
    client_ip = request.remote_addr or ""
    try:
        ip = ipaddress.ip_address(client_ip)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


@manager.route("/metrics", methods=["GET"])
def metrics_endpoint():
    """Prometheus metrics endpoint(需鉴权)。"""
    if not _is_metrics_authorized():
        return Response("forbidden", status=403)
    try:
        data = metrics.generate_latest()
        return Response(data, mimetype=metrics.CONTENT_TYPE_LATEST)
    except Exception as e:
        logging.exception("Failed to generate metrics")
        return jsonify({"error": str(e)}), 500
```

**环境变量配置**(追加到 [docker/.env](file:///Users/renwk/workspace/data-knowledge-api/docker/.env)):

```bash
# Metrics endpoint 鉴权(二选一)
# 方案 A: IP 白名单(支持 CIDR)
METRICS_SCRAPE_IPS=10.0.0.0/8,172.16.0.0/12,127.0.0.1
# 方案 B: Bearer token
# METRICS_TOKEN=your-secret-token-here
```

**验收标准**:
- [ ] 外网 IP 访问 `/metrics` 返回 403
- [ ] 内网 IP 访问正常返回指标
- [ ] 配置 `METRICS_TOKEN` 后,无 token 请求返回 403

---

### P0-3 删除空文件 api/utils/log_utils.py

**问题**: [api/utils/log_utils.py](file:///Users/renwk/workspace/data-knowledge-api/api/utils/log_utils.py) 仅 15 行版权头,实际日志初始化在 [common/log_utils.py](file:///Users/renwk/workspace/data-knowledge-api/common/log_utils.py),容易误导。

**改造**: 删除 `api/utils/log_utils.py`。

**全局搜索确认无引用**:

```bash
grep -rn "from api.utils.log_utils" --include="*.py" .
grep -rn "api.utils.log_utils" --include="*.py" .
```

**验收标准**:
- [ ] 文件已删除
- [ ] 全项目无 `from api.utils.log_utils import` 引用
- [ ] 测试通过

---

## 五、P1:补齐核心缺失(1-2 周)

### P1-1 接入 OpenTelemetry 分布式追踪

**目标**: 打通"前端 → Quart API → Agent LangGraph → RAG → LLM"完整调用链,这是当前最大观测盲区。

**技术选型**: OpenTelemetry SDK(Python)+ OTel Collector + Jaeger

#### 1.1 安装依赖

**改造位置**: [pyproject.toml](file:///Users/renwk/workspace/data-knowledge-api/pyproject.toml)

```toml
# dependencies 列表追加
"opentelemetry-api>=1.24.0",
"opentelemetry-sdk>=1.24.0",
"opentelemetry-exporter-otlp-proto-grpc>=1.24.0",
"opentelemetry-instrumentation-asgi>=0.45b0",      # Quart ASGI
"opentelemetry-instrumentation-httpx>=0.45b0",    # 追踪 LLM/RAG HTTP 调用
"opentelemetry-instrumentation-logging>=0.45b0",  # 自动注入 trace_id 到日志
```

#### 1.2 初始化 TracerProvider

**新建文件**: `api/utils/tracing.py`

```python
"""OpenTelemetry 初始化模块。

在应用启动时调用 init_tracing(),自动为 Quart 请求和 httpx 调用建立 span。
trace_id 会自动注入到 LogRecord,与现有 structured_logger 对齐。
"""
import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor

logger = logging.getLogger(__name__)

_initialized = False


def init_tracing(service_name: str = "data-knowledge-api") -> None:
    """初始化 OpenTelemetry TracerProvider。

    必须在 Quart app 启动前调用。环境变量:
    - OTEL_EXPORTER_OTLP_ENDPOINT: OTel Collector 地址(默认 http://otel-collector:4317)
    - OTEL_SERVICE_NAME: 服务名(默认 data-knowledge-api)
    - OTEL_TRACES_SAMPLER_RATE: 采样率 0-1(默认 1.0)
    """
    global _initialized
    if _initialized:
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    sampler_rate = float(os.getenv("OTEL_TRACES_SAMPLER_RATE", "1.0"))

    resource = Resource.create({
        SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", service_name),
        "deployment.environment": os.getenv("CONFIG_ENV", "boe"),
    })

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=endpoint, insecure=True),
        )
    )
    trace.set_tracer_provider(provider)

    # 自动注入 trace_id / span_id 到所有 LogRecord
    LoggingInstrumentor().instrument(set_logging_format=False)

    _initialized = True
    logger.info(f"OpenTelemetry tracing initialized, endpoint={endpoint}, sampler_rate={sampler_rate}")


def get_current_trace_id() -> str:
    """获取当前 span 的 trace_id(32 位 hex),无 active span 时返回空串。"""
    span = trace.get_current_span()
    ctx = span.get_span_context() if span else None
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return ""
```

#### 1.3 Quart 应用集成

**改造位置**: [api/apps/__init__.py](file:///Users/renwk/workspace/data-knowledge-api/api/apps/__init__.py)

```python
# api/apps/__init__.py 在 app = Quart(__name__) 之后追加
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from api.utils.tracing import init_tracing

# 初始化 OTel(幂等)
init_tracing(service_name="data-knowledge-api-quart")

# 为 Quart ASGI 应用注入 OTel 中间件
app.asgi_app = OpenTelemetryMiddleware(app.asgi_app)
```

#### 1.4 Agent 工作流手动 span

**改造位置**: [agent/langgraph/nodes/user_question.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/user_question.py)

```python
# user_question.py 替换原 trace_id 生成逻辑
from api.utils.tracing import get_current_trace_id
from opentelemetry import trace

def user_question_node(state: AgentState) -> dict:
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("agent.user_question") as span:
        # 优先用 HTTP 请求带入的 OTel trace_id,无则 OTel 自动生成新的
        otel_trace_id = get_current_trace_id()
        # 兜底:OTel context 缺失时仍生成 uuid
        if not otel_trace_id:
            otel_trace_id = str(uuid.uuid4()).replace("-", "")[:32]

        span.set_attribute("agent.route_target", state.get("route_target", ""))
        span.set_attribute("agent.user_question_length", len(state.get("user_question", "")))

        return {
            "trace_id": otel_trace_id,  # 与现有 state.trace_id 对齐
            ...
        }
```

**改造位置**: 其他关键节点(intent_router / quality_check / hallucination / evidence_fusion),在函数入口加 span:

```python
# 通用模式:各节点入口
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

def hallucination_node(state: AgentState) -> dict:
    with tracer.start_as_current_span("agent.hallucination") as span:
        span.set_attribute("agent.enforcement_mode", state.get("enforcement_mode", ""))
        span.set_attribute("agent.policy_action", state.get("policy_action", ""))
        # ... 原逻辑
```

#### 1.5 LLM 调用 span

**改造位置**: [rag/llm/chat_model.py](file:///Users/renwk/workspace/data-knowledge-api/rag/llm/chat_model.py) 的 `_record_generation_metrics`

```python
# chat_model.py 在 _record_generation_metrics 中追加 span
from opentelemetry import trace

def _record_generation_metrics(self, model, prompt_tokens, completion_tokens, latency_ms):
    # 原有 metrics 记录保持
    record_llm_tokens(model, "generate", prompt_tokens + completion_tokens)

    # 新增 OTel span
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(f"llm.chat.{model}") as span:
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.prompt_tokens", prompt_tokens)
        span.set_attribute("llm.completion_tokens", completion_tokens)
        span.set_attribute("llm.latency_ms", latency_ms)
        span.set_attribute("llm.total_tokens", prompt_tokens + completion_tokens)
```

#### 1.6 OTel Collector + Jaeger 部署

**新建文件**: `deployment/otel/collector-config.yaml`

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 1024
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
  resource:
    attributes:
      - key: deployment.environment
        from_attribute: deployment.environment
        action: upsert

exporters:
  jaeger:
    endpoint: jaeger:14250
    tls:
      insecure: true
  logging:
    loglevel: warn

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch, resource]
      exporters: [jaeger, logging]
```

**验收标准**:
- [ ] pyproject.toml 新增 5 个 opentelemetry 依赖
- [ ] `api/utils/tracing.py` 新建完成
- [ ] Quart app 启动时输出 "OpenTelemetry tracing initialized"
- [ ] Jaeger UI(`http://localhost:16686`)可看到 `agent.user_question` / `agent.hallucination` / `llm.chat.*` span
- [ ] 日志中 `trace_id` 字段与 Jaeger 一致
- [ ] 全链路:一个请求从 HTTP 入口到 LLM 调用可在 Jaeger 完整展示

---

### P1-2 补齐中间态可观测性

**问题**: observability 节点仅在终态触发,无法观测"重试了多少次才 pass"、"clarification 触发了多少次"。

**改造**: 在关键中间节点加 Counter,而非只在终态采集。

#### 1.2.1 新增指标定义

**改造位置**: [api/utils/metrics.py](file:///Users/renwk/workspace/data-knowledge-api/api/utils/metrics.py)

```python
# metrics.py 新增中间态指标

# 路由决策分布
rag_intent_route_total = Counter(
    "rag_intent_route_total",
    "Total count of intent router decisions by target.",
    ("target",),  # rag / db / hybrid / chitchat / clarification
)

# 质量门决策分布
rag_quality_check_decision_total = Counter(
    "rag_quality_check_decision_total",
    "Total count of quality_check decisions.",
    ("decision",),  # pass / retry_rag / retry_db / fallback_web
)

# 当前重试次数(每次请求结束时观测)
rag_retry_count = Gauge(
    "rag_retry_count",
    "Number of retries consumed for current request.",
)

# Evidence 快照重建次数
rag_evidence_snapshot_rebuild_total = Counter(
    "rag_evidence_snapshot_rebuild_total",
    "Total count of evidence snapshot rebuilds.",
)

# Clarification 触发次数
rag_clarification_trigger_total = Counter(
    "rag_clarification_trigger_total",
    "Total count of clarification triggers.",
)
```

#### 1.2.2 在中间节点埋点

**改造位置**(5 个文件):

```python
# agent/langgraph/nodes/intent_router.py 的 route_decision 末尾
from api.utils import metrics
metrics.rag_intent_route_total.labels(target=route_target).inc()

# agent/langgraph/nodes/quality_check.py 的 _make_decision 末尾
metrics.rag_quality_check_decision_total.labels(decision=decision).inc()
metrics.rag_retry_count.set(state.get("retry_count", 0) + (1 if "retry" in decision else 0))

# agent/langgraph/nodes/evidence_fusion.py 的 evidence_fusion_node 开头
metrics.rag_evidence_snapshot_rebuild_total.inc()

# agent/langgraph/nodes/clarification.py(如有)触发处
metrics.rag_clarification_trigger_total.inc()
```

**验收标准**:
- [ ] 5 个新指标在 `/metrics` 输出可见
- [ ] 重试场景下 `rag_quality_check_decision_total{decision="retry_rag"}` 计数递增
- [ ] Grafana 新增"中间态分布"仪表盘

---

### P1-3 接入 Sentry 错误上报

**目标**: 异常自动聚合 + 影响面分析 + trace_id 关联。

#### 1.3.1 安装依赖

**改造位置**: [pyproject.toml](file:///Users/renwk/workspace/data-knowledge-api/pyproject.toml)

```toml
"sentry-sdk>=1.40.0,<2.0.0",
```

#### 1.3.2 初始化 Sentry

**新建文件**: `api/utils/error_reporter.py`

```python
"""Sentry 错误上报初始化。

在应用启动时调用 init_sentry()。环境变量:
- SENTRY_DSN: Sentry 后端 DSN(必填,空则跳过)
- SENTRY_ENV: 环境名(默认 boe)
- SENTRY_TRACES_SAMPLE_RATE: 性能采样率(默认 0.1)
"""
import logging
import os

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration

logger = logging.getLogger(__name__)

_initialized = False


def init_sentry() -> None:
    """初始化 Sentry SDK。"""
    global _initialized
    if _initialized:
        return

    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        logger.info("SENTRY_DSN not set, Sentry disabled")
        return

    environment = os.getenv("SENTRY_ENV", os.getenv("CONFIG_ENV", "boe"))
    traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,  # 不发送 PII
        attach_stacktrace=True,
        max_breadcrumbs=50,
        integrations=[
            # ERROR 级别日志自动上报为 Sentry event
            LoggingIntegration(
                level=logging.INFO,
                event_level=logging.ERROR,
            ),
            # httpx 调用异常自动捕获
            HttpxIntegration(),
        ],
        before_send=_before_send,
    )
    _initialized = True
    logger.info(f"Sentry initialized, environment={environment}, traces_sample_rate={traces_sample_rate}")


def _before_send(event, hint):
    """发送前过滤:补充 trace_id tag。"""
    # 从 OTel context 获取 trace_id,写入 Sentry tag,便于跨系统关联
    try:
        from api.utils.tracing import get_current_trace_id
        trace_id = get_current_trace_id()
        if trace_id:
            event.setdefault("tags", {})["agent.trace_id"] = trace_id
    except Exception:
        pass
    return event


def capture_exception(exc: Exception, **tags) -> None:
    """手动上报异常(带额外 tag)。"""
    try:
        from api.utils.tracing import get_current_trace_id
        trace_id = get_current_trace_id()
        if trace_id:
            tags.setdefault("agent.trace_id", trace_id)
    except Exception:
        pass
    sentry_sdk.capture_exception(exc, tags=tags)
```

#### 1.3.3 应用启动集成

**改造位置**: [api/data-knowledge-api_server.py](file:///Users/renwk/workspace/data-knowledge-api/api/data-knowledge-api_server.py)

```python
# data-knowledge-api_server.py 在 init_root_logger 后追加
from api.utils.error_reporter import init_sentry
init_sentry()
```

#### 1.3.4 Agent 工作流异常上报

**改造位置**: [agent/langgraph/graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py) 的图执行入口

```python
# graph.py 在 graph.ainvoke 调用处加 try/except
from api.utils.error_reporter import capture_exception

async def run_agent(initial_state):
    try:
        final_state = await graph.ainvoke(initial_state)
        return final_state
    except Exception as e:
        capture_exception(e, agent_route_target=initial_state.get("route_target", ""))
        raise
```

**验收标准**:
- [ ] 配置 `SENTRY_DSN` 后,故意触发异常,Sentry 看板可见
- [ ] Sentry event 的 tags 中包含 `agent.trace_id`
- [ ] Jaeger 中同一 `trace_id` 可关联到 Sentry event

---

### P1-4 trace_id 跨 HTTP 边界贯穿

**说明**: 本项由 P1-1 OTel 自动完成,无需额外代码。

**机制**: OTel ASGI 中间件自动为每个 HTTP 请求创建 root span,生成 `trace_id`。`LoggingInstrumentor` 自动注入 `trace_id` 到所有 `LogRecord`。Sentry `before_send` 从 OTel context 提取 `trace_id` 写入 tags。

**验收标准**(端到端验证):
- [ ] 发起一个 HTTP 请求
- [ ] Loki 日志中该请求的所有日志行 `trace_id` 一致
- [ ] Jaeger 中同一 `trace_id` 可查到完整 span 树
- [ ] Sentry 中异常 event 的 `agent.trace_id` tag 与日志一致
- [ ] Prometheus 指标(如有 trace_id label)一致

---

## 六、P2:进阶能力(2-4 周)

### P2-1 接入 Langfuse LLM 可观测性

**目标**: LLM 调用级 trace + 成本/质量分析。

#### 2.1.1 安装依赖

已有 `langfuse>=2.60.0`([pyproject.toml#L57](file:///Users/renwk/workspace/data-knowledge-api/pyproject.toml#L57)),无需新增。

#### 2.1.2 初始化 Langfuse 客户端

**新建文件**: `api/utils/langfuse_client.py`

```python
"""Langfuse LLM 可观测性客户端。

环境变量:
- LANGFUSE_PUBLIC_KEY: Langfuse 公钥
- LANGFUSE_SECRET_KEY: Langfuse 私钥
- LANGFUSE_HOST: Langfuse 后端地址(自建)
"""
import logging
import os

logger = logging.getLogger(__name__)

_client = None


def get_langfuse_client():
    """获取 Langfuse 客户端单例。未配置时返回 None。"""
    global _client
    if _client is not None:
        return _client

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    host = os.getenv("LANGFUSE_HOST", "")

    if not all([public_key, secret_key, host]):
        logger.info("Langfuse not configured, LLM tracing disabled")
        return None

    try:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info(f"Langfuse initialized, host={host}")
    except Exception as e:
        logger.warning(f"Langfuse init failed: {e}")
        return None

    return _client
```

#### 2.1.3 LLM 调用埋点

**改造位置**: [rag/llm/chat_model.py](file:///Users/renwk/workspace/data-knowledge-api/rag/llm/chat_model.py) 的 `chat` / `async_chat`

```python
# chat_model.py 在 _record_generation_metrics 中追加 Langfuse 上报
def _record_generation_metrics(self, model, prompt, response, prompt_tokens, completion_tokens, latency_ms, trace_id=""):
    # 原有 Prometheus + OTel 记录保持
    record_llm_tokens(model, "generate", prompt_tokens + completion_tokens)
    # ... OTel span ...

    # 新增 Langfuse
    try:
        from api.utils.langfuse_client import get_langfuse_client
        client = get_langfuse_client()
        if client:
            trace = client.trace(
                name=f"llm.{model}",
                id=trace_id,  # 与 Agent trace_id 对齐
                metadata={"model": model, "latency_ms": latency_ms},
            )
            trace.generation(
                model=model,
                prompt=prompt[:2000],  # 截断防超限
                completion=response[:2000],
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            )
    except Exception as e:
        logger.warning(f"Langfuse record failed: {e}")
```

**验收标准**:
- [ ] Langfuse 看板可见每次 LLM 调用的 prompt/completion/cost
- [ ] Langfuse trace_id 与 Agent trace_id 对齐
- [ ] Langfuse 成本分析面板有数据

---

### P2-2 定义 SLO 与告警升级

**目标**: 从阈值告警升级到 SLO 燃烧率告警。

#### 2.2.1 定义 4 个核心 SLO

| SLO | 指标 | 目标 | 错误预算(30天) | 告警条件 |
|---|---|---|---|---|
| 答案可用性 | reject + exhausted 比例 | < 1% | 7.2h | 1h reject 率 > 5% |
| E2E 延迟 | P95 端到端延迟 | < 8s | — | 5min P95 > 10s |
| 幻觉通过率 | pass + filter 比例 | > 95% | 36h | 1h reject 率 > 10% |
| 检索成功率 | 非 fallback_web 比例 | > 98% | 14.4h | 1h fallback 率 > 5% |

#### 2.2.2 告警规则

**改造位置**: [deployment/prometheus/alert_rules.yml](file:///Users/renwk/workspace/data-knowledge-api/deployment/prometheus/alert_rules.yml)

```yaml
# 追加 SLO 燃烧率告警
groups:
  - name: ragflow_slo
    rules:
      - alert: RAGSLOAnswerAvailabilityBurn
        expr: |
          sum(rate(rag_hallucination_action_total{action=~"reject|exhausted"}[1h]))
          / sum(rate(rag_hallucination_action_total[1h])) > 0.05
        for: 5m
        labels:
          severity: critical
          slo: answer_availability
        annotations:
          summary: "答案可用性 SLO 燃烧率超限,reject+exhausted 率 > 5%"
          description: "1h 内拒答率 {{ $value | humanizePercentage }},超过 5% 阈值,错误预算快速消耗"

      - alert: RAGSLOLatencyP95
        expr: |
          histogram_quantile(0.95, rate(rag_e2e_latency_seconds_bucket[5m])) > 10
        for: 5m
        labels:
          severity: warning
          slo: latency_p95
        annotations:
          summary: "E2E 延迟 P95 > 10s"
          description: "5min 内 P95 延迟 {{ $value }}s,超过 10s 阈值"

      - alert: RAGSLOHallucinationPassRate
        expr: |
          sum(rate(rag_hallucination_action_total{action=~"reject"}[1h]))
          / sum(rate(rag_hallucination_action_total[1h])) > 0.10
        for: 10m
        labels:
          severity: critical
          slo: hallucination_pass_rate
        annotations:
          summary: "幻觉通过率 SLO 违约,reject 率 > 10%"

      - alert: RAGSLORetrievalSuccess
        expr: |
          sum(rate(rag_quality_check_decision_total{decision="fallback_web"}[1h]))
          / sum(rate(rag_quality_check_decision_total[1h])) > 0.05
        for: 10m
        labels:
          severity: warning
          slo: retrieval_success
        annotations:
          summary: "检索成功率 SLO 违约,fallback_web 率 > 5%"
```

**验收标准**:
- [ ] 4 条 SLO 告警规则生效
- [ ] AlertManager 收到告警并路由到对应接收人
- [ ] Grafana 仪表盘新增 SLO 燃烧率面板

---

### P2-3 docker-compose 自洽监控栈

**目标**: 本地一键拉起完整监控栈。

**新建文件**: `docker/docker-compose-observability.yml`

```yaml
# 可观测性监控栈(本地开发用)
# 启动: docker compose -f docker/docker-compose-observability.yml up -d
services:
  prometheus:
    image: prom/prometheus:v2.51.0
    container_name: ragflow-prometheus
    volumes:
      - ../deployment/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ../deployment/prometheus/alert_rules.yml:/etc/prometheus/alert_rules.yml:ro
    ports:
      - "9090:9090"
    networks:
      - data-knowledge-api
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.4.0
    container_name: ragflow-grafana
    volumes:
      - ../deployment/grafana/dashboard.json:/var/lib/grafana/dashboards/ragflow.json:ro
      - ../deployment/grafana/datasources.yml:/etc/grafana/provisioning/datasources/datasources.yml:ro
      - ../deployment/grafana/dashboards.yml:/etc/grafana/provisioning/dashboards/dashboards.yml:ro
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_USERS_ALLOW_SIGN_UP: "false"
    networks:
      - data-knowledge-api
    depends_on:
      - prometheus
      - loki
    restart: unless-stopped

  loki:
    image: grafana/loki:2.9.0
    container_name: ragflow-loki
    ports:
      - "3100:3100"
    command: -config.file=/etc/loki/local-config.yaml
    networks:
      - data-knowledge-api
    restart: unless-stopped

  jaeger:
    image: jaegertracing/all-in-one:1.55
    container_name: ragflow-jaeger
    ports:
      - "16686:16686"  # Jaeger UI
      - "14250:14250"  # gRPC(OTel Collector 上报)
    environment:
      COLLECTOR_OTLP_ENABLED: "true"
    networks:
      - data-knowledge-api
    restart: unless-stopped

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.95.0
    container_name: ragflow-otel-collector
    volumes:
      - ../deployment/otel/collector-config.yaml:/etc/otelcol/config.yaml:ro
    ports:
      - "4317:4317"  # OTLP gRPC(应用上报)
      - "4318:4318"  # OTLP HTTP
    networks:
      - data-knowledge-api
    depends_on:
      - jaeger
    restart: unless-stopped

  sentry:
    image: sentry-self-hosted-local:latest
    container_name: ragflow-sentry
    # Sentry self-hosted 需走官方 onpremise 部署,此处仅占位
    # 实际部署:git clone https://github.com/getsentry/self-hosted.git && ./install.sh
    profiles:
      - sentry
    ports:
      - "9000:9000"
    networks:
      - data-knowledge-api
    restart: unless-stopped

  langfuse:
    image: langfuse/langfuse:2.60.0
    container_name: ragflow-langfuse
    ports:
      - "3001:3000"
    environment:
      DATABASE_URL: postgresql://postgres:postgres@langfuse-db:5432/langfuse
      NEXTAUTH_URL: http://localhost:3001
      NEXTAUTH_SECRET: langfuse-secret
      SALT: langfuse-salt
      CLICKHOUSE_URL: http://langfuse-clickhouse:8123
    depends_on:
      - langfuse-db
      - langfuse-clickhouse
    profiles:
      - langfuse
    networks:
      - data-knowledge-api
    restart: unless-stopped

  langfuse-db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: langfuse
      POSTGRES_PASSWORD: postgres
    profiles:
      - langfuse
    networks:
      - data-knowledge-api

  langfuse-clickhouse:
    image: clickhouse/clickhouse-server:23.8
    profiles:
      - langfuse
    networks:
      - data-knowledge-api

networks:
  data-knowledge-api:
    external: true
```

**新建文件**: `deployment/prometheus/prometheus.yml`(当前缺失)

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - alert_rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - alertmanager:9093

scrape_configs:
  - job_name: ragflow
    metrics_path: /metrics
    bearer_token: ${METRICS_TOKEN}
    static_configs:
      - targets:
          - data-knowledge-api-cpu:9380
        labels:
          service: ragflow
          environment: ${CONFIG_ENV}
```

**新建文件**: `deployment/grafana/datasources.yml`

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
  - name: Jaeger
    type: jaeger
    access: proxy
    url: http://jaeger:16686
```

**验收标准**:
- [ ] `docker compose -f docker/docker-compose-observability.yml up -d` 一键启动
- [ ] Grafana(`:3000`)可查到 Prometheus / Loki / Jaeger 三个数据源
- [ ] Jaeger UI(`:16686`)可查到 trace
- [ ] Loki(`:3100`)有应用日志

---

### P2-4 统一日志框架

**目标**: 废弃双轨制,统一到 JSON 结构化日志,OTel 自动注入 trace_id。

#### 2.4.1 重构 common/log_utils.py

**改造位置**: [common/log_utils.py](file:///Users/renwk/workspace/data-knowledge-api/common/log_utils.py)

```python
# common/log_utils.py 重构 init_root_logger
import logging
from logging.handlers import RotatingFileHandler
import os
import sys

def init_root_logger(logfile_basename: str, log_format: str = None):
    """初始化 root logger,统一使用 JSON 结构化格式。

    OTel LoggingInstrumentor 会自动注入 trace_id / span_id 到 LogRecord。
    """
    from api.utils.structured_logger import JSONFormatter

    log_path = os.path.join(get_project_base_directory(), "logs", f"{logfile_basename}.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    formatter = JSONFormatter()  # 统一 JSON 格式

    # 文件 handler
    file_handler = RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    # 控制台 handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    # 按包名配级别
    for pkg, level in _parse_log_levels().items():
        logging.getLogger(pkg).setLevel(level)

    root.setLevel(logging.INFO)
```

#### 2.4.2 删除传统日志格式相关代码

废弃 `log_format` 参数(保留签名向后兼容,但忽略其值)。

**验收标准**:
- [ ] 所有日志输出为单行 JSON,含 `timestamp / level / logger / message / trace_id / span_id`
- [ ] Loki 可按 `trace_id` 检索全链路日志
- [ ] 传统文本格式日志不再出现

---

## 七、实施路线图

```
P0(1-2 天)
├── P0-1 修复 observability 调用 bug ← 立即修,核心指标缺失
├── P0-2 /metrics 加鉴权
└── P0-3 删除空文件
        ↓
P1(1-2 周)
├── P1-1 OpenTelemetry 分布式追踪 ← 最高 ROI
├── P1-2 中间态可观测性埋点
├── P1-3 Sentry 错误上报(可与 P1-1 并行)
└── P1-4 trace_id 贯穿(由 P1-1 自动完成)
        ↓
P2(2-4 周)
├── P2-1 Langfuse LLM 可观测
├── P2-2 SLO 与告警升级
├── P2-3 docker-compose 自洽监控栈
└── P2-4 统一日志框架
```

---

## 八、改造后能力矩阵

| 维度 | 可观测内容 | 支撑系统 |
|---|---|---|
| 调用链 | 前端→API→Agent→RAG→LLM 完整 span 树,含每跳耗时 | Jaeger |
| 延迟分布 | E2E P50/P95/P99 + 各节点 P95 + LLM 调用延迟 | Prometheus + Grafana |
| 质量 | 检索命中率 / Grader 命中率 / 忠实度分数 / 引用完整性 | Prometheus |
| 决策 | route_target 分布 / quality_check 决策比 / hallucination action 分布 | Prometheus |
| 重试 | 重试次数分布 / 重试→pass 转化率 / Token 预算消耗 | Prometheus |
| 成本 | LLM Token 累计 / LLM 成本 / 查询重写成本 / 单次问答成本 | Prometheus + Langfuse |
| LLM 调用 | 每次 prompt/completion 全文 + model + tokens + latency | Langfuse |
| 错误 | 异常堆栈聚合 + 影响租户数 + 首次/最后出现 + trace_id 关联 | Sentry |
| 日志 | 全链路 JSON 日志,按 trace_id 一键检索 | Loki |
| SLO | 4 个核心 SLO 燃烧率 + 错误预算剩余 | Grafana + AlertManager |

---

## 九、风险与回滚

### 9.1 风险

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| OTel SDK 与 Quart 异步兼容性问题 | 中 | 追踪缺失 | 先在 BOE 灰度,验证 span 完整性 |
| Sentry SDK 性能开销 | 低 | 请求延迟增加 | `traces_sample_rate=0.1`,仅 10% 采样 |
| Langfuse 大量 LLM 调用导致存储膨胀 | 中 | 磁盘压力 | prompt/completion 截断 2000 字,定期清理 |
| OTel Collector 单点故障 | 低 | 追踪中断 | Collector 无状态,重启不影响应用;BatchSpanProcessor 本地缓冲 |
| 统一日志格式后 Promtail 解析失败 | 中 | 日志丢失 | Promtail 配置同步更新,先在 BOE 验证 |

### 9.2 回滚策略

| 阶段 | 回滚方式 |
|---|---|
| P0 | git revert,无依赖 |
| P1-1 OTel | 移除 `init_tracing()` 调用,无代码侵入 |
| P1-3 Sentry | 移除 `init_sentry()` 调用,或清空 `SENTRY_DSN` |
| P2-1 Langfuse | 清空 `LANGFUSE_PUBLIC_KEY`,client 返回 None |
| P2-4 统一日志 | 恢复 `log_format` 参数 |

**设计原则**: 所有新增可观测性代码都 fail-safe,初始化失败不影响主流程。

---

## 十、环境变量清单

新增环境变量汇总(追加到 [docker/.env](file:///Users/renwk/workspace/data-knowledge-api/docker/.env)):

```bash
# ============ 可观测性配置 ============

# P0-2: Metrics endpoint 鉴权
METRICS_SCRAPE_IPS=10.0.0.0/8,172.16.0.0/12,127.0.0.1
# METRICS_TOKEN=your-secret-token

# P1-1: OpenTelemetry
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_SERVICE_NAME=data-knowledge-api
OTEL_TRACES_SAMPLER_RATE=1.0

# P1-3: Sentry
SENTRY_DSN=
SENTRY_ENV=boe
SENTRY_TRACES_SAMPLE_RATE=0.1

# P2-1: Langfuse
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://langfuse:3000
```

---

## 十一、验收检查清单

### P0 验收
- [ ] `/metrics` 输出包含 `rag_e2e_latency_seconds` 和 `rag_hallucination_score`
- [ ] Grafana E2E 延迟面板有数据
- [ ] 外网访问 `/metrics` 返回 403
- [ ] `api/utils/log_utils.py` 已删除
- [ ] 全项目无 `from api.utils.log_utils import` 引用

### P1 验收
- [ ] Jaeger UI 可查到完整调用链(HTTP→Agent→LLM)
- [ ] 日志中 `trace_id` 与 Jaeger 一致
- [ ] Sentry 看板可见异常聚合 + `agent.trace_id` tag
- [ ] 5 个中间态指标在 `/metrics` 可见
- [ ] 端到端:一个请求的日志/trace/Sentry 可通过同一 `trace_id` 关联

### P2 验收
- [ ] Langfuse 看板可见 LLM 调用 prompt/completion/cost
- [ ] 4 条 SLO 告警规则生效
- [ ] `docker compose -f docker/docker-compose-observability.yml up -d` 一键启动
- [ ] 所有日志为 JSON 格式,含 `trace_id` 字段
- [ ] Loki 按 `trace_id` 检索返回全链路日志

---

## 十二、附录:关键文件清单

| 文件 | 改造类型 | 阶段 |
|---|---|---|
| [api/utils/metrics.py](file:///Users/renwk/workspace/data-knowledge-api/api/utils/metrics.py) | 修改(新增函数+指标) | P0-1, P1-2 |
| [api/apps/system_app.py](file:///Users/renwk/workspace/data-knowledge-api/api/apps/system_app.py) | 修改(鉴权) | P0-2 |
| api/utils/log_utils.py | 删除 | P0-3 |
| api/utils/tracing.py | 新建 | P1-1 |
| api/utils/error_reporter.py | 新建 | P1-3 |
| api/utils/langfuse_client.py | 新建 | P2-1 |
| [api/apps/__init__.py](file:///Users/renwk/workspace/data-knowledge-api/api/apps/__init__.py) | 修改(OTel 中间件) | P1-1 |
| [api/data-knowledge-api_server.py](file:///Users/renwk/workspace/data-knowledge-api/api/data-knowledge-api_server.py) | 修改(Sentry 初始化) | P1-3 |
| [agent/langgraph/nodes/user_question.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/user_question.py) | 修改(trace_id 集成) | P1-1 |
| [agent/langgraph/nodes/observability.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/observability.py) | 无需修改(P0-1 修在 metrics.py) | P0-1 |
| [agent/langgraph/nodes/intent_router.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/intent_router.py) | 修改(中间态埋点) | P1-2 |
| [agent/langgraph/nodes/quality_check.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/quality_check.py) | 修改(中间态埋点) | P1-2 |
| [agent/langgraph/nodes/evidence_fusion.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/evidence_fusion.py) | 修改(中间态埋点) | P1-2 |
| [agent/langgraph/graph.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/graph.py) | 修改(异常上报) | P1-3 |
| [rag/llm/chat_model.py](file:///Users/renwk/workspace/data-knowledge-api/rag/llm/chat_model.py) | 修改(LLM span + Langfuse) | P1-1, P2-1 |
| [common/log_utils.py](file:///Users/renwk/workspace/data-knowledge-api/common/log_utils.py) | 修改(统一 JSON) | P2-4 |
| [pyproject.toml](file:///Users/renwk/workspace/data-knowledge-api/pyproject.toml) | 修改(新增依赖) | P1-1, P1-3 |
| [docker/.env](file:///Users/renwk/workspace/data-knowledge-api/docker/.env) | 修改(新增环境变量) | 全阶段 |
| deployment/prometheus/prometheus.yml | 新建 | P2-3 |
| deployment/prometheus/alert_rules.yml | 修改(SLO 告警) | P2-2 |
| deployment/grafana/datasources.yml | 新建 | P2-3 |
| deployment/grafana/dashboards.yml | 新建 | P2-3 |
| deployment/otel/collector-config.yaml | 新建 | P1-1 |
| docker/docker-compose-observability.yml | 新建 | P2-3 |
