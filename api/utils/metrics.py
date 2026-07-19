#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""Prometheus 指标定义与采集工具。

对应需求：P2-FR-06（全链路可观测性）

功能说明：
  定义 RAG 管道的业务、成本、性能、可靠性四类 Prometheus 指标，
  提供便捷的记录函数供各组件调用。

实现方式：
  1. 指标分类：
     - 质量类：rag_retrieval_hit_rate（检索命中率）、rag_grader_hit_rate（Grader 命中率）、
       rag_faithfulness_score（忠实度分数）、rag_answer_with_citation_rate（引用率）
     - 成本类：rag_llm_tokens_total（Token 消耗）、rag_llm_cost_total（成本估算）、
       rag_query_rewrite_cost（重写成本）
     - 性能类：rag_e2e_latency_seconds（端到端延迟）、rag_retrieval_latency_seconds（检索延迟）、
       rag_generate_latency_seconds（生成延迟）
     - 可靠性类：rag_http_5xx_rate（5xx 率）、rag_timeout_rate（超时率）、
       rag_degradation_count（降级次数）、rag_retry_trigger_count（重试次数）
  2. 兼容层：如果 prometheus_client 未安装，使用内置的轻量级兼容层
     提供相同的 API（Counter/Gauge/Histogram/REGISTRY/generate_latest）
  3. 滑动窗口：使用 _RateWindow 和 _RatioTracker 计算实时比率指标
  4. 成本估算：基于每 1K Token 的默认成本（可通过 RAG_LLM_COST_PER_1K_TOKENS 配置）
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Any

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        REGISTRY,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )

    _PROMETHEUS_AVAILABLE = True
except Exception:  # pragma: no cover - prometheus_client optional
    _PROMETHEUS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Minimal prometheus_client compatibility layer
# ---------------------------------------------------------------------------

if _PROMETHEUS_AVAILABLE:
    _MetricBase = object
else:  # pragma: no cover - fallback implementation
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _Sample:
        def __init__(self, name: str, labels: dict[str, str], value: float):
            self.name = name
            self.labels = labels
            self.value = value

    class _MetricBase:
        """Base class for fallback metrics with label support."""

        _type: str = "untyped"

        def __init__(
            self,
            name: str,
            documentation: str,
            labelnames: tuple[str, ...] = (),
            register: bool = True,
            **kwargs: Any,
        ):
            self._name = name
            self._documentation = documentation
            self._labelnames = labelnames
            self._lock = threading.RLock()
            self._children: dict[tuple[str, ...], "_MetricBase"] = {}
            self._kwargs = kwargs
            self._parent: "_MetricBase | None" = None
            self._label_values: tuple[str, ...] = ()
            if register:
                REGISTRY.register(self)

        def labels(self, **label_kwargs: str) -> "_MetricBase":
            if not self._labelnames:
                return self
            key = tuple(str(label_kwargs.get(ln, "")) for ln in self._labelnames)
            with self._lock:
                if key not in self._children:
                    child = self.__class__(
                        self._name,
                        self._documentation,
                        labelnames=(),
                        register=False,
                        **self._kwargs,
                    )
                    child._parent = self
                    child._label_values = key
                    self._children[key] = child
                return self._children[key]

        def _escape(self, text: str) -> str:
            return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

        def _format_labels(self, labels: dict[str, str]) -> str:
            if not labels:
                return ""
            parts = [f'{k}="{self._escape(v)}"' for k, v in sorted(labels.items())]
            return "{" + ",".join(parts) + "}"

        def _samples(self) -> list[_Sample]:
            return []

        def _all_samples(self) -> list[_Sample]:
            with self._lock:
                if self._children:
                    samples = []
                    for key, child in self._children.items():
                        labels = dict(zip(self._labelnames, key))
                        for sample in child._samples():
                            sample.labels.update(labels)
                            samples.append(sample)
                    return samples
                return self._samples()

        def collect_text(self) -> str:
            lines = [f"# HELP {self._name} {self._documentation}", f"# TYPE {self._name} {self._type}"]
            for sample in self._all_samples():
                lines.append(f"{sample.name}{self._format_labels(sample.labels)} {sample.value}")
            return "\n".join(lines) + "\n"

    class Counter(_MetricBase):
        _type = "counter"

        def __init__(self, name: str, documentation: str, labelnames: tuple[str, ...] = (), **kwargs: Any):
            super().__init__(name, documentation, labelnames, **kwargs)
            self._value = 0.0

        def inc(self, amount: float = 1) -> None:
            with self._lock:
                self._value += amount

        def _samples(self) -> list[_Sample]:
            return [_Sample(self._name, {}, self._value)]

    class Gauge(_MetricBase):
        _type = "gauge"

        def __init__(self, name: str, documentation: str, labelnames: tuple[str, ...] = (), **kwargs: Any):
            super().__init__(name, documentation, labelnames, **kwargs)
            self._value = 0.0

        def set(self, value: float) -> None:
            with self._lock:
                self._value = float(value)

        def inc(self, amount: float = 1) -> None:
            with self._lock:
                self._value += amount

        def dec(self, amount: float = 1) -> None:
            with self._lock:
                self._value -= amount

        def _samples(self) -> list[_Sample]:
            return [_Sample(self._name, {}, self._value)]

    class Histogram(_MetricBase):
        _type = "histogram"
        _default_buckets = (
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
            30.0,
            60.0,
            120.0,
            300.0,
            600.0,
            float("inf"),
        )

        def __init__(
            self,
            name: str,
            documentation: str,
            labelnames: tuple[str, ...] = (),
            buckets: tuple[float, ...] | None = None,
            **kwargs: Any,
        ):
            super().__init__(name, documentation, labelnames, **kwargs)
            self._buckets = tuple(sorted(set(buckets or self._default_buckets)))
            if self._buckets[-1] != float("inf"):
                self._buckets = self._buckets + (float("inf"),)
            self._counts: dict[float, int] = defaultdict(int)
            self._sum = 0.0
            self._count = 0

        def observe(self, value: float) -> None:
            with self._lock:
                self._sum += value
                self._count += 1
                for b in self._buckets:
                    if value <= b:
                        self._counts[b] += 1

        def _samples(self) -> list[_Sample]:
            samples = []
            for b in self._buckets:
                samples.append(_Sample(f"{self._name}_bucket", {"le": self._format_bucket(b)}, self._counts[b]))
            samples.append(_Sample(f"{self._name}_sum", {}, self._sum))
            samples.append(_Sample(f"{self._name}_count", {}, self._count))
            return samples

        @staticmethod
        def _format_bucket(value: float) -> str:
            if value == float("inf"):
                return "+Inf"
            return str(value)

    class _FallbackRegistry:
        def __init__(self):
            self._metrics: list[_MetricBase] = []
            self._lock = threading.Lock()

        def register(self, metric: _MetricBase) -> _MetricBase:
            with self._lock:
                self._metrics.append(metric)
            return metric

        def collect(self) -> list[_MetricBase]:
            with self._lock:
                return list(self._metrics)

    REGISTRY = _FallbackRegistry()

    def generate_latest(registry: _FallbackRegistry | None = None) -> bytes:
        registry = registry or REGISTRY
        chunks = []
        for metric in registry.collect():
            chunks.append(metric.collect_text())
        return "".join(chunks).encode("utf-8")


