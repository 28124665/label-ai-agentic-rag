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

import copy
import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

"""Agent 标准状态字段定义模块。

对应需求：P2-FR-01（Agent 状态标准化）

功能说明：
  定义 Agent Canvas 工作流中所有组件共享的标准状态字段 schema，确保各组件之间
  的状态传递格式统一、类型安全、可序列化。

实现方式：
  1. STANDARD_STATE_FIELDS：定义所有标准字段名及其期望的 Python 类型，涵盖基础信息、
     检索、重试控制、生成、幻觉检测、工具调用等维度。
  2. DEFAULT_STATE_VALUES：为每个字段提供默认值，保证新字段向后兼容。
  3. SENSITIVE_STATE_FIELDS：标记需要在持久化时加密的敏感字段（如 messages、retrieved_docs）。
  4. CHECKPOINT_ENABLED_NODES：定义哪些节点类型会生成检查点快照，用于时间旅行功能。
  5. STATE_CONFIG_DEFAULTS：定义状态生命周期默认配置（加密开关、大小限制、TTL 等）。
  6. 提供 get_state/set_state/extract_canvas_state/apply_canvas_state 等工具函数，
     统一通过 "sys." 前缀读写 Canvas globals。
  7. upgrade_state：实现状态 schema 版本升级，自动填充缺失字段并修正类型不兼容的字段。
  8. clamp_state_size：实现状态大小控制，超限时先截断大文本字段，最终丢弃可重建的集合。
"""

# ---------------------------------------------------------------------------
# Standard Agent shared-state schema
# ---------------------------------------------------------------------------

# 状态 schema 版本号，用于持久化时的版本兼容和升级
STATE_SCHEMA_VERSION = "1.0"

# Map canonical field name -> expected Python type
STANDARD_STATE_FIELDS: Dict[str, type] = {
    # Basic
    "query": str,
    "messages": list,
    "thread_id": str,
    # Retrieval
    "retrieved_docs": list,
    "graded_docs": list,
    "rewritten_query": str,
    "sub_queries": list,
    "hypothetical_answer": str,
    "hyde_retrieval_query": str,
    # Retry
    "retry_count": int,
    "max_retries": int,
    "retry_history": list,
    "accumulated_retry_tokens": int,
    "should_retry": bool,
    "retry_exceeded": bool,
    "trigger_web_search_fallback": bool,
    "web_search_fallback_triggered": bool,
    "is_chitchat": bool,
    "selected_strategy": str,
    # Generation
    "final_answer": str,
    "answer_with_citations": str,
    # Hallucination detection
    "is_hallucination": bool,
    "faithfulness_score": float,
    "hallucination_retry_count": int,
    # Tool calls
    "tool_calls": list,
}

DEFAULT_STATE_VALUES: Dict[str, Any] = {
    "query": "",
    "messages": [],
    "thread_id": "",
    "retrieved_docs": [],
    "graded_docs": [],
    "rewritten_query": "",
    "sub_queries": [],
    "hypothetical_answer": "",
    "hyde_retrieval_query": "",
    "retry_count": 0,
    "max_retries": 3,
    "retry_history": [],
    "accumulated_retry_tokens": 0,
    "should_retry": False,
    "retry_exceeded": False,
    "trigger_web_search_fallback": False,
    "web_search_fallback_triggered": False,
    "is_chitchat": False,
    "selected_strategy": "",
    "final_answer": "",
    "answer_with_citations": "",
    "is_hallucination": False,
    "faithfulness_score": 0.0,
    "hallucination_retry_count": 0,
    "tool_calls": [],
}

# 需要在持久化（Redis 检查点/数据库）时进行 AES 加密的敏感字段列表。
# 这些字段可能包含用户对话内容、检索文档原文等隐私数据。
SENSITIVE_STATE_FIELDS: Tuple[str, ...] = (
    "messages",
    "retrieved_docs",
    "final_answer",
    "graded_docs",
)

# 启用检查点快照的节点类型列表。
# Canvas 工作流在执行到这些节点时会自动保存当前状态快照到 Redis，
# 支持后续的时间旅行（Replay）功能。
CHECKPOINT_ENABLED_NODES: Tuple[str, ...] = (
    "begin",
    "categorize",
    "retrieval",
    "generate",
    "generator",
)

