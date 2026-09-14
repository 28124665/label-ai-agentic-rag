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
"""ReAct 子图数据模型定义。

定义受限 ReAct 子图内部使用的所有数据结构：
- ReactActionType: 工具 action 类型枚举
- ReactAction: 单步 action（tool_call / finish / ask_clarification）
- ReactObservation: 工具执行后的标准 observation
- ReactState: ReAct 子图内部状态（预算、action_history、observations）
- ReactExecutionResult: 子图最终输出（evidence 列表、step 摘要）

所有模型使用 TypedDict + total=False，缺失字段安全降级为 None。
"""
from __future__ import annotations

from typing import Literal, Optional, TypedDict


# ========== Action 类型常量 ==========
# 仅枚举 ReAct 允许的 action 类型，新增工具时需扩展并更新 Policy Guard 白名单
ACTION_TYPE_RAG_SEARCH = "rag_search"
ACTION_TYPE_DB_QUERY = "db_query"
ACTION_TYPE_WEB_SEARCH = "web_search"
ACTION_TYPE_ASK_CLARIFICATION = "ask_clarification"
ACTION_TYPE_FINISH = "finish"

# 工具名称到 action 类型的映射（与 agent_config.react.allowed_tools 对齐）
TOOL_NAME_TO_ACTION_TYPE = {
    "rag_search": ACTION_TYPE_RAG_SEARCH,
    "db_query": ACTION_TYPE_DB_QUERY,
    "web_search": ACTION_TYPE_WEB_SEARCH,
}


class ReactAction(TypedDict, total=False):
    """ReAct 单步 action。

    协议（详见 rag/prompts/react_step_v1.md）：
    - LLM 每一步必须输出 JSON
    - 含 thought_summary、action、stop 三个字段
    - 禁止自由文本动作
    """

    type: str  # rag_search / db_query / web_search / ask_clarification / finish
    tool_name: str  # 具体工具名（与 type 同义，保留用于可观测性）
    purpose: str  # 本次调用的目的（自然语言描述，便于审计）
    arguments: dict  # 工具参数
    thought_summary: str  # 思考摘要（日志记录，不进入下一轮 LLM 上下文）


class ReactObservation(TypedDict, total=False):
    """工具执行后的标准 observation。

    所有工具结果统一为此结构，再由 Evidence Normalizer 转为 Evidence。
    """

    action_type: str
    success: bool
    content: str  # 截断后的文本内容
    structured_data: dict  # 结构化数据（DB rows、metadata 等）
    error: str
    latency_ms: int
    risk_level: str  # low / medium / high
    truncated: bool  # 是否被截断
    metadata: dict  # 额外元数据


class ReactBudget(TypedDict, total=False):
    """ReAct 子图预算状态。

    每个字段都有"已用"和"上限"两部分，checked via budget.py。
    """

    step_count: int
    max_steps: int
    tool_call_count: int
    max_tool_calls: int
    db_query_count: int
    max_db_queries: int
    llm_call_count: int
    max_llm_calls: int
    deadline_ts: float  # unix timestamp
    token_used: int
    token_budget: int


class ReactState(TypedDict, total=False):
    """ReAct 子图内部状态。

    由 AgentState 派生（不修改 AgentState），仅在子图内部传递。
    """

    trace_id: str
    user_question: str
    tenant_id: str
    user_id: str
    query_lang: str
    task_type: str
    objective: str
    constraints: dict

    budget: ReactBudget

    allowed_tools: list[str]
    denied_tools: list[str]

    action_history: list[dict]  # 每一步完整 action
    observations: list[ReactObservation]
    failures: list[dict]

    next_action: ReactAction
    finish_reason: Literal[
        "completed",
        "budget_exhausted",
        "policy_denied",
        "tool_error",
        "needs_clarification",
        "error",
    ]
    needs_clarification: bool
    clarification_question: str


class ReactExecutionResult(TypedDict, total=False):
    """ReAct 子图最终输出（P0 修正版扩展）。

    由 LangGraph 主干读取，进入 evidence_fusion → answerability_check 流程。
    ReAct 子图不直接生成最终答案。
    """

    success: bool
    evidence: list[dict]  # 标准化后的 Evidence 列表
    step_count: int
    finish_reason: str
    summary: str  # 子图执行摘要（自然语言）

    # ★ P0 修正：终止信息（供主图 termination_router 消费）
    termination_reason: str  # same_action_loop / rerank_declining / budget_exhausted / ""
    termination_source: str  # loop_guard / budget / ""
    last_action_signature: str  # 最后一次规范化动作签名
    same_action_count: int  # 连续相同动作计数
    rerank_score_history: list[float]  # 跨轮 Rerank 最高分历史
    rerank_drop_count: int  # 连续下降计数
    budget_snapshot: dict  # 终止时的预算快照
    can_continue: bool  # 主图是否可继续执行（False 时主图应终止）

    # ★ P0 修正：检索观测列表（供主图合并到请求级历史）
    retrieval_observations: list[dict]


# ========== 默认配置 ==========
# 与 spec.md 中的 agent.react 配置对齐，提供合理默认值
DEFAULT_REACT_BUDGET = {
    "max_steps": 8,
    "max_tool_calls": 6,
    "max_db_queries": 3,
    "max_llm_calls": 5,
    "max_latency_ms": 30000,
    "token_budget": 12000,
}

# ★ P0 修正：LoopGuard 默认配置
DEFAULT_LOOP_GUARD_CONFIG = {
    "enabled": True,
    "max_iterations": 8,
    "same_action_limit": 3,
    "rerank_drop_limit": 2,
    "min_rerank_drop": 0.05,
    "min_evidence_gain": 1,
    "absolute_quality_floor": 0.30,
    "fallback_message": "当前信息不足以回答，建议人工介入",
}


class LoopGuardState(TypedDict, total=False):
    """请求级 LoopGuard 状态（P0 修正版）。

    由主图初始化，ReAct 子图通过上下文引用使用。
    在主图和子图间共享动作签名、停滞检测和 Rerank 趋势状态。
    """

    iteration_count: int
    max_iterations: int
    action_signature: str  # 最近一次规范化动作签名
    same_action_count: int  # 连续相同动作计数
    rerank_score_history: list[float]  # 跨轮 Rerank 最高分历史
    rerank_drop_count: int  # 连续下降计数
    retrieval_observations: list[dict]  # 检索观测列表
    termination_reason: str
    termination_source: str
    fallback_message: str


def get_default_budget(deadline_offset_ms: int = DEFAULT_REACT_BUDGET["max_latency_ms"]) -> ReactBudget:
    """构造默认 Budget 实例。

    Args:
        deadline_offset_ms: 距离当前时间的超时毫秒数

    Returns:
        ReactBudget: 初始预算状态（所有 count=0）
    """
    import time

    return ReactBudget(
        step_count=0,
        max_steps=DEFAULT_REACT_BUDGET["max_steps"],
        tool_call_count=0,
        max_tool_calls=DEFAULT_REACT_BUDGET["max_tool_calls"],
        db_query_count=0,
        max_db_queries=DEFAULT_REACT_BUDGET["max_db_queries"],
        llm_call_count=0,
        max_llm_calls=DEFAULT_REACT_BUDGET["max_llm_calls"],
        deadline_ts=time.time() + deadline_offset_ms / 1000.0,
        token_used=0,
        token_budget=DEFAULT_REACT_BUDGET["token_budget"],
    )
