#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Answerability Check — 答案充分性判断。

在生成最终答案前判断证据是否足够回答用户问题。
输出结构化判断结果，供 LangGraph 主干决策：
- generate: 证据充分，进入 prompt_assembly
- react_continue: 证据不足，回到 ReAct 子图补充
- ask_clarification: 问题本身需要澄清
- partial_answer: 部分证据充分，输出部分答案
- reject: 证据完全不足，拒绝回答

参考 docs §9.1
"""
from __future__ import annotations

import logging
from typing import Optional

from agent.langgraph.evidence.models import Evidence

logger = logging.getLogger(__name__)

# 答案充分性的最低 evidence 数阈值
MIN_EVIDENCE_COUNT = 1
# 充分答案的 coverage_score 阈值
COVERAGE_THRESHOLD = 0.6
# 高置信度阈值（达到则可保守回答）
HIGH_CONFIDENCE_THRESHOLD = 0.85


def check_answerability(
    evidences: list[Evidence],
    user_question: str = "",
) -> dict:
    """判断证据是否充分回答用户问题。

    Args:
        evidences: 融合后的 Evidence 列表
        user_question: 用户问题（用于上下文判断）

    Returns:
        dict: {
            "answerable": bool,
            "coverage_score": float,
            "missing_aspects": list[str],
            "conflicts": list[dict],
            "needs_more_evidence": bool,
            "recommended_action": str,  # generate / react_continue / ask_clarification / partial_answer / reject
            "reason": str,
        }
    """
    if not evidences:
        return {
            "answerable": False,
            "coverage_score": 0.0,
            "missing_aspects": ["无任何证据"],
            "conflicts": [],
            "needs_more_evidence": True,
            "recommended_action": "ask_clarification",
            "reason": "没有任何证据，需要澄清问题或先做基础检索",
        }

    # 1. 计算 coverage_score（综合权威性 + 相关性）
    if evidences:
        avg_score = sum(
            float(e.get("relevance_score", 0.0)) * 0.6
            + float(e.get("authority_score", 0.0)) * 0.4
            for e in evidences
        ) / len(evidences)
    else:
        avg_score = 0.0

    coverage_score = round(avg_score, 3)

    # 2. 检测冲突
    conflicts: list[dict] = []
    db_evs = [e for e in evidences if e.get("source_type") == "db"]
    web_evs = [e for e in evidences if e.get("source_type") == "web"]
    if db_evs and web_evs:
        # 简化：db 与 web 同时存在且无 RAG 佐证时记录为潜在冲突
        rag_evs = [e for e in evidences if e.get("source_type") == "rag"]
        if not rag_evs:
            conflicts.append({
                "type": "db_web_no_rag_support",
                "description": "DB 与 Web 结论不一致，缺少知识库规范佐证",
            })

    # 3. 决策
    if coverage_score >= COVERAGE_THRESHOLD and not conflicts:
        return {
            "answerable": True,
            "coverage_score": coverage_score,
            "missing_aspects": [],
            "conflicts": conflicts,
            "needs_more_evidence": False,
            "recommended_action": "generate",
            "reason": f"证据充分（coverage={coverage_score}）",
        }

    if coverage_score >= HIGH_CONFIDENCE_THRESHOLD and conflicts:
        return {
            "answerable": True,
            "coverage_score": coverage_score,
            "missing_aspects": [],
            "conflicts": conflicts,
            "needs_more_evidence": False,
            "recommended_action": "generate",
            "reason": f"高置信度（coverage={coverage_score}），忽略冲突",
        }

    if len(evidences) >= MIN_EVIDENCE_COUNT and coverage_score >= 0.3:
        return {
            "answerable": True,
            "coverage_score": coverage_score,
            "missing_aspects": ["部分信息可能不充分"],
            "conflicts": conflicts,
            "needs_more_evidence": False,
            "recommended_action": "partial_answer",
            "reason": f"部分证据充分（coverage={coverage_score}），输出部分答案",
        }

    if len(evidences) < MIN_EVIDENCE_COUNT:
        return {
            "answerable": False,
            "coverage_score": coverage_score,
            "missing_aspects": ["证据数量不足"],
            "conflicts": conflicts,
            "needs_more_evidence": True,
            "recommended_action": "ask_clarification",
            "reason": "证据数量不足，建议澄清",
        }

    return {
        "answerable": False,
        "coverage_score": coverage_score,
        "missing_aspects": ["证据相关性较低"],
        "conflicts": conflicts,
        "needs_more_evidence": True,
        "recommended_action": "react_continue",
        "reason": f"证据相关性不足（coverage={coverage_score}），回到 ReAct 补充",
    }
