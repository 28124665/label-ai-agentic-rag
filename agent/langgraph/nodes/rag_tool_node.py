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
"""RAG 工具节点。

调用 RAGTool 进行知识库检索。

方案 A（详见设计文档 §4.5.6）：
- 透传 state["retry_count"] 给 RAGTool（用于查询重写策略轮转）
- 累计 retry_token_used 并写回 state（quality_check 检查预算）
- 透传 rag_score_source 到 state（quality_check 按来源选阈值）
"""

import logging
import time
from typing import Any

from agent.langgraph.gateways.errors import RetrievalDataError
from agent.langgraph.state import AgentState
from agent.langgraph.tools.rag_tool import get_rag_tool

logger = logging.getLogger(__name__)


async def rag_tool_node(state: AgentState) -> dict[str, Any]:
    """RAG 工具节点。

    调用 RAGTool.invoke()，将结果写入 AgentState。

    方案 A（详见设计文档 §4.5.6）：
    - 透传 state["retry_count"] 给 RAGTool（用于查询重写策略轮转起点）
    - 累计 retry_token_used 并写回 state（quality_check 检查 Token 预算）
    - 透传 rag_score_source 到 state（quality_check 按来源选阈值）

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段
    """
    start_time = time.time()

    user_question = state.get("user_question", "")
    query_simplified = state.get("query_simplified", "")

    if not user_question:
        logger.warning("[rag_tool] 用户问题为空")
        return _empty_rag_state(start_time)

    rag_tool = get_rag_tool()

    # 从 agent_config 读取 RAG 工具参数，保留默认值兜底
    # §5.9 参数补齐：similarity_threshold / keywords_similarity_weight / rerank_id
    # 现状遗漏属缺陷，补齐后远程/本地两模式行为一致
    agent_config = state.get("agent_config", {}) or {}
    rag_config = agent_config.get("rag_config", {}) or {}

    # ★ 方案 A：透传主流程 retry_count 给 RAGTool（用于查询重写策略轮转）
    # 详见设计文档 §4.4：策略轮转公式 strategies[(retry_count + attempt) % len]
    retry_count = state.get("retry_count", 0)

    # ★ 多KB选择策略：优先使用 route_decision.kb_ids（LLM Router / Planner 推荐），
    # 否则回退到 state.kb_ids（Agent 配置的默认知识库）
    route_decision = state.get("route_decision", {}) or {}
    recommended_kb_ids = route_decision.get("kb_ids", []) if isinstance(route_decision, dict) else getattr(route_decision, "kb_ids", [])
    kb_ids = recommended_kb_ids if recommended_kb_ids else state.get("kb_ids", [])

    input_data = {
        "query": user_question,
        "query_simplified": query_simplified or user_question,
        "top_k": rag_config.get("top_k", 5),
        "enable_rewrite": rag_config.get("enable_rewrite", True),
        "enable_rerank": rag_config.get("enable_rerank", True),
        "kb_ids": kb_ids,
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "cross_languages": rag_config.get("cross_languages", []),
        # ★ §5.9 补齐：从 rag_config 读取此前遗漏的参数
        "similarity_threshold": rag_config.get("similarity_threshold", 0.2),
        "keywords_similarity_weight": rag_config.get("keywords_similarity_weight", 0.5),
        "rerank_id": rag_config.get("rerank_id", ""),
        "retry_count": retry_count,  # ★ 方案 A：策略轮转起点
    }

    try:
        result = await rag_tool.invoke(input_data)

        # ★ 方案 A：估算本次检索消耗的 Token（粗略估算：docs 总字符数 / 4）
        # 累计写回 state，供 quality_check 检查 Token 预算（详见设计文档 §4.5.6）
        docs = result.get("docs", [])
        estimated_tokens = sum(len(d.get("content", "")) for d in docs) // 4
        retry_token_used = state.get("retry_token_used", 0) + estimated_tokens

        logger.info(
            f"[rag_tool] 检索完成: docs={len(docs)}, "
            f"score={result.get('quality_score', 0.0):.2f} "
            f"({result.get('score_source', 'base')}), "
            f"strategy={result.get('rewrite_strategy', 'none')}, "
            f"retry_count={retry_count}, token_used={retry_token_used}"
        )

        # §5.8 透传降级状态到 state（retrieval_error_code / retrieval_mode_used）
        return {
            "rag_docs": docs,
            "rag_quality_score": result.get("quality_score", 0.0),
            "rag_has_relevant": result.get("has_relevant", False),
            "rag_relevant_count": result.get("relevant_count", 0),
            "rag_top_score": result.get("top_score", 0.0),
            "rag_score_source": result.get("score_source", "base"),  # ★ 方案 A：分数来源
            "query_lang": result.get("detected_lang", state.get("query_lang", "zh_CN")),
            "query_simplified": result.get("query_simplified", query_simplified),
            "retrieval_error_code": result.get("retrieval_error_code", ""),
            "retrieval_mode_used": result.get("retrieval_mode_used", ""),
            "retry_token_used": retry_token_used,  # ★ 方案 A：累计 Token
            "node_timings": {"rag_tool": int((time.time() - start_time) * 1000)},
        }

    except RetrievalDataError:
        # §5.8：数据/权限类业务错误透传上抛，不静默转空结果
        # （区别于「KB 真为空」与「服务异常降级」，由上游显式处理）
        raise
    except Exception as e:
        logger.error(f"[rag_tool] 检索失败: {e}")
        return _empty_rag_state(start_time)


def _empty_rag_state(start_time: float) -> dict[str, Any]:
    """空结果状态（方案 A：包含 score_source 默认值）。

    用于 user_question 为空或检索异常时返回统一结构的空状态，
    保证 quality_check 读到的字段完整（rag_score_source 默认 "base"）。

    Args:
        start_time: 节点开始时间戳

    Returns:
        dict: 空的 RAG 状态字段，包含 rag_score_source="base" 默认值
    """
    return {
        "rag_docs": [],
        "rag_quality_score": 0.0,
        "rag_has_relevant": False,
        "rag_relevant_count": 0,
        "rag_top_score": 0.0,
        "rag_score_source": "base",  # ★ 方案 A：空结果默认 base 分数
        "retrieval_error_code": "",
        "retrieval_mode_used": "",
        "node_timings": {"rag_tool": int((time.time() - start_time) * 1000)},
    }
