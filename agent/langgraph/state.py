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
"""LangGraph Agent 状态定义模块。

定义 LangGraph 工作流中所有节点共享的统一状态结构 AgentState，
确保状态传递的类型安全和可追溯性。
"""

from typing import Annotated, Any, Literal, TypedDict, Optional

from agent.langgraph.routers.models import ExecutionPlan, RouteDecision


class ToolResult(TypedDict, total=False):
    """单个工具执行结果。

    用于计划执行器并行调度时，存储每个步骤的执行结果。

    Attributes:
        step_id: 对应 PlanStep.step_id
        tool: rag / database / web
        success: 是否成功
        error: 失败原因（success=False 时）
        rag_docs: RAG 检索结果
        rag_quality_score: RAG 质量评分
        rag_has_relevant: RAG 是否有相关文档
        rag_relevant_count: RAG 相关文档数
        rag_top_score: RAG 最高分
        db_result: 数据库查询结果
        db_quality_score: 数据库质量评分
        web_docs: Web 搜索结果
        latency_ms: 执行耗时（毫秒）
        db_id: 使用的数据库实例（便于日志追溯）
        kb_ids: 使用的知识库列表
    """

    step_id: str
    tool: str
    success: bool
    error: str
    rag_docs: list[dict]
    rag_quality_score: float
    rag_has_relevant: bool
    rag_relevant_count: int
    rag_top_score: float
    db_result: dict
    db_quality_score: float
    web_docs: list[dict]
    latency_ms: int
    db_id: str
    kb_ids: list[str]


def merge_timings(left: dict | None, right: dict | None) -> dict:
    """LangGraph reducer for ``node_timings``.

    By default LangGraph replaces dict fields on partial state updates,
    which would discard previous nodes' timings. This reducer merges
    the incoming timing dict into the existing one so that all node
    timings accumulate across the graph execution.
    """
    merged: dict[str, float] = {}
    if left:
        merged.update(left)
    if right:
        merged.update(right)
    return merged


