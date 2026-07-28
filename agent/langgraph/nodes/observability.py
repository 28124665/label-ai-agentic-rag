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
"""可观测性节点。

记录全局指标和结构化日志，包括端到端延迟、各节点耗时、工具调用次数等。

参考原有实现：
- api/utils/metrics.py: Prometheus 指标记录
- api/utils/structured_logger.py: 结构化日志
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


def observability_node(state: AgentState) -> dict[str, Any]:
    """可观测性节点。

    记录全局指标和结构化日志：
    - e2e_latency：端到端延迟
    - 各节点耗时：从 node_timings 中提取
    - 工具调用次数：根据 route_target 判断
    - 质量评分：rag_quality_score / db_quality_score
    - 幻觉检测分数：hallucination_score

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段（空字典，不修改状态）
    """
    start_time = time.time()

    trace_id = state.get("trace_id", "")
    node_timings = state.get("node_timings", {})
    route_target = state.get("route_target", "chitchat")
    retry_count = state.get("retry_count", 0)
    rag_quality_score = state.get("rag_quality_score", 0.0)
    db_quality_score = state.get("db_quality_score", 0.0)
    hallucination_score = state.get("hallucination_score", 0.0)
    hallucination_action = state.get("hallucination_action", "pass")
    generated_answer = state.get("generated_answer", "")

    # 计算端到端延迟
    total_node_latency = sum(node_timings.values()) if node_timings else 0
    e2e_latency_ms = int((time.time() - start_time) * 1000) + total_node_latency

    # 记录结构化日志
    log_data = {
        "trace_id": trace_id,
        "route_target": route_target,
        "retry_count": retry_count,
        "e2e_latency_ms": e2e_latency_ms,
        "node_timings": node_timings,
        "rag_quality_score": rag_quality_score,
        "db_quality_score": db_quality_score,
        "hallucination_score": hallucination_score,
        "hallucination_action": hallucination_action,
        "answer_length": len(generated_answer),
    }

    logger.info(f"[observability] 全局指标: {log_data}")

    # 记录 Prometheus 指标（如果可用）
    try:
        from api.utils import metrics

        # 记录端到端延迟
        metrics.record_e2e_latency(e2e_latency_ms / 1000.0)

        # 记录幻觉检测分数
        if hallucination_score > 0:
            metrics.record_hallucination_score(
                hallucination_score, hallucination_action
            )
    except Exception as e:
        logger.warning(f"[observability] Prometheus 指标记录失败: {e}")

    # 记录结构化日志（如果可用）
    try:
        from api.utils.structured_logger import log_trace

        log_trace(
            trace_id=trace_id,
            duration_ms=e2e_latency_ms,
            status="success",
            metadata=log_data,
        )
    except Exception as e:
        logger.warning(f"[observability] 结构化日志记录失败: {e}")

    return {
        "node_timings": {
            **node_timings,
            "observability": int((time.time() - start_time) * 1000),
        },
    }
