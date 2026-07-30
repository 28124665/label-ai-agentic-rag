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
"""最终答案节点。

返回最终答案给用户，支持流式输出。

扩展：支持 report_generated / report_failed 类型最终答案
（按 docs/报告生成Tool设计.md §9 设计）。
"""

import json
import logging
import time
from typing import Any

from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)


def final_answer_node(state: AgentState) -> dict[str, Any]:
    """最终答案节点。

    决策顺序：
    1. 若 report_artifacts 非空 → 返回 report_generated / report_failed
    2. 否则按幻觉检测结果返回普通最终答案

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 final_answer
    """
    start_time = time.time()

    # ========== 1. 报告生成优先（按 docs/报告生成Tool设计.md §9） ==========
    report_artifacts = state.get("report_artifacts") or []
    if report_artifacts:
        report_response = _build_report_response(state)
        logger.info(
            f"[final_answer] 返回报告类型答案: type={report_response.get('type')}, "
            f"report_id={report_response.get('report_id')}"
        )
        return {
            "final_answer": json.dumps(report_response, ensure_ascii=False, default=str),
            "node_timings": {"final_answer": int((time.time() - start_time) * 1000)},
        }

    # ========== 2. 普通问答最终答案 ==========
    generated_answer = state.get("generated_answer", "")
    hallucination_action = state.get("hallucination_action", "pass")
    query_lang = state.get("query_lang", "zh_CN")

    # 根据幻觉检测动作决定最终答案
    if hallucination_action == "reject":
        # 严重幻觉，直接拒答
        final_answer = _get_rejection_message(query_lang)
        logger.info("[final_answer] 幻觉检测拒答")
    elif hallucination_action == "exhausted":
        # 重新生成次数用尽，返回保守答案（基于检索上下文）
        final_answer = _get_conservative_answer(
            state.get("merged_context", ""),
            query_lang,
        )
        logger.info("[final_answer] 重新生成次数用尽，返回保守答案")
    elif hallucination_action == "filter":
        # 过滤后的答案（当前简化为直接返回原答案）
        # TODO: 集成 HallucinationDetector 的过滤逻辑
        final_answer = generated_answer or ""
        logger.info("[final_answer] 幻觉检测过滤，返回原答案")
    else:
        # pass：直接返回生成的答案
        final_answer = generated_answer or ""
        logger.info("[final_answer] 幻觉检测通过，返回生成答案")

    logger.info(
        f"[final_answer] 最终答案: "
        f"length={len(final_answer)}, action={hallucination_action}"
    )

    return {
        "final_answer": final_answer,
        "node_timings": {"final_answer": int((time.time() - start_time) * 1000)},
    }


def _build_report_response(state: AgentState) -> dict[str, Any]:
    """构建 report_generated / report_failed 类型最终答案。

    按 docs/报告生成Tool设计.md §9 设计：
    - 成功：type=report_generated, download_url, summary, sections/charts/tables, verification
    - 失败：type=report_failed, title, summary, issues, action=regenerate
    - 下一期（按 docs/报告可信治理 §6）：新增 publish_status / needs_human_review / claims / data_sources

    Args:
        state: 当前 AgentState

    Returns:
        dict: 报告响应对象
    """
    report_artifacts = state.get("report_artifacts") or []
    artifact = report_artifacts[0] if report_artifacts else {}

    report_error = state.get("report_error", "") or ""
    report_error_code = state.get("report_error_code", "") or ""
    verification = artifact.get("verification_result", {}) or {}
    issues = verification.get("issues", []) or []

    # 验证失败 → report_failed
    if report_error_code == "REPORT_VERIFICATION_FAILED" or (
        verification and not verification.get("passed", True)
    ):
        issue_messages = [
            i.get("message", "") for i in issues if isinstance(i, dict)
        ]
        return {
            "type": "report_failed",
            "title": artifact.get("title", "分析报告"),
            "summary": "报告生成过程中发现部分数字无法与数据来源匹配，当前未导出正式报告。",
            "issues": issue_messages,
            "action": "regenerate",
            "report_id": artifact.get("report_id", ""),
            # 下一期：治理字段（即便失败也保留，便于用户查看 Claim / 来源）
            "publish_status": state.get("report_publish_status", "draft"),
            "needs_human_review": state.get("report_needs_human_review", False),
        }

    # 成功 → report_generated
    return {
        "type": "report_generated",
        "title": artifact.get("title", "分析报告"),
        "summary": artifact.get("summary", state.get("report_summary", "")),
        "download_url": state.get("report_download_url", ""),
        "file_uri": state.get("report_file_uri", ""),
        "format": artifact.get("format", "markdown"),
        "sections": len(artifact.get("sections", []) or []),
        "charts": len(artifact.get("charts", []) or []),
        "tables": len(artifact.get("tables", []) or []),
        "report_id": artifact.get("report_id", ""),
        "partial": bool(artifact.get("partial") or state.get("report_partial", False)),
        "verification": {
            "passed": verification.get("passed", False),
            "score": verification.get("score", 0.0),
        },
        "quality_score": state.get("report_quality_score", 0.0),
        # 下一期：可信治理与人机协同（按 docs/报告可信治理 §4-6）
        "publish_status": state.get(
            "report_publish_status",
            artifact.get("publish_status", "approved"),
        ),
        "needs_human_review": state.get(
            "report_needs_human_review",
            artifact.get("publish_status", "approved") == "pending_review",
        ),
        "claim_count": len(state.get("report_claims") or []),
        "data_source_count": len(state.get("report_data_sources") or []),
        "human_review": state.get("report_human_review", {}) or {},
    }


def _get_rejection_message(query_lang: str) -> str:
    """获取拒答消息。

    Args:
        query_lang: 查询语言

    Returns:
        str: 拒答消息
    """
    messages = {
        "zh_CN": "抱歉，根据现有参考资料无法回答您的问题。建议您提供更多相关信息。",
        "zh_TW": "抱歉，根據現有參考資料無法回答您的問題。建議您提供更多相關資訊。",
        "en": "I'm sorry, I cannot answer your question based on the available references. Please provide more relevant information.",
    }
    return messages.get(query_lang, messages["zh_CN"])


def _get_conservative_answer(merged_context: str, query_lang: str) -> str:
    """获取保守答案（基于检索上下文）。

    当幻觉检测动作为 exhausted（重新生成次数用尽）时，
    返回一个保守的答案，仅包含检索到的信息。

    Args:
        merged_context: 合并后的上下文
        query_lang: 查询语言

    Returns:
        str: 保守答案
    """
    if not merged_context:
        return _get_rejection_message(query_lang)

    # 保守策略：直接返回检索上下文摘要
    prefix = {
        "zh_CN": "根据现有参考资料，以下是相关信息：\n\n",
        "zh_TW": "根據現有參考資料，以下是相關資訊：\n\n",
        "en": "Based on the available references, here is the relevant information:\n\n",
    }
    return prefix.get(query_lang, prefix["zh_CN"]) + merged_context[:2000]
