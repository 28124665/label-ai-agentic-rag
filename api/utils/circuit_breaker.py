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
"""Python 层熔断器与优雅降级实现。

对应需求：P2-NFR-02（优雅降级与熔断机制）

功能说明：
  为 RAG 管道的各依赖服务（LLM、Rerank、ES、Embedding、Web Search）提供
  熔断保护和优雅降级能力，确保单个服务故障不会导致整个系统不可用。

实现方式：
  1. 熔断器状态机（CircuitBreaker）：
     - CLOSED（关闭）：正常状态，请求通过，连续失败计数
     - OPEN（打开）：熔断状态，快速拒绝请求，直到 recovery_timeout 过期
     - HALF_OPEN（半开）：探测状态，允许有限请求探测服务恢复
     - 转换：CLOSED -> (连续失败 >= threshold) -> OPEN -> (超时恢复) -> HALF_OPEN
       -> (探测成功) -> CLOSED / (探测失败) -> OPEN
  2. Redis 共享状态：
     - 熔断器状态通过 Redis Hash 存储，Python 和 Go 层共享
     - 状态变更通过 Redis Pub/Sub 发布事件
     - 本地缓存 + Redis 同步，避免每次请求都访问 Redis
  3. 分级配置（DEFAULT_BREAKER_CONFIGS）：
     - LLM：阈值 5 次失败，60s 恢复，30s 超时
     - Rerank：阈值 3 次，30s 恢复（更敏感）
     - ES/Infinity：阈值 5 次，5s 超时（快速失败）
     - Web Search：阈值 3 次，30s 恢复
  4. 降级响应格式（make_degraded_response）：
     - 统一返回 {degraded: true, degraded_reason: "...", message: "..."}
     - 支持中英文降级提示信息
  5. 全局注册表（CircuitBreakerRegistry）：
     - 单例模式管理所有熔断器实例
     - 按服务名获取/创建熔断器
"""

import asyncio
import functools
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

def _get_redis_conn():
    """Lazily import Redis connection to avoid circular imports."""
    try:
        from rag.utils.redis_conn import REDIS_CONN
        return REDIS_CONN
    except Exception:
        return None


class CircuitBreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration for a dependency service."""

    failure_threshold: int = 5
    recovery_timeout: int = 60
    half_open_max_calls: int = 3
    timeout_seconds: int = 30
    labels: dict[str, str] = field(default_factory=dict)


# Default configurations aligned with P2-NFR-02.
DEFAULT_BREAKER_CONFIGS: dict[str, CircuitBreakerConfig] = {
    "llm": CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60, half_open_max_calls=3, timeout_seconds=30),
    "rerank": CircuitBreakerConfig(failure_threshold=3, recovery_timeout=30, half_open_max_calls=2, timeout_seconds=10),
    "es": CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60, half_open_max_calls=3, timeout_seconds=5),
    "infinity": CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60, half_open_max_calls=3, timeout_seconds=5),
    "embedding": CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60, half_open_max_calls=3, timeout_seconds=10),
    "web_search": CircuitBreakerConfig(failure_threshold=3, recovery_timeout=30, half_open_max_calls=2, timeout_seconds=10),
}


# Redis key prefix for shared state between Python and Go.
_CIRCUIT_BREAKER_KEY_PREFIX = "circuit_breaker:"
_CIRCUIT_BREAKER_CHANNEL = "circuit_breaker_events"


class DegradedReason:
    LLM_UNAVAILABLE = "llm_unavailable"
    RERANK_UNAVAILABLE = "rerank_unavailable"
    ES_UNAVAILABLE = "es_unavailable"
    INFINITY_UNAVAILABLE = "infinity_unavailable"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    WEB_SEARCH_UNAVAILABLE = "web_search_unavailable"


# User-facing degraded messages (Chinese default with English fallback metadata).
DEGRADED_MESSAGES: dict[str, dict[str, str]] = {
    DegradedReason.LLM_UNAVAILABLE: {
        "zh": "当前智能生成服务暂不可用，已为您返回相关检索内容",
        "en": "The AI generation service is temporarily unavailable. Relevant retrieval results are returned.",
    },
    DegradedReason.RERANK_UNAVAILABLE: {
        "zh": "当前检索结果未经重排序，可能相关性有所下降",
        "en": "Retrieval results are returned without reranking; relevance may be reduced.",
    },
    DegradedReason.ES_UNAVAILABLE: {
        "zh": "知识库检索服务暂时不可用，请稍后重试",
        "en": "The knowledge base search service is temporarily unavailable. Please retry later.",
    },
    DegradedReason.INFINITY_UNAVAILABLE: {
        "zh": "知识库检索服务暂时不可用，请稍后重试",
        "en": "The knowledge base search service is temporarily unavailable. Please retry later.",
    },
    DegradedReason.EMBEDDING_UNAVAILABLE: {
        "zh": "当前使用关键词检索，结果可能不够精准",
        "en": "Keyword search is used now; results may be less precise.",
    },
    DegradedReason.WEB_SEARCH_UNAVAILABLE: {
        "zh": "当前无法获取网络实时信息",
        "en": "Unable to fetch real-time web information now.",
    },
}


def degraded_message(reason: str, lang: str = "zh") -> str:
    return DEGRADED_MESSAGES.get(reason, {}).get(lang, DEGRADED_MESSAGES.get(reason, {}).get("en", reason))


def make_degraded_response(reason: str, data: Any = None, message: str | None = None, lang: str = "zh") -> dict[str, Any]:
    """Build a uniform degraded response containing degraded flag and reason."""
    return {
        "degraded": True,
        "degraded_reason": reason,
        "message": message or degraded_message(reason, lang),
        "data": data,
    }


def is_degraded_response(value: Any) -> bool:
    return isinstance(value, dict) and value.get("degraded") is True


class CircuitBreaker:
    """线程安全的熔断器实现，支持 Redis 共享状态。

    状态机：CLOSED -> OPEN -> HALF_OPEN -> CLOSED
    - CLOSED：请求正常通过，连续失败达到阈值时切换到 OPEN
    - OPEN：快速拒绝请求，recovery_timeout 后切换到 HALF_OPEN
    - HALF_OPEN：允许有限探测请求，成功则关闭，失败则重新打开

    支持同步（call）和异步（call_async）两种调用方式，
    以及装饰器模式（as_decorator）。
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None, redis_conn=None):
        self.name = name
        self.config = config or DEFAULT_BREAKER_CONFIGS.get(name, CircuitBreakerConfig())
        self._redis = redis_conn
        self._lock = threading.RLock()
        # In-process cache of state to avoid hitting Redis on every call.
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at = 0.0
        self._last_failure_at = 0.0
        self._total_failures = 0
        self._total_successes = 0
        self._load_state()

    @property
    def state(self) -> CircuitBreakerState:
        with self._lock:
            self._load_state()
            return self._state

    def _redis_key(self) -> str:
        return f"{_CIRCUIT_BREAKER_KEY_PREFIX}{self.name}"

    def _redis_client(self):
        """Return the underlying Redis client, or None if Redis is unavailable."""
        if self._redis is None:
            return None
        return getattr(self._redis, "REDIS", None)

    def _load_state(self) -> None:
        client = self._redis_client()
        if client is None:
            return
        try:
            raw = client.hgetall(self._redis_key())
            if not raw:
                return
            if isinstance(raw, dict):
                state_str = raw.get("state") or raw.get(b"state")
                self._state = CircuitBreakerState(state_str) if state_str else self._state
                self._failure_count = int(raw.get("failure_count") or raw.get(b"failure_count") or 0)
                self._success_count = int(raw.get("success_count") or raw.get(b"success_count") or 0)
                self._opened_at = float(raw.get("opened_at") or raw.get(b"opened_at") or 0)
                self._last_failure_at = float(raw.get("last_failure_at") or raw.get(b"last_failure_at") or 0)
                self._total_failures = int(raw.get("total_failures") or raw.get(b"total_failures") or 0)
                self._total_successes = int(raw.get("total_successes") or raw.get(b"total_successes") or 0)
        except Exception as e:
            logging.warning(f"[CircuitBreaker:{self.name}] failed to load state: {e}")

    def _save_state(self) -> None:
        client = self._redis_client()
        if client is None:
            return
        try:
            client.hset(
                self._redis_key(),
                mapping={
                    "state": self._state.value,
                    "failure_count": self._failure_count,
                    "success_count": self._success_count,
                    "opened_at": self._opened_at,
                    "last_failure_at": self._last_failure_at,
                    "total_failures": self._total_failures,
                    "total_successes": self._total_successes,
                    "updated_at": time.time(),
                },
            )
            client.expire(self._redis_key(), 7 * 24 * 3600)
        except Exception as e:
            logging.warning(f"[CircuitBreaker:{self.name}] failed to save state: {e}")

    def _publish_event(self, event: str, detail: dict | None = None) -> None:
        client = self._redis_client()
        if client is None:
            return
        try:
            payload = {"name": self.name, "event": event, "state": self._state.value, "ts": time.time()}
            if detail:
                payload.update(detail)
            client.publish(_CIRCUIT_BREAKER_CHANNEL, json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logging.warning(f"[CircuitBreaker:{self.name}] failed to publish event: {e}")

    def allow_request(self) -> bool:
        with self._lock:
            self._load_state()
            now = time.time()
            if self._state == CircuitBreakerState.OPEN:
                if now - self._opened_at >= self.config.recovery_timeout:
                    self._state = CircuitBreakerState.HALF_OPEN
                    self._failure_count = 0
                    self._success_count = 0
                    self._save_state()
                    self._publish_event("state_changed", {"from": "open", "to": "half_open"})
                    logging.warning(f"[CircuitBreaker:{self.name}] moved to HALF_OPEN")
                    return True
                return False
            if self._state == CircuitBreakerState.HALF_OPEN:
                return self._success_count < self.config.half_open_max_calls
            return True

    def record_success(self) -> None:
        with self._lock:
            self._load_state()
            self._total_successes += 1
            prev_state = self._state
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.half_open_max_calls:
                    self._state = CircuitBreakerState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logging.warning(f"[CircuitBreaker:{self.name}] moved to CLOSED")
                    self._publish_event("state_changed", {"from": "half_open", "to": "closed"})
            elif self._state == CircuitBreakerState.CLOSED:
                self._failure_count = 0
            self._save_state()
            if prev_state != self._state:
                self._save_state()

    def record_failure(self) -> None:
        with self._lock:
            self._load_state()
            self._total_failures += 1
            self._last_failure_at = time.time()
            prev_state = self._state
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._state = CircuitBreakerState.OPEN
                self._failure_count = 1
                self._success_count = 0
                self._opened_at = time.time()
                logging.warning(f"[CircuitBreaker:{self.name}] moved to OPEN (half-open failure)")
                self._publish_event("state_changed", {"from": "half_open", "to": "open"})
            elif self._state == CircuitBreakerState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.config.failure_threshold:
                    self._state = CircuitBreakerState.OPEN
                    self._opened_at = time.time()
                    logging.warning(
                        f"[CircuitBreaker:{self.name}] moved to OPEN after {self._failure_count} consecutive failures"
                    )
                    self._publish_event("state_changed", {"from": "closed", "to": "open", "failure_count": self._failure_count})
            self._save_state()
            if prev_state != self._state:
                self._save_state()

    def _enforce_timeout(self, func: Callable, args: tuple, kwargs: dict) -> Any:
        timeout = self.config.timeout_seconds
        if timeout <= 0:
            return func(*args, **kwargs)
        # Use threading.Timer for sync timeout.
        result_container: list[Any] = []
        exception_container: list[BaseException] = []

        def target():
            try:
                result_container.append(func(*args, **kwargs))
            except BaseException as e:
                exception_container.append(e)

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise TimeoutError(f"CircuitBreaker:{self.name} call exceeded {timeout}s")
        if exception_container:
            raise exception_container[0]
        if not result_container:
            raise TimeoutError(f"CircuitBreaker:{self.name} call exceeded {timeout}s")
        return result_container[0]

    def call(self, func: Callable, *args, fallback: Callable | None = None, **kwargs) -> Any:
        if not self.allow_request():
            if fallback:
                return fallback()
            raise CircuitBreakerOpenError(self.name)
        try:
            result = self._enforce_timeout(func, args, kwargs)
        except Exception as e:
            self.record_failure()
            raise
        self.record_success()
        return result

    async def call_async(self, func: Callable, *args, fallback: Callable | None = None, **kwargs) -> Any:
        if not self.allow_request():
            if fallback:
                return fallback()
            raise CircuitBreakerOpenError(self.name)
        timeout = self.config.timeout_seconds
        try:
            if timeout > 0:
                result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
            else:
                result = await func(*args, **kwargs)
        except asyncio.TimeoutError:
            self.record_failure()
            raise TimeoutError(f"CircuitBreaker:{self.name} call exceeded {timeout}s")
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def as_decorator(self, fallback: Callable | None = None):
        def decorator(func: Callable):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                return self.call(func, *args, fallback=fallback, **kwargs)

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await self.call_async(func, *args, fallback=fallback, **kwargs)

            return async_wrapper if asyncio.iscoroutinefunction(func) else wrapper

        return decorator


class CircuitBreakerOpenError(Exception):
    def __init__(self, name: str):
        super().__init__(f"Circuit breaker '{name}' is OPEN")
        self.name = name


class CircuitBreakerRegistry:
    """全局熔断器注册表（单例模式）。

    管理所有依赖服务的熔断器实例，按服务名索引。
    线程安全，支持并发获取/创建熔断器。
    """

    _instance: "CircuitBreakerRegistry | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._breakers: dict[str, CircuitBreaker] = {}
                    cls._instance._registry_lock = threading.Lock()
        return cls._instance

    def get(self, name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
        with self._registry_lock:
            breaker = self._breakers.get(name)
            if breaker is None:
                breaker = CircuitBreaker(name, config=config or DEFAULT_BREAKER_CONFIGS.get(name), redis_conn=_get_redis_conn())
                self._breakers[name] = breaker
            return breaker

    def reset(self, name: str) -> None:
        with self._registry_lock:
            breaker = self._breakers.get(name)
            if breaker:
                breaker._state = CircuitBreakerState.CLOSED
                breaker._failure_count = 0
                breaker._success_count = 0
                breaker._opened_at = 0.0
                breaker._save_state()


def get_breaker(name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
    return CircuitBreakerRegistry().get(name, config)


def with_circuit_breaker(name: str, fallback: Callable | None = None):
    breaker = get_breaker(name)
    return breaker.as_decorator(fallback=fallback)
