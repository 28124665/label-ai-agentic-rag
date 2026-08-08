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

from typing import Annotated, Literal, TypedDict, Optional

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
    - RAG Tool 输出：rag_docs、rag_quality_score、rag_has_relevant、rag_relevant_count、rag_top_score、rag_score_source
    - Database Tool 输出：db_result、db_quality_score
    - Web Tool 输出：web_docs
    - 融合上下文：merged_context
    - LLM 生成：generated_answer
    - 幻觉检测：hallucination_score、hallucination_action
    - 重试控制：retry_count、max_retries、retry_token_used、retry_token_budget
    - 可观测性：trace_id、node_timings

    使用 total=False 允许部分字段在状态初始化时缺失，
    各节点按需读写字段。

    方案 A（RAGTool 单次执行 + 主流程统一重试）新增字段：
    - rag_score_source：分数来源标注，供 quality_check 按来源选择阈值
    - retry_token_used：已用 Token 预算，由 rag_tool_node 累计、quality_check 检查
    - retry_token_budget：Token 预算上限，默认 2000，可被 agent_config 覆盖
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

    # Skill 路由（v1.1 §11.1，版本化引用 + 三元审计链）
    # resolved_skill_ref: ResolvedSkillRef 序列化 dict，一次请求固定不可变
    # skill_route_trace: SkillRoutingTrace 序列化 dict，含 catalog_revision ↔ evidence_snapshot_id 审计链
    # catalog_revision: 当前请求绑定的 Catalog 版本号，retry 期间冻结（§11.5.2 约束3）
    resolved_skill_ref: dict
    skill_route_trace: dict
    catalog_revision: str

    # P0 主链路接线字段（§18.1，过渡期兼容 §11.6.2 约束2）
    # skill_set: ResolvedSkillSet 序列化 dict（旧模型，供 plan_with_skills / Policy Guard 回退）
    #   - 写入：intent_router_node（Skill 命中且非 fallback 时）
    #   - 读取：planner.plan_with_skills / SkillPolicyAdapter / SkillEvidenceAdapter
    #   - 迁移完成后移除，由 resolved_skill_ref 替代
    skill_set: dict
    # skill_resolution: SkillResolveResult 序列化 dict（含 resolved/reason/fallback_used/warnings）
    #   - 写入：intent_router_node（所有路径均写入，便于审计"为何未命中 Skill"）
    #   - 读取：observability 节点上报路由追踪
    skill_resolution: dict
    # skill_evidence_requirements: SkillEvidenceRequirement 序列化 list
    #   - 写入：intent_router_node（Skill 命中时由 SkillEvidenceAdapter.collect_requirements 生成）
    #   - 读取：evidence_fusion_node（按 source_quota 截断）/ answerability 检查
    skill_evidence_requirements: list[dict]

    # RAG Tool 输出
    rag_docs: list[dict]  # [{content, score, source, chunk_id}]
    rag_quality_score: float  # 0.0 ~ 1.0
    rag_has_relevant: bool
    rag_relevant_count: int
    rag_top_score: float
    # 分数来源标注（方案 A 新增）：
    #   - 取值 "base"（基础评估，Rerank 分数）或 "grader_merged"（Grader 增强后融合分数）
    #   - 默认 "base"（字段缺失时 quality_check 使用 base 阈值 0.7）
    #   - 写入：rag_tool_node（透传自 RAGTool._evaluate_quality 的 score_source）
    #   - 读取：quality_check（按来源选择阈值 base=0.7 / grader_merged=0.65）
    rag_score_source: str

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
    # 已用 Token 预算（方案 A 新增）：
    #   - 默认 0（首次检索前无消耗）
    #   - 写入：rag_tool_node（每次检索后累加估算 Token，公式 docs 总字符数 / 4）
    #   - 读取：quality_check（达到 budget 上限时降级 fallback_web，不再重试）
    retry_token_used: int
    # Token 预算上限（方案 A 新增）：
    #   - 默认 2000（DEFAULT_RETRY_TOKEN_BUDGET，可被 agent_config.retry.max_retry_tokens 覆盖）
    #   - 写入：图初始化时由配置注入（rag_tool_node 不写，quality_check 不写）
    #   - 读取：quality_check（与 retry_token_used 比较，判断是否耗尽预算）
    retry_token_budget: int

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

    # ========== Claim 级可追溯幻觉检测（v2.1 §4.0 + §4.1 + §4.2） ==========
    # Evidence 融合后的统一证据列表（替代 rag_docs/db_result/web_docs 分散读取）
    # 每项为 Evidence TypedDict（见 evidence/models.py）
    evidence: list[dict]

    # 不可变 Evidence 快照 ID（Prompt/verifier/references 使用同一 ID，硬约束 #4）
    evidence_snapshot_id: str

    # Evidence 快照元数据（含 tool_run_id/attempt_id/created_at/source_type_counts）
    evidence_snapshot: Optional[dict]

    # 工具运行 ID（用于重试时证据重建，每次工具执行递增）
    tool_run_id: str

    # 重试轮次（首次=1，quality_check 重试递增）
    attempt_id: int

    # 运行模式（chitchat/factual/report，与 EnforcementMode 正交）
    run_mode: str

    # 强制级别（disabled/shadow/enforced，v2.1 §4.0.1 P0-3 修复）
    enforcement_mode: str

    # 结构化答案 AST（见 evidence/answer_ast.py，含 section/paragraph/claim/table 节点）
    answer_ast: Optional[dict]

    # Claim 列表（见 evidence/claim.py，含 raw_declared_indices/verified_support_ids 等）
    claims: list[dict]

    # Claim 裁决列表（含 claim_id/final_status/verified_support_ids/contradicting_ids）
    claim_verdicts: list[dict]

    # Policy Engine 决策结果（pass/filter/regenerate/reject）
    policy_action: str

    # 引用目录（含 index/evidence_id/source_type/source_uri/accessible）
    citation_catalog: list[dict]

    # 引用指标（含 citation_validity/citation_correctness/citation_completeness/faithfulness）
    citation_metrics: Optional[dict]

    # 验证器状态（ok/timeout/error/mixed/unknown，v2.1 P0-5 修复）
    verifier_status: str

    # 拒答原因（ENFORCED 模式下 reject 时填充）
    reject_reason: str
