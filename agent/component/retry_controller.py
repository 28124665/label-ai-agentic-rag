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
import logging
import os
from abc import ABC
from typing import Any

from agent.component.base import ComponentBase, ComponentParamBase
from agent.component.state_fields import get_state, set_state
from api.utils import metrics
from common.connection_utils import timeout

"""分级重试控制器组件。

对应需求：P2-FR-02（查询重写 - 分级重试控制）

功能说明：
  根据检索结果质量决定是否触发查询重写重试，控制重试次数和 Token 成本上限，
  在检索质量和响应延迟之间取得平衡。

实现方式：
  1. 触发重试的条件（全部满足才重试）：
     - has_relevant=False 或 relevant_count < min_relevant_docs（默认 2）
     - retry_count < max_retries（默认 3）
     - accumulated_retry_tokens < max_retry_tokens（默认 2000）
  2. 不触发重试的场景：
     - 闲聊/问候类查询（is_chitchat=True）
     - 已触发 Web 搜索兜底（web_search_fallback_triggered=True）
     - 检索结果已足够相关
  3. 停止条件（触发 Web 搜索兜底）：
     - retry_count >= max_retries
     - accumulated_retry_tokens >= max_retry_tokens
  4. 每次重试记录历史（query、strategy、result_count），
     并递增 retry_count 和记录 metrics
"""


DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_RETRY_TOKENS = 2000
DEFAULT_MIN_RELEVANT_DOCS = 2


class RetryControllerParam(ComponentParamBase):
    """
    Define the RetryController component parameters.
    """

    def __init__(self):
        super().__init__()
        self.has_relevant = "sys.has_relevant"
        self.relevant_count = "sys.relevant_count"
        self.retry_count = "sys.retry_count"
        self.accumulated_retry_tokens = "sys.accumulated_retry_tokens"
        self.max_retries = DEFAULT_MAX_RETRIES
        self.max_retry_tokens = DEFAULT_MAX_RETRY_TOKENS
        self.min_relevant_docs = DEFAULT_MIN_RELEVANT_DOCS
        self.is_chitchat = "sys.is_chitchat"
        self.web_search_fallback_triggered = "sys.web_search_fallback_triggered"
        self.strategy = "sys.selected_strategy"

    def check(self):
        self.check_defined_type(self.has_relevant, "[RetryController] has_relevant", ["str"])
        self.check_defined_type(self.relevant_count, "[RetryController] relevant_count", ["str"])
        self.check_defined_type(self.retry_count, "[RetryController] retry_count", ["str"])
        self.check_defined_type(self.accumulated_retry_tokens, "[RetryController] accumulated_retry_tokens", ["str"])
        self.check_nonnegative_number(self.max_retries, "[RetryController] max_retries")
        self.check_nonnegative_number(self.max_retry_tokens, "[RetryController] max_retry_tokens")
        self.check_nonnegative_number(self.min_relevant_docs, "[RetryController] min_relevant_docs")
        self.check_defined_type(self.is_chitchat, "[RetryController] is_chitchat", ["str"])
        self.check_defined_type(self.web_search_fallback_triggered, "[RetryController] web_search_fallback_triggered", ["str"])