# 状态生命周期默认配置，可通过环境变量覆盖。
# state_encryption_enabled: 是否对敏感字段加密（生产环境必须开启）
# state_max_size_mb: 状态序列化后的最大大小（MB），超限自动压缩
# state_ttl_seconds: 运行时状态在 Redis 中的过期时间
# checkpoint_ttl_days: 检查点快照的保留天数
STATE_CONFIG_DEFAULTS: Dict[str, Any] = {
    "state_encryption_enabled": True,
    "state_max_size_mb": 1,
    "state_ttl_seconds": 3600,
    "checkpoint_ttl_days": 7,
    "state_schema_version": STATE_SCHEMA_VERSION,
}

# Canvas stores shared state in ``canvas.globals`` using this prefix.
_CANVAS_STATE_PREFIX = "sys."
_SCHEMA_VERSION_KEY = "state_schema_version"


def canvas_state_key(field: str) -> str:
    """将标准字段名转换为 Canvas globals 中的存储 key。

    所有标准字段在 Canvas globals 中以 "sys." 前缀存储，
    例如 "query" -> "sys.query"，"graded_docs" -> "sys.graded_docs"。
    """
    return f"{_CANVAS_STATE_PREFIX}{field}"


def get_state(canvas, field: str, default: Any = None) -> Any:
    """Read a canonical state field from a Canvas instance."""
    if default is None:
        default = DEFAULT_STATE_VALUES.get(field)
    return canvas.globals.get(canvas_state_key(field), default)


def set_state(canvas, field: str, value: Any) -> None:
    """Write a canonical state field to a Canvas instance."""
    canvas.globals[canvas_state_key(field)] = value


def extract_canvas_state(canvas) -> Dict[str, Any]:
    """Extract the standard state from a Canvas instance."""
    state: Dict[str, Any] = {}
    for field in STANDARD_STATE_FIELDS:
        state[field] = canvas.globals.get(canvas_state_key(field), DEFAULT_STATE_VALUES[field])
    state[_SCHEMA_VERSION_KEY] = canvas.globals.get(canvas_state_key(_SCHEMA_VERSION_KEY)) or STATE_SCHEMA_VERSION
    return state


def apply_canvas_state(canvas, state: Dict[str, Any]) -> None:
    """Apply a standard state dict to a Canvas instance."""
    for field, default in DEFAULT_STATE_VALUES.items():
        canvas.globals[canvas_state_key(field)] = state.get(field, default)
    canvas.globals[canvas_state_key(_SCHEMA_VERSION_KEY)] = state.get(_SCHEMA_VERSION_KEY, STATE_SCHEMA_VERSION)