# ---------------------------------------------------------------------------
# Public helper: track simple rates as gauges
# ---------------------------------------------------------------------------

class _RateWindow:
    """Sliding-window success/total tracker used for on-the-fly rate gauges."""

    def __init__(self, window_seconds: int = 300):
        self._window_seconds = window_seconds
        self._lock = threading.RLock()
        self._events: list[tuple[float, bool]] = []

    def record(self, success: bool) -> None:
        now = time.time()
        with self._lock:
            self._events.append((now, success))
            cutoff = now - self._window_seconds
            self._events = [e for e in self._events if e[0] > cutoff]

    def rate(self) -> float:
        now = time.time()
        with self._lock:
            cutoff = now - self._window_seconds
            recent = [e for e in self._events if e[0] > cutoff]
            if not recent:
                return 0.0
            return sum(1 for _, success in recent if not success) / len(recent)


class _RatioTracker:
    """Sliding-window ratio tracker for success-rate gauges."""

    def __init__(self, window_seconds: int = 300):
        self._window_seconds = window_seconds
        self._lock = threading.RLock()
        self._total: list[float] = []
        self._success: list[float] = []

    def record(self, success: bool) -> None:
        now = time.time()
        with self._lock:
            self._total.append(now)
            if success:
                self._success.append(now)
            cutoff = now - self._window_seconds
            self._total = [t for t in self._total if t > cutoff]
            self._success = [t for t in self._success if t > cutoff]

    def ratio(self) -> float:
        now = time.time()
        with self._lock:
            cutoff = now - self._window_seconds
            total = [t for t in self._total if t > cutoff]
            success = [t for t in self._success if t > cutoff]
            if not total:
                return 0.0
            return len(success) / len(total)


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

# Quality metrics
rag_retrieval_hit_rate = Gauge(
    "rag_retrieval_hit_rate",
    "Ratio of answers that contain citations to total answers.",
    ("kb_id",),
)

rag_answer_with_citation_rate = Gauge(
    "rag_answer_with_citation_rate",
    "Ratio of answers that include citation markers.",
    ("kb_id",),
)

rag_grader_hit_rate = Gauge(
    "rag_grader_hit_rate",
    "Ratio of runs where Grader found at least one relevant document.",
    ("kb_id",),
)

rag_faithfulness_score = Gauge(
    "rag_faithfulness_score",
    "Hallucination detector faithfulness score (0-1).",
    ("kb_id",),
)

