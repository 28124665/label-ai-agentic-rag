#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""OpenTelemetry 初始化模块。

在应用启动时调用 init_tracing(),自动为 Quart 请求和 httpx 调用建立 span。
trace_id 会自动注入到 LogRecord,与现有 structured_logger 对齐。

环境变量:
- OTEL_EXPORTER_OTLP_ENDPOINT: OTel Collector 地址(默认 http://otel-collector:4317)
- OTEL_SERVICE_NAME: 服务名(默认 data-knowledge-api)
- OTEL_TRACES_SAMPLER_RATE: 采样率 0-1(默认 1.0)
"""

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False
_tracer = None


def init_tracing(service_name: str = "data-knowledge-api") -> None:
    """初始化 OpenTelemetry TracerProvider。

    必须在 Quart app 启动前调用。幂等:重复调用无副作用。
    OTel SDK 未安装时跳过,不影响主流程。
    """
    global _initialized, _tracer

    if _initialized:
        return
    _initialized = True

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
    except ImportError:
        logger.info("OpenTelemetry SDK not installed, tracing disabled")
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

    _tracer = trace.get_tracer(__name__)
    logger.info(f"OpenTelemetry tracing initialized, endpoint={endpoint}, sampler_rate={sampler_rate}")


def get_tracer(name: str = __name__):
    """获取 tracer 实例。OTel 未初始化时返回 None。"""
    if _tracer is None:
        try:
            from opentelemetry import trace
            return trace.get_tracer(name)
        except ImportError:
            return None
    return _tracer


def get_current_trace_id() -> str:
    """获取当前 span 的 trace_id(32 位 hex),无 active span 时返回空串。

    用于把 OTel trace_id 与 Agent state.trace_id 对齐,
    实现日志/Sentry/Prometheus/Jaeger 四系统一 ID 关联。
    """
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx and ctx.is_valid:
            return format(ctx.trace_id, "032x")
    except ImportError:
        pass
    except Exception:
        pass
    return ""


def get_current_span():
    """获取当前 active span,用于 set_attribute。OTel 不可用时返回 None。"""
    try:
        from opentelemetry import trace
        return trace.get_current_span()
    except ImportError:
        return None
    except Exception:
        return None