class AgentState(TypedDict, total=False):
    """LangGraph 全局状态定义。

    状态字段按功能分组：
    - 用户输入：user_question、query_lang
    - 路由决策：route_target
    - RAG Tool 输出：rag_docs、rag_quality_score、rag_has_relevant、rag_relevant_count、rag_top_score
    - Database Tool 输出：db_result、db_quality_score
    - Web Tool 输出：web_docs
    - 融合上下文：merged_context
    - LLM 生成：generated_answer
    - 幻觉检测：hallucination_score、hallucination_action
    - 重试控制：retry_count、max_retries
    - 可观测性：trace_id、node_timings

    使用 total=False 允许部分字段在状态初始化时缺失，
    各节点按需读写字段。
    """

    # 用户输入
    user_question: str
    query_lang: Literal["zh_CN", "zh_TW", "en"]
    query_simplified: str  # 繁转简后的查询（用于检索/DB 表匹配）
    tenant_id: str
    llm_id: str
    kb_ids: list[str]
    db_id: str
    mcp_server_name: str

    # 路由决策
    route_target: Literal["rag", "database", "hybrid", "web", "chitchat"]
    route_decision: Optional[RouteDecision]  # 三层路由架构的完整决策信息

    # RAG Tool 输出
    rag_docs: list[dict]  # [{content, score, source, chunk_id}]
    rag_quality_score: float  # 0.0 ~ 1.0
    rag_has_relevant: bool
    rag_relevant_count: int
    rag_top_score: float

    # Database Tool 输出
    db_result: dict  # {sql, rows, row_count, source}
    db_quality_score: float

    # Web Tool 输出
    web_docs: list[dict]  # [{content, url, title}]

    # 融合后的上下文
    merged_context: str

    # LLM 生成
    generated_answer: str

    # 质量决策
    quality_decision: str  # 质量检查节点的决策结果

    # 幻觉检测
    hallucination_score: float  # 0.0 ~ 1.0
    hallucination_action: Literal["pass", "filter", "regenerate", "exhausted", "reject"]
    regenerate_count: int  # 防止 LLM 重新生成无限循环

    # 提示词组装
    final_prompt: str  # 最终组装后的提示词

    # 最终答案
    final_answer: str  # 最终生成的答案

    # 重试控制
    retry_count: int
    max_retries: int

    # 可观测性
    trace_id: str
    node_timings: Annotated[dict, merge_timings]  # {node_name: latency_ms}

    # 对话历史（供 prompt_assembly 注入 LLM 上下文）
    conversation_history: list[dict]  # [{role: "user"|"assistant", content: str}]

    # Agent 配置（工具参数，从 DB agent 配置传入）
    agent_config: dict  # {tools_config, routing_config, degradation_config, model_config}

    # 图执行起始时间戳（供 observability 计算端到端耗时）
    graph_start_time: float

    # ========== 计划执行器相关（第3层 Planner 复杂任务并行执行） ==========
    execution_plan: Optional[ExecutionPlan]  # Planner 生成的执行计划
    tool_results: list[ToolResult]           # 多工具并行执行结果列表
    plan_execution_status: str               # 计划执行状态：running/completed/failed

    # ========== 工具结果反思 ==========
    # reflection_result 结构:
    #   key_findings: list[str]     — 关键发现
    #   info_sufficient: bool       — 信息是否充分
    #   gaps: list[str]             — 信息缺口
    #   next_action: str            — 建议下一步
    #   reflection_text: str        — 完整反思文本
    reflection_result: Optional[dict]

    # ========== 主动澄清交互 ==========
    # clarification_request 结构:
    #   question: str               — 澄清问题
    #   options: list[str]          — 可选项
    #   original_question: str      — 原始问题
    clarification_request: Optional[dict]
    clarification_context: Optional[str]      # 用户对澄清的回答

    # ========== 受限 ReAct 子图（仅复杂任务） ==========
    # react_state 内部结构:
    #   step_count: int             — 已执行步数
    #   tool_call_count: int        — 工具调用次数
    #   db_query_count: int         — DB 查询次数
    #   llm_call_count: int         — LLM 调用次数
    #   action_history: list[dict]  — 每一步 action
    #   observations: list[dict]    — 每一步 observation
    #   finish_reason: str          — finish / budget_exhausted / policy_denied / error
    react_state: Optional[dict]

    # react_execution_result 结构:
    #   success: bool               — 子图是否成功完成
    #   evidence: list[dict]        — 标准化后的 Evidence 列表
    #   step_count: int             — 总步数
    #   finish_reason: str          — 终止原因
    #   summary: str                — 子图执行摘要
    react_execution_result: Optional[dict]

    # ========== Evidence 与答案充分性 ==========
    # evidence: list[dict]          — 标准化后的证据集合（来自所有工具）
    # 每个 evidence 含 source_type / title / content / source_uri / confidence 等字段
    evidence: list[dict]

    # evidence_fusion_result 结构:
    #   fused_count: int            — 融合后证据数
    #   conflicts: list[dict]       — 检测到的冲突
    #   token_budget: int           — 分配给 Prompt 的 token 预算
    evidence_fusion_result: Optional[dict]

    # answerability_result 结构:
    #   answerable: bool            — 证据是否充分
    #   coverage_score: float       — 覆盖率 0.0 ~ 1.0
    #   missing_aspects: list[str]  — 缺失的方面
    #   recommended_action: str     — generate / react_continue / ask_clarification / partial_answer
    answerability_result: Optional[dict]

    # ========== 能力开关（每会话可控） ==========
    # react_enabled: 来自 agent_config.react.enabled（默认 False）
    #   - True: 复杂任务有可能进入 react_subgraph（由 Planner 决定）
    #   - False: 永远不进入 react_subgraph
    react_enabled: bool

    # db_tool_enabled: 来自 agent_config.db_tool.enabled（默认 True，向后兼容）
    #   - True: 复杂任务有可能使用 db_tool（由路由决定）
    #   - False: 路由时跳过 db_tool，hybrid 降级为单 RAG，database 降级为 rag_tool
    db_tool_enabled: bool
