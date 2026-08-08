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
"""Evidence Snapshot 不可变快照（v2.1 §4.1.1）。

设计目标：
    Prompt 组装、verifier 验证、最终 references 必须引用同一个不可变
    ``evidence_snapshot_id``，确保整条链路证据一致性。

核心约束（评审硬约束 #4）：
    - snapshot 创建后 evidences 列表不可修改（frozen）
    - snapshot_id 基于 evidences 内容哈希，同内容必同 ID
    - 重试时通过 tool_run_id 重建新 snapshot，不复用旧 snapshot

类比 Java 中的不可变集合 + 哈希签名：
    类似 ``Collections.unmodifiableList`` + ``SHA-256`` 签名，
    一旦构建完成外部只能读不能写。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent.langgraph.evidence.models import Evidence

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """当前时间 ISO 字符串。"""
    return datetime.utcnow().isoformat() + "Z"


def _make_snapshot_id(evidences: list[Evidence]) -> str:
    """基于 evidences 内容生成稳定 snapshot_id。

    对每个 evidence_id 排序后拼接哈希，确保同内容必同 ID。
    类比 Java 中的 ``hashCode``：内容相同则哈希相同。

    Args:
        evidences: Evidence 列表

    Returns:
        str: ``es_`` 前缀的 16 位哈希 ID
    """
    if not evidences:
        return "es_empty"
    # 提取 evidence_id 列表并排序，确保顺序无关
    ev_ids = sorted(ev.get("evidence_id", "") for ev in evidences)
    raw = "|".join(ev_ids)
    return "es_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class EvidenceSnapshot:
    """不可变 Evidence 快照（v2.1 §4.1.1）。

    Attributes:
        snapshot_id: 不可变快照 ID（基于内容哈希）
        evidences: Evidence 列表（frozen=True，创建后不可修改）
        created_at: 创建时间 ISO 字符串
        tool_run_id: 触发本轮快照的工具运行 ID（用于重试重建）
        attempt_id: 重试轮次标识（首次=1，重试=2/3...）
        schema_version: 强类型 schema 版本号（任务二建议 1）

    不可变性保障：
        - dataclass(frozen=True) 阻止字段重新赋值
        - evidences 使用 tuple 而非 list，阻止 append/extend
        - 外部获取 evidences 时返回 list 副本，阻止内部修改

    类比 Java：
        ``final class EvidenceSnapshot { private final List<Evidence> evidences; }``
        + 构造器中 ``Collections.unmodifiableList`` 包装
    """

    snapshot_id: str
    evidences: tuple[Evidence, ...]  # ★ tuple 而非 list，确保不可变
    created_at: str = field(default_factory=_now_iso)
    tool_run_id: str = ""
    attempt_id: int = 1
    schema_version: str = "1.0"

    @classmethod
    def build(
        cls,
        evidences: list[Evidence],
        tool_run_id: str = "",
        attempt_id: int = 1,
    ) -> "EvidenceSnapshot":
        """构建不可变快照。

        Args:
            evidences: Evidence 列表（内部会转为 tuple）
            tool_run_id: 触发本轮快照的工具运行 ID
            attempt_id: 重试轮次（首次=1）

        Returns:
            EvidenceSnapshot: 不可变快照实例
        """
        snapshot_id = _make_snapshot_id(evidences)
        return cls(
            snapshot_id=snapshot_id,
            evidences=tuple(evidences),  # ★ list → tuple 确保不可变
            tool_run_id=tool_run_id,
            attempt_id=attempt_id,
        )

    def get_evidences(self) -> list[Evidence]:
        """获取 Evidence 列表副本（外部安全访问）。

        返回 list 副本而非内部 tuple，避免外部代码依赖 tuple 类型。
        类比 Java 中 ``new ArrayList<>(this.evidences)`` 防御性拷贝。
        """
        return list(self.evidences)

    def get_evidence_by_id(self, evidence_id: str) -> Evidence | None:
        """按 ID 查找 Evidence。

        Args:
            evidence_id: Evidence ID

        Returns:
            Evidence 或 None（不存在时）
        """
        for ev in self.evidences:
            if ev.get("evidence_id") == evidence_id:
                return ev
        return None

    def get_evidence_by_index(self, index: int) -> Evidence | None:
        """按编号查找 Evidence（1-based）。

        用于 CitationBinder 解析 [1][2] 等引用编号。

        Args:
            index: 1-based 编号

        Returns:
            Evidence 或 None（越界时）
        """
        if index < 1 or index > len(self.evidences):
            return None
        return self.evidences[index - 1]

    def count_by_source_type(self, source_type: str) -> int:
        """统计指定 source_type 的 Evidence 数量。

        用于来源配额检查（§4.1.5）。

        Args:
            source_type: rag / db / web / report

        Returns:
            数量
        """
        return sum(1 for ev in self.evidences if ev.get("source_type") == source_type)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于日志/审计/SSE 传输）。

        Returns:
            dict: 包含所有字段的字典
        """
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "evidence_count": len(self.evidences),
            "created_at": self.created_at,
            "tool_run_id": self.tool_run_id,
            "attempt_id": self.attempt_id,
            "source_type_counts": {
                st: self.count_by_source_type(st)
                for st in ("rag", "db", "web", "report")
            },
        }
