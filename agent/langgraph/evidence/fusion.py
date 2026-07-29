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
"""Evidence 融合（Fusion）。

将多个 source_type 的 Evidence 合并为一个有序、去重、可信度加权的列表。

融合策略（docs §7.4）：
1. 去重：基于 evidence_id 去重
2. 排序：按 (authority_score * 0.4 + relevance_score * 0.4 + freshness_score * 0.2) 加权
3. 冲突检测：DB 与 Web 在同一 key 上结论不一致时记录冲突
4. token 预算分配：按总字符数等比分配
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from agent.langgraph.evidence.models import Evidence

logger = logging.getLogger(__name__)


def _evidence_weight(ev: Evidence) -> float:
    """计算 Evidence 综合权重。"""
    authority = float(ev.get("authority_score", 0.5) or 0.5)
    relevance = float(ev.get("relevance_score", 0.5) or 0.5)
    freshness = float(ev.get("freshness_score", 0.5) or 0.5)
    return authority * 0.4 + relevance * 0.4 + freshness * 0.2


def _detect_conflicts(evidences: list[Evidence]) -> list[dict]:
    """检测 Evidence 之间的冲突。

    简化实现：基于 source_type + 简单关键词重叠判断。
    生产环境建议用更精细的语义冲突检测。

    Returns:
        list[dict]: [{evidence_ids, type, description}, ...]
    """
    conflicts: list[dict] = []
    # 仅检查 db vs web 冲突
    db_evs = [e for e in evidences if e.get("source_type") == "db"]
    web_evs = [e for e in evidences if e.get("source_type") == "web"]
    if db_evs and web_evs:
        # 简化：如果 DB 和 Web 都存在但内容关键词不重叠，记录潜在冲突
        for db_ev in db_evs:
            for web_ev in web_evs:
                if db_ev.get("content") and web_ev.get("content"):
                    # 简单判定：内容首 50 字符重叠 < 30% 视为潜在冲突
                    db_head = db_ev["content"][:50]
                    web_head = web_ev["content"][:50]
                    overlap = sum(1 for c in db_head if c in web_head) / max(len(db_head), 1)
                    if overlap < 0.3:
                        conflicts.append({
                            "type": "db_web_low_overlap",
                            "evidence_ids": [db_ev.get("evidence_id", ""), web_ev.get("evidence_id", "")],
                            "description": "DB 事实与 Web 结果相关性较低，需人工核查",
                        })
    return conflicts


def fuse_evidences(
    evidences: list[Evidence],
    max_count: int = 20,
    token_budget: int = 4000,
) -> dict:
    """融合多个 Evidence 列表。

    Args:
        evidences: 原始 Evidence 列表
        max_count: 融合后最多保留的 Evidence 数
        token_budget: 分配给 Prompt 的总 token 预算

    Returns:
        dict: {
            "fused": list[Evidence],          # 融合后有序的 Evidence
            "fused_count": int,
            "conflicts": list[dict],
            "token_budget": int,              # 实际分配的 token 预算
            "dropped_count": int,             # 被丢弃的 Evidence 数
        }
    """
    if not evidences:
        return {
            "fused": [],
            "fused_count": 0,
            "conflicts": [],
            "token_budget": token_budget,
            "dropped_count": 0,
        }

    # 1. 去重
    seen_ids: set[str] = set()
    deduped: list[Evidence] = []
    for ev in evidences:
        eid = ev.get("evidence_id", "")
        if not eid or eid in seen_ids:
            continue
        seen_ids.add(eid)
        deduped.append(ev)

    # 2. 排序（按权重降序）
    deduped.sort(key=_evidence_weight, reverse=True)

    # 3. 冲突检测
    conflicts = _detect_conflicts(deduped)

    # 4. 截断到 max_count
    fused = deduped[:max_count]
    dropped_count = len(deduped) - len(fused)

    return {
        "fused": fused,
        "fused_count": len(fused),
        "conflicts": conflicts,
        "token_budget": token_budget,
        "dropped_count": dropped_count,
    }


def format_evidences_for_prompt(evidences: list[Evidence], max_chars: int = 6000) -> str:
    """将 Evidence 列表格式化为 Prompt 友好的 Markdown 文本。

    Args:
        evidences: 融合后的 Evidence 列表
        max_chars: 总字符上限

    Returns:
        str: Markdown 格式的证据文本
    """
    if not evidences:
        return "（无证据）"

    lines: list[str] = []
    used = 0
    for i, ev in enumerate(evidences, 1):
        title = ev.get("title", "证据")
        source_type = ev.get("source_type", "")
        content = ev.get("content", "")
        source_uri = ev.get("source_uri", "")
        block = f"### 证据 {i}（{source_type}）\n**标题**: {title}\n**来源**: {source_uri}\n\n{content}\n"
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)

    return "\n".join(lines)
