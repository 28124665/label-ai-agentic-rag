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
"""Langfuse LLM 可观测性客户端。

环境变量:
- LANGFUSE_PUBLIC_KEY: Langfuse 公钥
- LANGFUSE_SECRET_KEY: Langfuse 私钥
- LANGFUSE_HOST: Langfuse 后端地址(自建)
- LANGFUSE_ENVIRONMENT: 环境标识 (production/staging/development)
"""

import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_client = None


def get_langfuse_client():
    """获取 Langfuse 客户端单例。未配置或初始化失败时返回 None。"""
    global _client

    if _client is not None:
        return _client

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    host = os.getenv("LANGFUSE_HOST", "")

    if not all([public_key, secret_key, host]):
        logger.debug("Langfuse not configured, LLM tracing disabled")
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


def _get_environment() -> str:
    return os.getenv("LANGFUSE_ENVIRONMENT", os.getenv("ENV", "production"))


def _safe_json(obj: Any) -> Any:
    """安全转换对象为 JSON 可序列化格式。"""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_safe_json(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_json(v) for k, v in obj.items()}
    return str(obj)


def record_llm_generation(
    model: str,
    tokens: int,
    duration_ms: float,
    status: str = "success",
    trace_id: str = "",
    # === P0: 核心参数 ===
    input: Optional[Any] = None,
    output: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    # === P1: 增强参数 ===
    name: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    retry_count: int = 0,
    error_type: Optional[str] = None,
) -> None:
    """记录一次 LLM 调用到 Langfuse。

    在 chat_model.py 的 _record_generation_metrics 中调用,
    Langfuse 未配置时静默跳过。

    Args:
        model: 模型名
        tokens: 总 token 数 (兼容旧参数)
        duration_ms: 延迟毫秒
        status: 成功/失败
        trace_id: Agent trace_id,与 Jaeger/Sentry 关联
        input: 完整的 prompt messages (P0)
        output: 完整的模型输出文本 (P0)
        prompt_tokens: 输入 token 数 (P0)
        completion_tokens: 输出 token 数 (P0)
        total_tokens: 总 token 数 (P0, 优先于 tokens)
        name: 调用用途标识如 intent_router/planner/reflection (P1)
        temperature: 温度参数 (P1)
        max_tokens: 最大 token 限制 (P1)
        start_time: 调用开始时间戳 (P1)
        end_time: 调用结束时间戳 (P1)
        retry_count: 重试次数 (P1)
        error_type: 错误类型 (P1)
    """
    client = get_langfuse_client()
    if client is None:
        return

    try:
        effective_total = total_tokens or tokens
        effective_prompt = prompt_tokens or 0
        effective_completion = completion_tokens or 0

        # 如果只给了 prompt_tokens 和 completion_tokens，自动计算 total
        if prompt_tokens is not None and completion_tokens is not None and total_tokens is None:
            effective_total = prompt_tokens + completion_tokens

        # 如果只给了 total 但没有拆分的，用默认值
        if prompt_tokens is None and completion_tokens is None:
            effective_prompt = effective_total
            effective_completion = 0

        usage = {
            "prompt_tokens": effective_prompt,
            "completion_tokens": effective_completion,
            "total_tokens": effective_total,
        }

        generation_name = name or f"llm.{model}"
        trace_name = name or f"llm.{model}"

        trace = client.trace(
            name=trace_name,
            id=trace_id if trace_id else None,
            input=_safe_json(input) if input is not None else None,
            output=output,
            metadata={
                "model": model,
                "tokens": effective_total,
                "duration_ms": duration_ms,
                "status": status,
                "retry_count": retry_count,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "environment": _get_environment(),
            },
        )
        trace.generation(
            name=generation_name,
            model=model,
            input=_safe_json(input) if input is not None else None,
            output=output,
            usage=usage,
            metadata={
                "duration_ms": duration_ms,
                "status": status,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "retry_count": retry_count,
                "error_type": error_type,
                "environment": _get_environment(),
                "start_time": start_time,
                "end_time": end_time or time.time(),
            },
        )
    except Exception as e:
        logger.debug(f"Langfuse record failed: {e}")


def record_llm_embedding(
    model: str,
    input_texts: list,
    tokens: int,
    duration_ms: float,
    status: str = "success",
    trace_id: str = "",
    output_dim: Optional[int] = None,
) -> None:
    """记录一次 Embedding 调用到 Langfuse。

    在 embedding_model.py 的 encode/encode_queries 中调用。

    Args:
        model: 模型名
        input_texts: 输入文本列表
        tokens: 消费的 token 数
        duration_ms: 延迟毫秒
        status: 成功/失败
        trace_id: Agent trace_id
        output_dim: 输出向量维度
    """
    client = get_langfuse_client()
    if client is None:
        return

    try:
        text_count = len(input_texts) if isinstance(input_texts, list) else 1
        trace = client.trace(
            name=f"embedding.{model}",
            id=trace_id if trace_id else None,
            metadata={
                "model": model,
                "tokens": tokens,
                "duration_ms": duration_ms,
                "status": status,
                "text_count": text_count,
                "output_dim": output_dim,
                "environment": _get_environment(),
            },
        )
        trace.generation(
            name=f"embedding.{model}",
            model=model,
            input=_safe_json([str(t)[:200] for t in (input_texts if isinstance(input_texts, list) else [input_texts])]),
            usage={"total_tokens": tokens, "prompt_tokens": tokens, "completion_tokens": 0},
            metadata={
                "text_count": text_count,
                "output_dim": output_dim,
                "duration_ms": duration_ms,
                "status": status,
                "environment": _get_environment(),
            },
        )
    except Exception as e:
        logger.debug(f"Langfuse embedding record failed: {e}")


def record_llm_rerank(
    model: str,
    query: str,
    documents: list,
    tokens: int,
    duration_ms: float,
    status: str = "success",
    trace_id: str = "",
) -> None:
    """记录一次 Rerank 调用到 Langfuse。

    在 rerank_model.py 的 similarity 中调用。

    Args:
        model: 模型名
        query: 查询文本
        documents: 文档列表
        tokens: 消费的 token 数
        duration_ms: 延迟毫秒
        status: 成功/失败
        trace_id: Agent trace_id
    """
    client = get_langfuse_client()
    if client is None:
        return

    try:
        doc_count = len(documents) if isinstance(documents, list) else 0
        trace = client.trace(
            name=f"rerank.{model}",
            id=trace_id if trace_id else None,
            metadata={
                "model": model,
                "tokens": tokens,
                "duration_ms": duration_ms,
                "status": status,
                "doc_count": doc_count,
                "environment": _get_environment(),
            },
        )
        trace.generation(
            name=f"rerank.{model}",
            model=model,
            input={"query": str(query)[:500], "document_count": doc_count},
            usage={"total_tokens": tokens, "prompt_tokens": tokens, "completion_tokens": 0},
            metadata={
                "doc_count": doc_count,
                "duration_ms": duration_ms,
                "status": status,
                "environment": _get_environment(),
            },
        )
    except Exception as e:
        logger.debug(f"Langfuse rerank record failed: {e}")
