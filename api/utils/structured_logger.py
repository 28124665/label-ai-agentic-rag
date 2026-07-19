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
"""JSON 结构化日志工具。

对应需求：P2-FR-06（全链路可观测性 - 结构化日志）

功能说明：
  输出单行 JSON 格式的结构化日志，便于 Promtail/Fluent Bit 等日志采集器解析。
  自动脱敏敏感字段（用户查询、对话内容、文档原文等）。

实现方式：
  1. JSONFormatter：将 LogRecord 格式化为 JSON，包含 trace_id、span_id、
     node_name、duration_ms、status 等可观测性字段
  2. _SensitiveFieldFilter：递归遍历 metadata 字典，对敏感 key
     （query/answer/messages/content 等）执行前缀+后缀脱敏
     （如 "用户的问题内容" -> "用户...内容"）
  3. 便捷日志函数：
     - log_node_execution：组件/节点执行事件
     - log_retrieval：检索事件（含 result_count、kb_id）
     - log_generation：生成事件（含 model、tokens）
     - log_grader：Grader 评估事件（含 fallback_reason、relevant_count）
     - log_hallucination_detection：幻觉检测事件（含 faithfulness_score、action）
     - log_workflow：端到端工作流事件
     - log_error：错误事件
  4. 日志级别通过 RAGFLOW_STRUCTURED_LOG_LEVEL 环境变量配置
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any


class _SensitiveFieldFilter:
    """Mask sensitive fields in log metadata dictionaries."""

    _SENSITIVE_KEYS = frozenset(
        {
            "query",
            "question",
            "answer",
            "final_answer",
            "messages",
            "content",
            "content_with_weight",
            "retrieved_docs",
            "graded_docs",
            "evidence",
            "prompt",
            "history",
            "input",
        }
    )
    _MASKED = "***"

    def mask(self, value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for k, v in value.items():
                if k in self._SENSITIVE_KEYS:
                    result[k] = self._mask_value(v)
                else:
                    result[k] = self.mask(v)
            return result
        if isinstance(value, list):
            return [self.mask(item) for item in value]
        return value

    def _mask_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._mask_text(value)
        if isinstance(value, (list, tuple)):
            return [self._mask_text(item) if isinstance(item, str) else self._MASKED for item in value]
        return self._MASKED

    @staticmethod
    def _mask_text(text: str, keep_prefix: int = 2, keep_suffix: int = 2) -> str:
        if not isinstance(text, str):
            return str(text)
        if len(text) <= keep_prefix + keep_suffix + 3:
            return "*" * len(text)
        return f"{text[:keep_prefix]}...{text[-keep_suffix:]}"


_SENSITIVE_FILTER = _SensitiveFieldFilter()


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "thread_id": getattr(record, "thread_id", ""),
            "trace_id": getattr(record, "trace_id", ""),
            "span_id": getattr(record, "span_id", ""),
            "node_name": getattr(record, "node_name", ""),
            "duration_ms": getattr(record, "duration_ms", None),
            "status": getattr(record, "status", ""),
            "metadata": getattr(record, "metadata", None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Drop None values to keep logs compact.
        payload = {k: v for k, v in payload.items() if v is not None and v != ""}
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_structured_logger(name: str = "ragflow.observability") -> logging.Logger:
    """Return a logger configured for structured JSON output."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    level = os.environ.get("RAGFLOW_STRUCTURED_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))
    return logger


_STRUCTURED_LOGGER = get_structured_logger()


def _make_record(
    message: str,
    level: int = logging.INFO,
    trace_id: str = "",
    span_id: str = "",
    node_name: str = "",
    duration_ms: float | None = None,
    status: str = "",
    metadata: dict[str, Any] | None = None,
    mask: bool = True,
) -> logging.LogRecord:
    """Build a LogRecord with extra observability fields."""
    if mask and metadata:
        metadata = _SENSITIVE_FILTER.mask(metadata)
    record = _STRUCTURED_LOGGER.makeRecord(
        _STRUCTURED_LOGGER.name,
        level,
        "(unknown file)",
        0,
        message,
        (),
        None,
    )
    record.trace_id = trace_id
    record.span_id = span_id
    record.node_name = node_name
    record.duration_ms = duration_ms
    record.status = status
    record.metadata = metadata
    return record