# Cost metrics
rag_llm_tokens_total = Counter(
    "rag_llm_tokens_total",
    "Total LLM tokens consumed by the RAG pipeline.",
    ("model", "node"),
)

rag_llm_cost_total = Counter(
    "rag_llm_cost_total",
    "Estimated total LLM cost in USD.",
    ("model",),
)

rag_query_rewrite_cost = Counter(
    "rag_query_rewrite_cost",
    "Estimated cumulative cost of query rewrite retries in USD.",
    ("strategy",),
)

# Performance metrics
rag_e2e_latency_seconds = Histogram(
    "rag_e2e_latency_seconds",
    "End-to-end request latency in seconds.",
    ("kb_id",),
)

rag_retrieval_latency_seconds = Histogram(
    "rag_retrieval_latency_seconds",
    "Retrieval latency in seconds.",
    ("kb_id",),
)

rag_generate_latency_seconds = Histogram(
    "rag_generate_latency_seconds",
    "Answer generation latency in seconds.",
    ("model",),
)

# Reliability metrics
rag_http_5xx_rate = Gauge(
    "rag_http_5xx_rate",
    "Ratio of HTTP 5xx responses over the last 5 minutes.",
)

rag_timeout_rate = Gauge(
    "rag_timeout_rate",
    "Ratio of requests that timed out over the last 5 minutes.",
)

rag_degradation_count = Counter(
    "rag_degradation_count",
    "Number of times a degraded response was returned.",
    ("reason",),
)

rag_retry_trigger_count = Counter(
    "rag_retry_trigger_count",
    "Number of query rewrite retries triggered.",
    ("strategy",),
)

# ---------------------------------------------------------------------------
# Rate windows and convenience helpers
# ---------------------------------------------------------------------------

_5xx_window = _RateWindow(window_seconds=300)
_timeout_window = _RateWindow(window_seconds=300)

# Sliding-window trackers for quality rate gauges.
_citation_tracker = _RatioTracker(window_seconds=300)
_grader_hit_tracker = _RatioTracker(window_seconds=300)

# Approximate cost per 1k tokens (input+output blended).  Override via env.
_DEFAULT_COST_PER_1K_TOKENS = float(os.environ.get("RAG_LLM_COST_PER_1K_TOKENS", "0.003"))


def record_http_status(status_code: int, timed_out: bool = False) -> None:
    """Record HTTP outcome for reliability rate gauges."""
    is_5xx = 500 <= status_code < 600
    _5xx_window.record(not is_5xx)
    _timeout_window.record(not timed_out)
    rag_http_5xx_rate.set(_5xx_window.rate())
    rag_timeout_rate.set(_timeout_window.rate())
    if is_5xx:
        rag_degradation_count.labels(reason="http_5xx").inc()
    if timed_out:
        rag_degradation_count.labels(reason="timeout").inc()


def record_llm_tokens(model: str, node: str, tokens: int) -> None:
    """Record LLM token consumption and estimated cost."""
    if tokens <= 0:
        return
    rag_llm_tokens_total.labels(model=model, node=node).inc(tokens)
    cost = tokens / 1000.0 * _DEFAULT_COST_PER_1K_TOKENS
    rag_llm_cost_total.labels(model=model).inc(cost)


def record_query_rewrite_cost(strategy: str, tokens: int) -> None:
    """Record the cost associated with a query rewrite strategy."""
    if tokens <= 0:
        return
    cost = tokens / 1000.0 * _DEFAULT_COST_PER_1K_TOKENS
    rag_query_rewrite_cost.labels(strategy=strategy or "unknown").inc(cost)


def record_degradation(reason: str) -> None:
    """Record a graceful degradation event."""
    rag_degradation_count.labels(reason=str(reason)).inc()


def record_retry(strategy: str) -> None:
    """Record a query rewrite retry event."""
    rag_retry_trigger_count.labels(strategy=str(strategy) or "unknown").inc()


def record_answer_with_citation(kb_id: str, has_citation: bool) -> None:
    """Record whether the final answer included citations."""
    kb_id = kb_id or "unknown"
    _citation_tracker.record(has_citation)
    ratio = _citation_tracker.ratio()
    rag_retrieval_hit_rate.labels(kb_id=kb_id).set(ratio)
    rag_answer_with_citation_rate.labels(kb_id=kb_id).set(ratio)


def record_grader_hit(kb_id: str, has_relevant: bool) -> None:
    """Record whether Grader found at least one relevant document."""
    kb_id = kb_id or "unknown"
    _grader_hit_tracker.record(has_relevant)
    rag_grader_hit_rate.labels(kb_id=kb_id).set(_grader_hit_tracker.ratio())


def record_faithfulness_score(kb_id: str, score: float) -> None:
    """Record hallucination detector faithfulness score."""
    rag_faithfulness_score.labels(kb_id=kb_id or "unknown").set(score)
