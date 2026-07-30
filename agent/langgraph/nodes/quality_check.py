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
"""质量检查节点。

根据检索结果质量评分决定是否通过、重试或降级。

参考原有实现：
- agent/component/retry_controller.py: RetryController 组件
- agent/component/grader.py: Grader 组件的质量评估逻辑
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)

# 质量阈值（参考 Grader 和 RetryController 的配置）
QUALITY_PASS_THRESHOLD = 0.7

# RAG 模式下至少需要的相关文档数
MIN_RELEVANT_DOCS = 2


async def quality_check_node(state: AgentState) -> dict[str, Any]:
    """质量检查节点。

    根据当前工具调用的质量评分决定下一步行动：
    - pass：质量达标，进入 prompt_assembly
    - retry_rag：RAG 质量不达标，重试 RAG
    - retry_db：数据库质量不达标，重试数据库查询
    - fallback_web：重试次数用尽，降级到 Web 搜索

    当决策为重试时，自动递增 retry_count。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 quality_decision 和 retry_count
    """
    start_time = time.time()

    route_target = state.get("route_target", "chitchat")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    quality_score = 0.0
    has_relevant = False
    relevant_count = 0

    if route_target == "rag":
        quality_score = state.get("rag_quality_score", 0.0)
        has_relevant = state.get("rag_has_relevant", False)
        relevant_count = state.get("rag_relevant_count", 0)
    elif route_target == "database":
        quality_score = state.get("db_quality_score", 0.0)
        db_result = state.get("db_result", {})
        # 数据库查询：只要返回有效行数即视为相关，不强制要求 >= 2 行
        has_relevant = db_result.get("row_count", 0) > 0
        relevant_count = db_result.get("row_count", 0)

        # SQL Agent 探索终态适配（docs/数据库Tool渐进式披露LLM化落地设计.md §5.2）：
        # 空结果语义分化，消除无意义重试
        exploration_verdict = db_result.get("exploration_verdict", "")
        if exploration_verdict:
            decision = _decide_by_verdict(exploration_verdict, retry_count, max_retries)
            if decision is not None:
                logger.info(
                    f"[quality_check] DB 探索终态决策: verdict={exploration_verdict}, "
                    f"decision={decision}, retry={retry_count}/{max_retries}"
                )
                updates: dict[str, Any] = {
                    "quality_decision": decision,
                    "node_timings": {"quality_check": int((time.time() - start_time) * 1000)},
                }
                if decision == "retry_db":
                    updates["retry_count"] = retry_count + 1
                return updates
    elif route_target == "hybrid":
        rag_score = state.get("rag_quality_score", 0.0)
        db_score = state.get("db_quality_score", 0.0)
        quality_score = (rag_score + db_score) / 2 if db_score > 0 else rag_score
        has_relevant = state.get("rag_has_relevant", False)
        relevant_count = state.get("rag_relevant_count", 0)

    decision = _make_decision(
        quality_score=quality_score,
        has_relevant=has_relevant,
        relevant_count=relevant_count,
        retry_count=retry_count,
        max_retries=max_retries,
        route_target=route_target,
    )

    logger.info(
        f"[quality_check] 决策: score={quality_score:.2f}, "
        f"relevant={relevant_count}, retry={retry_count}/{max_retries}, "
        f"decision={decision}"
    )

    updates: dict[str, Any] = {
        "quality_decision": decision,
        "node_timings": {"quality_check": int((time.time() - start_time) * 1000)},
    }

    # 重试决策需要递增 retry_count，避免无限循环
    if decision in ("retry_rag", "retry_db"):
        updates["retry_count"] = retry_count + 1

    return updates


def _decide_by_verdict(
    exploration_verdict: str,
    retry_count: int,
    max_retries: int,
) -> str | None:
    """按 SQL Agent 探索终态决策（docs §5.2）。

    Args:
        exploration_verdict: 探索终态（data_found / no_data_confirmed /
            exploration_failed / budget_exhausted）
        retry_count: 当前重试次数
        max_retries: 最大重试次数

    Returns:
        str | None: 决策结果；None 表示该 verdict 不改变默认决策流程
    """
    if exploration_verdict == "data_found":
        return "pass"
    if exploration_verdict == "no_data_confirmed":
        # LLM 确认库中无数据：不重试，由 prompt 层生成"未查询到数据"话术
        return "pass"
    if exploration_verdict == "exploration_failed":
        # 探索过程出错：受 retry_count 限制重试
        return "retry_db" if retry_count < max_retries else "fallback_web"
    if exploration_verdict == "budget_exhausted":
        # 预算已耗尽：不再重试（重试只会再次耗尽），降级 web 或保守回答
        return "fallback_web"
    return None


def _make_decision(
    quality_score: float,
    has_relevant: bool,
    relevant_count: int,
    retry_count: int,
    max_retries: int,
    route_target: str,
) -> str:
    """质量检查决策函数。

    决策优先级（参考 RetryController.evaluate_retry_conditions）：
    1. 闲聊/问候类查询 → 直接通过
    2. 质量达标 → 通过
       - RAG/Hybrid：score >= 0.7 且 relevant_count >= 2
       - Database：score >= 0.7 且 row_count > 0
    3. 重试次数未用尽 → 重试当前工具
    4. 重试次数用尽 → 降级到 Web 搜索

    Args:
        quality_score: 质量评分
        has_relevant: 是否有相关文档/结果
        relevant_count: 相关文档/结果数量
        retry_count: 当前重试次数
        max_retries: 最大重试次数
        route_target: 当前路由目标

    Returns:
        str: 决策结果（pass / retry_rag / retry_db / fallback_web）
    """
    if route_target == "chitchat":
        return "pass"

    quality_ok = quality_score >= QUALITY_PASS_THRESHOLD and has_relevant

    # 数据库模式：只要返回有效行数即通过；RAG/Hybrid 仍要求至少 2 条相关文档
    if route_target == "database":
        quality_ok = quality_score >= QUALITY_PASS_THRESHOLD and relevant_count > 0
    elif route_target in ("rag", "hybrid"):
        quality_ok = (
            quality_score >= QUALITY_PASS_THRESHOLD
            and has_relevant
            and relevant_count >= MIN_RELEVANT_DOCS
        )

    if quality_ok:
        return "pass"

    if retry_count < max_retries:
        if route_target in ("rag", "hybrid"):
            return "retry_rag"
        elif route_target == "database":
            return "retry_db"

    return "fallback_web"


def quality_check_decision(state: AgentState) -> str:
    """质量检查条件路由函数，用于 LangGraph 条件边。

    Args:
        state: 当前 AgentState

    Returns:
        str: 下一个节点名称
    """
    decision = state.get("quality_decision", "pass")

    if decision == "pass":
        return "prompt_assembly"
    elif decision == "retry_rag":
        return "rag_tool"
    elif decision == "retry_db":
        return "db_tool"
    elif decision == "fallback_web":
        return "web_tool"
    else:
        return "prompt_assembly"
