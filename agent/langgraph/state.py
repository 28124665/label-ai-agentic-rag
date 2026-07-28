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

from typing import Literal, TypedDict


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
    hallucination_action: Literal["pass", "filter", "regenerate", "reject"]
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
    node_timings: dict  # {node_name: latency_ms}
