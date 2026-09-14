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
"""Sentry 错误上报初始化。

在应用启动时调用 init_sentry()。环境变量:
- SENTRY_DSN: Sentry 后端 DSN(空则跳过)
- SENTRY_ENV: 环境名(默认 boe)
- SENTRY_TRACES_SAMPLE_RATE: 性能采样率(默认 0.1)
"""

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_sentry() -> None:
    """初始化 Sentry SDK。

    幂等:重复调用无副作用。sentry-sdk 未安装或 SENTRY_DSN 未配置时跳过。
    """
    global _initialized

    if _initialized:
        return
    _initialized = True

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.info("sentry-sdk not installed, Sentry disabled")
        return

    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        logger.info("SENTRY_DSN not set, Sentry disabled")
        return

    environment = os.getenv("SENTRY_ENV", os.getenv("CONFIG_ENV", "boe"))
    traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

    # httpx 集成可选,sentry-sdk 2.0+ 内置
    integrations = [
        # ERROR 级别日志自动上报为 Sentry event
        LoggingIntegration(
            level=logging.INFO,
            event_level=logging.ERROR,
        ),
    ]

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,  # 不发送 PII
        attach_stacktrace=True,
        max_breadcrumbs=50,
        integrations=integrations,
        before_send=_before_send,
    )
    logger.info(f"Sentry initialized, environment={environment}, traces_sample_rate={traces_sample_rate}")


def _before_send(event, hint):
    """发送前过滤:补充 trace_id tag。"""
    try:
        from api.utils.tracing import get_current_trace_id
        trace_id = get_current_trace_id()
        if trace_id:
            event.setdefault("tags", {})["agent.trace_id"] = trace_id
    except Exception:
        pass
    return event


def capture_exception(exc: Exception, **tags) -> None:
    """手动上报异常(带额外 tag)。

    自动从 OTel context 获取 trace_id 写入 tag,
    实现 Sentry event 与日志/Jaeger 跨系统关联。
    """
    try:
        import sentry_sdk

        from api.utils.tracing import get_current_trace_id
        trace_id = get_current_trace_id()
        if trace_id:
            tags.setdefault("agent.trace_id", trace_id)
    except Exception:
        pass

    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc, tags=tags)
    except Exception:
        pass