def upgrade_state(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """将原始状态字典升级到当前 schema 版本。

    升级策略：
      - 缺失字段：使用 DEFAULT_STATE_VALUES 中的默认值填充
      - 类型不兼容：尝试类型转换（如 int->str），转换失败则重置为默认值
      - 列表类型：尝试 list() 转换，None 转为空列表
      - 布尔类型：尝试 bool() 转换

    这保证了旧版本保存的检查点在加载后能正常使用新版本的字段。
    """
    if not isinstance(state, dict):
        state = {}

    upgraded = copy.deepcopy(state)
    for field, default in DEFAULT_STATE_VALUES.items():
        if field not in upgraded:
            upgraded[field] = copy.deepcopy(default)
            continue

        expected = STANDARD_STATE_FIELDS.get(field)
        value = upgraded[field]
        if expected is not None and not isinstance(value, expected):
            try:
                if expected is list:
                    upgraded[field] = list(value) if value is not None else []
                elif expected is bool:
                    upgraded[field] = bool(value)
                else:
                    upgraded[field] = expected(value)
            except Exception:
                logging.warning(
                    "State field '%s' has incompatible type %s, resetting to default",
                    field,
                    type(value).__name__,
                )
                upgraded[field] = copy.deepcopy(default)

    upgraded[_SCHEMA_VERSION_KEY] = STATE_SCHEMA_VERSION
    return upgraded


def upgrade_canvas_globals(globals_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure a Canvas ``globals`` dict conforms to the current state schema.

    Supports both prefixed (``sys.<field>``) and legacy unprefixed field names.
    """
    state: Dict[str, Any] = {}
    for field in STANDARD_STATE_FIELDS:
        prefixed = canvas_state_key(field)
        if prefixed in globals_dict:
            state[field] = globals_dict[prefixed]
        elif field in globals_dict:
            state[field] = globals_dict[field]

    state = upgrade_state(state)

    for field, value in state.items():
        globals_dict[canvas_state_key(field)] = value

    return globals_dict


# ---------------------------------------------------------------------------
# State size control
# ---------------------------------------------------------------------------

LARGE_STATE_FIELDS: Tuple[str, ...] = SENSITIVE_STATE_FIELDS + (
    "answer_with_citations",
    "rewritten_query",
)


def _estimate_size(obj: Any) -> int:
    """估算状态字典序列化后的字节大小，用于判断是否需要压缩。"""
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return 0


def _truncate_text(text: str, max_bytes: int) -> str:
    """截断文本使其 UTF-8 编码不超过指定字节数。
    使用二分查找定位最长可保留前缀，并追加 '...[truncated]' 后缀。
    """
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False, default=str)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Binary search for the longest prefix that fits.
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(text[:mid].encode("utf-8")) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    prefix = text[:lo]
    suffix = "...[truncated]"
    # Ensure the suffix itself fits; if not, shorten it.
    room = max_bytes - len(prefix.encode("utf-8"))
    if room > 0:
        suffix = suffix.encode("utf-8")[:room].decode("utf-8", errors="ignore")
    return prefix + suffix


def _clamp_value(value: Any, max_bytes: int) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, max_bytes)
    if isinstance(value, list):
        clamped: List[Any] = []
        per_item = max(256, max_bytes // max(1, len(value))) if value else max_bytes
        for item in value:
            if isinstance(item, dict):
                item_copy = copy.deepcopy(item)
                for content_key in ("content", "text", "answer", "value", "reason", "evidence"):
                    if content_key in item_copy and isinstance(item_copy[content_key], str):
                        item_copy[content_key] = _truncate_text(item_copy[content_key], per_item)
                clamped.append(item_copy)
            elif isinstance(item, str):
                clamped.append(_truncate_text(item, per_item))
            else:
                clamped.append(item)
            if _estimate_size(clamped) >= max_bytes:
                break
        return clamped
    if isinstance(value, dict):
        return {k: _clamp_value(v, max_bytes // max(1, len(value))) for k, v in value.items()}
    return value


def clamp_state_size(
    state: Dict[str, Any],
    max_size_mb: Optional[float] = None,
    large_fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """将状态字典压缩到配置的大小限制以内。

    压缩策略（按优先级）：
      1. 如果总大小未超限，直接返回深拷贝
      2. 对大文本字段（messages、retrieved_docs 等）逐字段截断
      3. 如果仍超限，丢弃可重建的集合（retrieved_docs、graded_docs、messages）
      4. 最终兜底：截断 final_answer 等核心文本字段

    这确保了 Redis 中存储的状态不会因文档过多而超出内存限制。
    """
    if max_size_mb is None:
        max_size_mb = STATE_CONFIG_DEFAULTS["state_max_size_mb"]
    max_bytes = int(max_size_mb * 1024 * 1024)

    if _estimate_size(state) <= max_bytes:
        return copy.deepcopy(state)

    reduced = copy.deepcopy(state)
    fields = tuple(large_fields) if large_fields else LARGE_STATE_FIELDS
    per_field = max(1024, max_bytes // max(1, len(fields)))

    for field in fields:
        if field not in reduced:
            continue
        reduced[field] = _clamp_value(reduced[field], per_field)
        if _estimate_size(reduced) <= max_bytes:
            return reduced

    # Final fallback: drop the bulkiest reproducible collections.
    for field in ("retrieved_docs", "graded_docs", "messages"):
        reduced[field] = []
    for field in ("final_answer", "answer_with_citations", "rewritten_query"):
        if isinstance(reduced.get(field), str):
            reduced[field] = _truncate_text(reduced[field], max(256, max_bytes // 10))

    return reduced