class RetryController(ComponentBase, ABC):
    """重试控制器组件。

    评估检索结果质量，决定是否触发查询重写重试。
    作为 Canvas 工作流中的条件判断节点，输出 should_retry 信号
    控制工作流是否回到查询重写节点重新执行。
    """
    component_name = "RetryController"

    def get_input_elements(self) -> dict[str, Any]:
        res = {}
        for key in (
            self._param.has_relevant,
            self._param.relevant_count,
            self._param.retry_count,
            self._param.accumulated_retry_tokens,
            self._param.is_chitchat,
            self._param.web_search_fallback_triggered,
            self._param.strategy,
        ):
            res.update(self.get_input_elements_from_text(key))
        return res

    def get_input_form(self) -> dict[str, dict]:
        return {
            "has_relevant": {"name": "Has Relevant", "type": "line"},
            "relevant_count": {"name": "Relevant Count", "type": "line"},
            "retry_count": {"name": "Retry Count", "type": "line"},
            "accumulated_retry_tokens": {"name": "Accumulated Retry Tokens", "type": "line"},
        }

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    def _invoke(self, **kwargs):
        has_relevant = self._resolve_bool(kwargs, self._param.has_relevant, default=True)
        relevant_count = self._resolve_int(kwargs, self._param.relevant_count, default=0)
        retry_count = self._resolve_int(kwargs, self._param.retry_count, default=0)
        accumulated_retry_tokens = self._resolve_int(
            kwargs, self._param.accumulated_retry_tokens, default=0
        )
        is_chitchat = self._resolve_bool(kwargs, self._param.is_chitchat, default=False)
        web_search_fallback_triggered = self._resolve_bool(
            kwargs, self._param.web_search_fallback_triggered, default=False
        )
        strategy = self._resolve_str(kwargs, self._param.strategy, default="")

        decision = evaluate_retry_conditions(
            has_relevant=has_relevant,
            relevant_count=relevant_count,
            retry_count=retry_count,
            accumulated_retry_tokens=accumulated_retry_tokens,
            max_retries=self._param.max_retries,
            max_retry_tokens=self._param.max_retry_tokens,
            min_relevant_docs=self._param.min_relevant_docs,
            is_chitchat=is_chitchat,
            web_search_fallback_triggered=web_search_fallback_triggered,
        )

        # Update retry state when a retry is actually triggered.
        if decision["should_retry"]:
            retry_count += 1
            retry_history = get_state(self._canvas, "retry_history", default=[])
            retry_history.append({
                "query": self._canvas.get_variable_value("sys.query") or "",
                "strategy": strategy or "unknown",
                "result_count": relevant_count,
            })
            try:
                set_state(self._canvas, "retry_count", retry_count)
                set_state(self._canvas, "retry_history", retry_history)
            except Exception as e:
                logging.warning(f"[RetryController] Failed to update retry state: {e}")
            try:
                metrics.record_retry(strategy=strategy or "unknown")
            except Exception as e:
                logging.warning(f"[RetryController] Failed to record retry metric: {e}")

        self.set_output("should_retry", decision["should_retry"])
        self.set_output("retry_exceeded", decision["retry_exceeded"])
        self.set_output("trigger_web_search_fallback", decision["trigger_web_search_fallback"])
        self.set_output("retry_count", retry_count)
        self.set_output("stop_reason", decision["stop_reason"])

        try:
            set_state(self._canvas, "should_retry", decision["should_retry"])
            set_state(self._canvas, "retry_exceeded", decision["retry_exceeded"])
            set_state(self._canvas, "trigger_web_search_fallback", decision["trigger_web_search_fallback"])
        except Exception as e:
            logging.warning(f"[RetryController] Failed to write shared state: {e}")

    def _resolve_bool(self, kwargs: dict, key: str, default: bool = False) -> bool:
        value = self._resolve_value(kwargs, key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    def _resolve_int(self, kwargs: dict, key: str, default: int = 0) -> int:
        value = self._resolve_value(kwargs, key)
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _resolve_str(self, kwargs: dict, key: str, default: str = "") -> str:
        value = self._resolve_value(kwargs, key)
        if value is None:
            return default
        return str(value)

    def _resolve_value(self, kwargs: dict, key: str) -> Any:
        if key in kwargs:
            return kwargs[key]
        return self._canvas.get_variable_value(key)

    def thoughts(self) -> str:
        return "Evaluating retrieval quality and deciding whether to trigger a rewrite retry."


def evaluate_retry_conditions(
    has_relevant: bool,
    relevant_count: int,
    retry_count: int,
    accumulated_retry_tokens: int,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_retry_tokens: int = DEFAULT_MAX_RETRY_TOKENS,
    min_relevant_docs: int = DEFAULT_MIN_RELEVANT_DOCS,
    is_chitchat: bool = False,
    web_search_fallback_triggered: bool = False,
) -> dict[str, Any]:
    """评估是否应触发查询重写重试。

    决策逻辑（按优先级）：
      1. 已触发 Web 搜索兜底 -> 不重试（stop_reason: web_search_fallback_already_triggered）
      2. 闲聊/问候 -> 不重试（stop_reason: chitchat_or_greeting）
      3. 检索质量足够（has_relevant=True 且 relevant_count >= min_relevant_docs）-> 不重试
      4. 重试次数已达上限 -> 触发 Web 搜索兜底（stop_reason: max_retries_reached）
      5. Token 成本已达上限 -> 触发 Web 搜索兜底（stop_reason: max_retry_tokens_reached）
      6. 以上均不满足 -> 触发重试（stop_reason: retrieval_quality_below_threshold）

    Returns:
        包含 should_retry、retry_exceeded、trigger_web_search_fallback、stop_reason 的字典
    """
    result = {
        "should_retry": False,
        "retry_exceeded": False,
        "trigger_web_search_fallback": False,
        "stop_reason": "",
    }

    if web_search_fallback_triggered:
        result["stop_reason"] = "web_search_fallback_already_triggered"
        return result

    if is_chitchat:
        result["stop_reason"] = "chitchat_or_greeting"
        return result

    needs_retry = not has_relevant or relevant_count < min_relevant_docs
    if not needs_retry:
        result["stop_reason"] = "sufficient_relevant_docs"
        return result

    if retry_count >= max_retries:
        result["retry_exceeded"] = True
        result["trigger_web_search_fallback"] = True
        result["stop_reason"] = "max_retries_reached"
        return result

    if accumulated_retry_tokens >= max_retry_tokens:
        result["retry_exceeded"] = True
        result["trigger_web_search_fallback"] = True
        result["stop_reason"] = "max_retry_tokens_reached"
        return result

    result["should_retry"] = True
    result["stop_reason"] = "retrieval_quality_below_threshold"
    return result
