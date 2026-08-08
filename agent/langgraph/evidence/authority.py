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
"""AuthorityResolver — 证据权威性动态计算（v2.1 §4.5.2 P1-B 修复）。

设计背景：
    v1 用 source_type 硬编码权威度，并在 contradiction 后乘 score——但 contradiction 已是 0，
    继续乘无效；且 DB 不天然 0.95（SQL 可能查错表），Web 可能为官方源。
    本模块改为「来源权重 + 查询正确性 + 时效性」三维加权求和，每个维度均可独立配置。

计算公式：
    authority = source_weight * 0.4 + query_correctness * 0.3 + freshness * 0.3

维度说明：
    - source_weight:       db=0.95, rag=0.80, report=0.85, web=0.50（与 models.DEFAULT_AUTHORITY_SCORE 对齐）
    - query_correctness:   DB 查询成功=1.0 / 失败=0.0；RAG/Web/Report 默认 0.8
    - freshness:           30 天内=1.0, 90 天内=0.8, 365 天内=0.5, 更早=0.3

类比 Java 中的策略模式：
    ``AuthorityResolver`` 类似 ``@Component class AuthorityResolver``，
    通过构造器注入 ``source_weights`` / ``formula_weights`` / ``default_query_correctness``
    即可替换打分策略，无需修改 resolve 主流程（开闭原则）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


# 默认来源权重（与 evidence/models.py DEFAULT_AUTHORITY_SCORE 保持一致，避免双源不一致）
DEFAULT_SOURCE_WEIGHTS: dict[str, float] = {
    "db": 0.95,      # 数据库事实最权威
    "rag": 0.80,     # 知识库次之
    "report": 0.85,  # 报告权威性视来源
    "web": 0.50,     # Web 可信度最低
}

# 默认公式权重（source_weight / query_correctness / freshness 三项之和=1.0）
DEFAULT_FORMULA_WEIGHTS: dict[str, float] = {
    "source_weight": 0.4,
    "query_correctness": 0.3,
    "freshness": 0.3,
}

# 各来源默认查询正确性（DB 由 metadata 推断覆盖；其余来源无法判定 SQL 正确性，给中性偏积极分）
DEFAULT_QUERY_CORRECTNESS: dict[str, float] = {
    "rag": 0.8,
    "web": 0.8,
    "report": 0.8,
    "db": 1.0,
}

# 时效性分档： (天数上限, 分数)，按顺序匹配第一条 age_days <= 天数 的档位
FRESHNESS_TIERS: list[tuple[int, float]] = [
    (30, 1.0),    # 30 天内：最新
    (90, 0.8),    # 90 天内：较新
    (365, 0.5),   # 365 天内：一般
]
# 超过 365 天的兜底分数
FRESHNESS_STALE = 0.3
# 无法解析时间戳时的中性分数（既不一刀切拉低也不虚高）
FRESHNESS_UNKNOWN = 0.5


class AuthorityResolver:
    """权威性动态计算器（v2.1 §4.5.2 P1-B 修复）。

    通过构造器注入自定义权重即可替换打分策略，类比 Java 策略模式：
    新增来源类型或调整公式权重无需修改 resolve 主流程。

    Args:
        source_weights: 来源权重覆盖，如 {"web": 0.7}；未覆盖项沿用默认值
        formula_weights: 公式权重覆盖，如 {"source_weight": 0.5, "freshness": 0.2}
        default_query_correctness: 各来源默认查询正确性覆盖
    """

    def __init__(
        self,
        source_weights: Optional[dict[str, float]] = None,
        formula_weights: Optional[dict[str, float]] = None,
        default_query_correctness: Optional[dict[str, float]] = None,
    ) -> None:
        # 合并默认权重与自定义覆盖（自定义优先，未覆盖项保留默认）
        self._source_weights: dict[str, float] = {
            **DEFAULT_SOURCE_WEIGHTS,
            **(source_weights or {}),
        }
        self._formula_weights: dict[str, float] = {
            **DEFAULT_FORMULA_WEIGHTS,
            **(formula_weights or {}),
        }
        self._default_query_correctness: dict[str, float] = {
            **DEFAULT_QUERY_CORRECTNESS,
            **(default_query_correctness or {}),
        }

    def resolve(self, evidence: dict[str, Any], query: str = "") -> float:
        """计算单条 Evidence 的权威性分数。

        公式：authority = source_weight * w_src + query_correctness * w_qc + freshness * w_fr

        Args:
            evidence: 标准化 Evidence dict（含 source_type / created_at / metadata）
            query: 自然语言查询（保留用于审计/扩展，当前不参与打分——
                查询正确性由 DB 执行结果推断，而非自然语言 query 本身）

        Returns:
            float: 权威性分数 0.0 ~ 1.0
        """
        source_type = evidence.get("source_type", "web")
        # 未知来源类型给中性 0.5，避免 KeyError 中断主流程
        source_weight = self._source_weights.get(source_type, 0.5)
        query_correctness = self._compute_query_correctness(evidence, source_type)
        freshness = self._compute_freshness(evidence)

        w = self._formula_weights
        score = (
            source_weight * w.get("source_weight", 0.4)
            + query_correctness * w.get("query_correctness", 0.3)
            + freshness * w.get("freshness", 0.3)
        )
        return max(0.0, min(1.0, score))

    def resolve_batch(
        self,
        evidences: list[dict[str, Any]],
        query: str = "",
    ) -> list[float]:
        """批量计算权威性分数。

        Args:
            evidences: Evidence dict 列表
            query: 自然语言查询（透传给每条 resolve）

        Returns:
            list[float]: 与输入等长的权威性分数列表
        """
        return [self.resolve(ev, query=query) for ev in evidences]

    def _compute_query_correctness(
        self,
        evidence: dict[str, Any],
        source_type: str,
    ) -> float:
        """计算查询正确性。

        - DB：SQL 执行成功=1.0，失败=0.0（依据 metadata 推断）
        - RAG / Web / Report：默认 0.8（无法判定查询正确性，给中性偏积极分）

        DB 成功/失败判定优先级：
            1. metadata["query_success"] 显式标记
            2. metadata["error"] 或 structured_data["error"] 存在 → 失败
            3. Evidence 存在即视为查询执行成功（normalize_db_evidence 对零行也产出 Evidence）
        """
        if source_type != "db":
            return self._default_query_correctness.get(source_type, 0.8)

        metadata = evidence.get("metadata") or {}
        # 1. 显式标记优先
        if "query_success" in metadata:
            return 1.0 if metadata["query_success"] else 0.0
        # 2. 错误标记 → 失败
        if metadata.get("error"):
            return 0.0
        structured = evidence.get("structured_data") or {}
        if isinstance(structured, dict) and structured.get("error"):
            return 0.0
        # 3. Evidence 存在 → 查询执行成功
        return 1.0

    def _compute_freshness(self, evidence: dict[str, Any]) -> float:
        """计算时效性分数（基于时间衰减）。

        分档：
            - 30 天内  = 1.0
            - 90 天内  = 0.8
            - 365 天内 = 0.5
            - 更早     = 0.3

        时间戳选取优先级：metadata.as_of（DB 数据版本）> metadata.published_at（Web 发布）
        > metadata.fetched_at > created_at。无法解析时返回中性 0.5。
        """
        timestamp = self._pick_timestamp(evidence)
        if not timestamp:
            return FRESHNESS_UNKNOWN

        age_days = self._age_days(timestamp)
        if age_days is None:
            return FRESHNESS_UNKNOWN

        for threshold, score in FRESHNESS_TIERS:
            if age_days <= threshold:
                return score
        return FRESHNESS_STALE

    @staticmethod
    def _pick_timestamp(evidence: dict[str, Any]) -> str:
        """按优先级选取最能代表「数据时效」的时间戳。"""
        metadata = evidence.get("metadata") or {}
        for key in ("as_of", "published_at", "fetched_at"):
            value = metadata.get(key)
            if value:
                return str(value)
        created = evidence.get("created_at")
        return str(created) if created else ""

    @staticmethod
    def _age_days(timestamp: str) -> Optional[int]:
        """将 ISO 时间戳转为距今天数；解析失败返回 None。"""
        if not timestamp:
            return None
        ts = timestamp.strip()
        # 兼容 ISO 8601 末尾 'Z'（UTC），fromisoformat 在 3.11 前不支持 'Z'
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            return None
        # 无时区信息时按 UTC 处理（models._now_iso 输出 UTC）
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, (now - dt).days)
