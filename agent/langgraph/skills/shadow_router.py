#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Shadow 路由器（设计文档 §19 阶段2）。

在新版 Skill Router 全量上线前，以 Shadow 模式并行执行新 Router，
对比新旧结果差异，用于离线评估与回归验证，不影响生产链路。

核心约束（§19 阶段2）：
    - 异步执行新 Router，**500ms 超时即丢弃**（不阻塞主链路）
    - 结果**不写入 AgentState**（Shadow 结果仅供对比，不参与下游）
    - ``_record_diff`` 记录新旧差异，**query 只存 hash**（脱敏，§13.4.2 约束6）

类比 Java：
    ``ShadowSkillRouter`` ≈ ``@Component`` 异步旁路服务，
    通过 ``CompletableFuture.orTimeout`` 实现超时丢弃，差异写入独立审计表。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Shadow 执行默认超时（ms）
_DEFAULT_TIMEOUT_MS = 500
# diff 缓冲上限（避免无界增长）
_MAX_DIFF_BUFFER = 1000


class ShadowSkillRouter:
    """Shadow 路由器（设计文档 §19 阶段2）。

    通过注入一个 ``route_fn``（新 Router 的路由入口）执行 Shadow 路由。
    ``route_fn`` 可为同步或异步函数，签名 ``route_fn(query, context) -> Any``。

    类比 Java 中的 ``@Component``：无状态（除 diff 缓冲），可单例复用。
    """

    def __init__(
        self,
        route_fn: Callable[..., Any] | None = None,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    ) -> None:
        """
        Args:
            route_fn: 新 Router 路由入口，签名 ``(query, context) -> result``；
                      返回值可为 awaitable；为 None 时 Shadow 不执行
            timeout_ms: 超时阈值（毫秒），超时即丢弃
        """
        self._route_fn = route_fn
        self._timeout_ms = timeout_ms
        self._diffs: deque[dict[str, Any]] = deque(maxlen=_MAX_DIFF_BUFFER)

    async def shadow_route(
        self,
        query: str,
        context: dict[str, Any] | None,
        production_result: Any,
    ) -> None:
        """异步执行 Shadow 路由并记录差异。

        - 新 Router 在 ``timeout_ms`` 内未返回则丢弃（不抛异常）
        - 结果不写入 AgentState（本方法返回 None，不产生副作用）
        - 超时 / 异常均记录 debug 日志后吞掉

        Args:
            query: 用户原始 query（仅存 hash）
            context: 路由上下文（透传给 route_fn）
            production_result: 生产 Router 的结果（用于对比）
        """
        if self._route_fn is None:
            return
        try:
            raw = self._route_fn(query, context)
            shadow_result = await asyncio.wait_for(
                self._maybe_await(raw),
                timeout=self._timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            logger.debug(
                "[ShadowRouter] 超时丢弃（>%dms）", self._timeout_ms
            )
            return
        except Exception as exc:
            # Shadow 异常不得影响主链路
            logger.debug("[ShadowRouter] 执行失败（已吞掉）: %s", exc)
            return

        if shadow_result is not None:
            self._record_diff(query, production_result, shadow_result)

    def _record_diff(
        self,
        query: str,
        production_result: Any,
        shadow_result: Any,
    ) -> None:
        """记录新旧路由差异（脱敏：query 仅存 hash）。

        差异记录供离线分析使用，不进入 AgentState。
        """
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        diff = {
            "query_hash": query_hash,
            "production": self._summarize(production_result),
            "shadow": self._summarize(shadow_result),
            "is_divergent": self._is_divergent(
                production_result, shadow_result
            ),
            "timestamp": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        self._diffs.append(diff)
        if diff["is_divergent"]:
            logger.info(
                "[ShadowRouter] 检测到路由差异: query_hash=%s, prod=%s, shadow=%s",
                query_hash, diff["production"], diff["shadow"],
            )

    @property
    def diffs(self) -> list[dict[str, Any]]:
        """已记录的差异列表（只读副本）。"""
        return list(self._diffs)

    # ========== 内部方法 ==========
    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        """兼容同步 / 异步 route_fn：若是 awaitable 则等待，否则直接返回。"""
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _summarize(result: Any) -> dict[str, Any]:
        """提取路由结果摘要（skill_id / outcome）。

        兼容 ``SkillRouteResponse`` 对象与 dict 两种形态。
        """
        if result is None:
            return {"skill_id": None, "outcome": None}
        if isinstance(result, dict):
            return {
                "skill_id": result.get("skill_id"),
                "outcome": str(result.get("outcome")) if result.get("outcome") is not None else None,
            }
        # 对象形态：尝试取 skill_ref.skill_id 与 outcome
        skill_ref = getattr(result, "skill_ref", None)
        skill_id = getattr(skill_ref, "skill_id", None) if skill_ref is not None else None
        outcome = getattr(result, "outcome", None)
        return {
            "skill_id": skill_id,
            "outcome": str(outcome) if outcome is not None else None,
        }

    @staticmethod
    def _is_divergent(production_result: Any, shadow_result: Any) -> bool:
        """判定新旧结果是否发散（skill_id 或 outcome 不同即发散）。"""
        prod = ShadowSkillRouter._summarize(production_result)
        shad = ShadowSkillRouter._summarize(shadow_result)
        return prod.get("skill_id") != shad.get("skill_id") or \
            prod.get("outcome") != shad.get("outcome")
