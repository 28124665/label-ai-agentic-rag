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
"""主动健康检查器。

对应需求：P2-NFR-02（优雅降级 - 主动健康检查）

功能说明：
  定期探测各依赖服务的健康状态，将探测结果反馈给熔断器，
  实现主动熔断（不仅依赖被动调用失败）。

实现方式：
  1. HealthProbe：抽象健康探测基类，封装探测函数和异常处理
  2. 内置探测函数：
     - _http_probe：HTTP HEAD/GET 请求探测
     - _tcp_probe：TCP 连接探测
     - _ping_redis：通过 Redis 连接器的 health() 方法探测
     - _ping_es：通过 ES 连接器的 ping() 方法探测
     - _ping_infinity：通过 Infinity 连接池探测
  3. HealthChecker：后台线程定期（默认 10s）执行所有注册的探测
     - 探测成功 -> 调用 breaker.record_success()
     - 探测失败 -> 调用 breaker.record_failure()
  4. 默认只注册基础设施服务（es/infinity/redis）的主动探测
     模型服务（llm/rerank/embedding/web_search）依赖被动失败检测
"""

import logging
import threading
import time
import urllib.request
from abc import ABC
from typing import Callable

from api.utils.circuit_breaker import get_breaker


class HealthProbe(ABC):
    """Abstract base for a dependency health probe."""

    name: str
    probe_fn: Callable[[], bool]

    def __init__(self, name: str, probe_fn: Callable[[], bool]):
        self.name = name
        self.probe_fn = probe_fn

    def check(self) -> bool:
        try:
            return self.probe_fn()
        except Exception as e:
            logging.warning(f"[HealthProbe:{self.name}] health check failed: {e}")
            return False


def _http_probe(url: str, timeout: float = 3.0, method: str = "HEAD") -> bool:
    """Lightweight HTTP probe using a HEAD/GET request."""
    if not url:
        return False
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False


def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    """Lightweight TCP connectivity probe."""
    import socket

    if not host or port <= 0:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _ping_redis() -> bool:
    """Probe Redis availability used by circuit breaker state sharing."""
    from rag.utils.redis_conn import REDIS_CONN

    try:
        if REDIS_CONN and REDIS_CONN.is_alive():
            return REDIS_CONN.health()
    except Exception as e:
        logging.warning(f"[HealthProbe:redis] redis health check failed: {e}")
    return False


def _ping_es() -> bool:
    """Probe Elasticsearch using the existing connection wrapper if available."""
    try:
        from rag.utils.es_conn import ESConnection

        conn = ESConnection()
        if conn and conn.es:
            conn.es.ping()
            return True
    except Exception as e:
        logging.warning(f"[HealthProbe:es] es health check failed: {e}")
    return False


def _ping_infinity() -> bool:
    """Probe Infinity using the existing connection wrapper if available."""
    try:
        from rag.utils.infinity_conn import InfinityConnection

        conn = InfinityConnection()
        if conn and conn.connPool:
            inf_conn = conn.connPool.get_conn()
            try:
                conn.connPool.release_conn(inf_conn)
                return True
            except Exception:
                return False
    except Exception as e:
        logging.warning(f"[HealthProbe:infinity] infinity health check failed: {e}")
    return False


def _default_probe_for(name: str) -> Callable[[], bool]:
    """Build a default probe function for a known dependency service."""
    if name == "es":
        return _ping_es
    if name == "infinity":
        return _ping_infinity
    if name == "redis":
        return _ping_redis
    return lambda: False


class HealthChecker:
    """后台健康检查器，定期探测依赖服务状态。

    每 interval_seconds（默认 10 秒）执行一轮探测。
    探测结果直接反馈给对应的熔断器，实现主动熔断。
    以 daemon 线程运行，不阻塞主进程。
    """

    DEFAULT_INTERVAL_SECONDS = 10

    def __init__(self, interval_seconds: int = DEFAULT_INTERVAL_SECONDS):
        self.interval_seconds = interval_seconds
        self._probes: dict[str, HealthProbe] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def register(self, name: str, probe_fn: Callable[[], bool] | None = None) -> "HealthChecker":
        """Register a service to be health-checked.

        If ``probe_fn`` is not provided, a default probe for known services
        (es, infinity, redis) is used; otherwise the probe always returns False
        and relies on passive failure detection.
        """
        with self._lock:
            self._probes[name] = HealthProbe(name, probe_fn or _default_probe_for(name))
        return self

    def unregister(self, name: str) -> "HealthChecker":
        with self._lock:
            self._probes.pop(name, None)
        return self

    def _run_once(self) -> None:
        with self._lock:
            probes = list(self._probes.values())

        for probe in probes:
            healthy = probe.check()
            breaker = get_breaker(probe.name)
            if healthy:
                breaker.record_success()
            else:
                breaker.record_failure()
                logging.warning(f"[HealthChecker] {probe.name} is unhealthy, recorded circuit breaker failure")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_once()
            except Exception as e:
                logging.warning(f"[HealthChecker] run once failed: {e}")
            self._stop_event.wait(self.interval_seconds)

    def start(self) -> "HealthChecker":
        """Start the background health checking thread."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="health_checker", daemon=True)
            self._thread.start()
            logging.info("[HealthChecker] started")
        return self

    def stop(self) -> "HealthChecker":
        """Stop the background health checking thread."""
        with self._lock:
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=5.0)
                self._thread = None
        return self


# Global singleton used by the application.
_global_health_checker: HealthChecker | None = None
_global_lock = threading.Lock()


def get_health_checker() -> HealthChecker:
    """Get the global health checker instance (starts it on first call)."""
    global _global_health_checker
    if _global_health_checker is None:
        with _global_lock:
            if _global_health_checker is None:
                _global_health_checker = HealthChecker().start()
    return _global_health_checker


def register_default_probes() -> HealthChecker:
    """Register probes for infrastructure dependency services and start checking.

    Model services (llm/rerank/embedding/web_search) rely on passive failure
    detection through the circuit breaker because they do not have a generic
    health endpoint. Only infrastructure services with deterministic probes are
    actively checked by default.
    """
    checker = get_health_checker()
    for name in ("es", "infinity", "redis"):
        checker.register(name)
    return checker