def log_node_execution(
    trace_id: str,
    span_id: str,
    node_name: str,
    duration_ms: float,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a component/node execution event."""
    _STRUCTURED_LOGGER.handle(
        _make_record(
            f"Node {node_name} executed",
            trace_id=trace_id,
            span_id=span_id,
            node_name=node_name,
            duration_ms=duration_ms,
            status=status,
            metadata=metadata,
        )
    )


def log_retrieval(
    trace_id: str,
    span_id: str,
    duration_ms: float,
    status: str,
    result_count: int = 0,
    kb_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a retrieval node event."""
    metadata = metadata or {}
    metadata.update({"result_count": result_count, "kb_id": kb_id})
    _STRUCTURED_LOGGER.handle(
        _make_record(
            "Retrieval completed",
            trace_id=trace_id,
            span_id=span_id,
            node_name="retrieval",
            duration_ms=duration_ms,
            status=status,
            metadata=metadata,
        )
    )


def log_generation(
    trace_id: str,
    span_id: str,
    duration_ms: float,
    status: str,
    model: str = "",
    tokens: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an answer generation event."""
    metadata = metadata or {}
    metadata.update({"model": model, "tokens": tokens})
    _STRUCTURED_LOGGER.handle(
        _make_record(
            "Generation completed",
            trace_id=trace_id,
            span_id=span_id,
            node_name="generate",
            duration_ms=duration_ms,
            status=status,
            metadata=metadata,
        )
    )


def log_grader(
    trace_id: str,
    span_id: str,
    duration_ms: float,
    status: str,
    fallback_reason: str = "",
    relevant_count: int = 0,
    total_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a Grader evaluation event."""
    metadata = metadata or {}
    metadata.update(
        {
            "fallback_reason": fallback_reason or "none",
            "relevant_count": relevant_count,
            "total_count": total_count,
        }
    )
    _STRUCTURED_LOGGER.handle(
        _make_record(
            "Grader evaluation completed",
            trace_id=trace_id,
            span_id=span_id,
            node_name="grader",
            duration_ms=duration_ms,
            status=status,
            metadata=metadata,
        )
    )


def log_hallucination_detection(
    trace_id: str,
    span_id: str,
    duration_ms: float,
    status: str,
    faithfulness_score: float,
    hallucination_count: int = 0,
    action: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a hallucination detection event."""
    metadata = metadata or {}
    metadata.update(
        {
            "faithfulness_score": faithfulness_score,
            "hallucination_count": hallucination_count,
            "action": action or "none",
        }
    )
    _STRUCTURED_LOGGER.handle(
        _make_record(
            "Hallucination detection completed",
            trace_id=trace_id,
            span_id=span_id,
            node_name="hallucination_detector",
            duration_ms=duration_ms,
            status=status,
            metadata=metadata,
        )
    )


def log_workflow(
    trace_id: str,
    span_id: str,
    duration_ms: float,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an end-to-end workflow event."""
    _STRUCTURED_LOGGER.handle(
        _make_record(
            "Workflow completed",
            trace_id=trace_id,
            span_id=span_id,
            node_name="workflow",
            duration_ms=duration_ms,
            status=status,
            metadata=metadata,
        )
    )


def log_error(
    trace_id: str,
    span_id: str,
    node_name: str,
    error: Exception | str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an error event."""
    message = str(error)
    _STRUCTURED_LOGGER.handle(
        _make_record(
            message,
            level=logging.ERROR,
            trace_id=trace_id,
            span_id=span_id,
            node_name=node_name,
            status="error",
            metadata=metadata,
        )
    )


def mask_text(text: str, keep_prefix: int = 2, keep_suffix: int = 2) -> str:
    """Mask a sensitive text value for safe logging."""
    return _SensitiveFieldFilter._mask_text(text, keep_prefix, keep_suffix)
