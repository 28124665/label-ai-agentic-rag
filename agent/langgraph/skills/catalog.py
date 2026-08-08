"""Skill Catalog Snapshot — 不可变版本化快照（P1/P2，§7.1/§7.4）。

设计文档 §7.1 Catalog Snapshot 约束：
    1. Snapshot 构建成功后不可变。
    2. reload 在后台构建新 Snapshot，完成全量校验和索引后原子替换指针。
    3. 构建失败继续使用上一 Snapshot，禁止部分更新。
    4. 请求开始时固定 revision，请求结束前不得切换。
    5. 多 Worker 环境通过版本通知或轮询保证最终一致；审计记录实际使用 revision。

P2 阶段扩展（§7.4 / §8.4）：
    - 新增 cards_by_key：(skill_id, version) → SkillCard 映射
    - 新增 bm25_index：BM25 语义索引（进程内，§7.4.1）
    - 新增 embedding_index：Embedding 向量矩阵（异步构建，§7.4.2）
    - P1 阶段这些字段为 None（未构建），P2 阶段由 loader 填充

类比 Java：
    ``SkillCatalogSnapshot`` ≈ 不可变值对象（``@Value`` / ``record``），
    通过 ``frozen=True`` dataclass 实现不可变性，类似 Java 的 ``Collections.unmodifiableMap``。
    ``SkillRegistry`` 持有 Snapshot 引用，reload 时原子替换（类比 ``AtomicReference``）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill, SkillBase


@dataclass(frozen=True)
class SkillCatalogSnapshot:
    """不可变 Skill Catalog 快照。

    构建后不可修改（frozen=True），reload 时创建新 Snapshot 原子替换旧引用。
    所有 Mapping 字段使用 MappingProxyType 包装，防止外部代码修改内部字典。

    Attributes:
        revision: 版本标识（格式：catalog_YYYYMMDDHHMMSS_NNN）
        created_at: 创建时间（UTC，aware datetime）
        checksum: 内容校验和（SHA256 of skill_id + version 排序拼接）
        skills_by_id: skill_id → SkillBase 的不可变映射
        report_type_index: report_type → ReportSkill 的不可变映射（O(1) 查询）
        data_skill_index: linked_report_skill_id → DataSkill 的不可变映射（O(1) 反向依赖查询）
        retrieval_skill_index: linked_report_skill_id → RetrievalSkill 的不可变映射（O(1) 反向依赖查询）
        skill_count: Skill 总数（缓存，避免 len() 遍历）
        cards_by_key: (skill_id, version) → SkillCard 的不可变映射（P2，§7.4/§8.4）
        bm25_index: BM25 语义索引（P2，§7.4.1；None=未构建，降级为 keyword 匹配）
        embedding_index: Embedding 向量矩阵（P2，§7.4.2；None=未构建或异步构建中）
    """

    revision: str
    created_at: datetime
    checksum: str
    skills_by_id: Mapping[str, SkillBase]
    report_type_index: Mapping[str, ReportSkill]
    data_skill_index: Mapping[str, DataSkill]
    retrieval_skill_index: Mapping[str, RetrievalSkill]
    skill_count: int
    # P2 新增字段（默认 None，向后兼容 P1）
    cards_by_key: Mapping[tuple[str, str], Any] | None = None
    bm25_index: Any | None = None
    embedding_index: Any | None = None
    # P3 新增字段：manifests_by_id 存储 SkillManifest（§8.2），供 hard_filter/PolicyGuard 使用
    manifests_by_id: Mapping[str, Any] | None = None

    def get_by_id(self, skill_id: str) -> SkillBase | None:
        """O(1) 按 skill_id 查询。"""
        return self.skills_by_id.get(skill_id)

    def get_report_by_type(self, report_type: str) -> ReportSkill | None:
        """O(1) 按 report_type 查询（替代原 O(N) 线性扫描）。"""
        return self.report_type_index.get(report_type)

    def get_data_for_report(self, report_skill_id: str) -> DataSkill | None:
        """O(1) 按 linked_report_skill_id 查询 DataSkill（替代原 O(N) 线性扫描）。"""
        return self.data_skill_index.get(report_skill_id)

    def get_retrieval_for_report(self, report_skill_id: str) -> RetrievalSkill | None:
        """O(1) 按 linked_report_skill_id 查询 RetrievalSkill（替代原 O(N) 线性扫描）。"""
        return self.retrieval_skill_index.get(report_skill_id)

    def list_all(self) -> list[SkillBase]:
        """返回所有 Skill（保持配置加载顺序）。"""
        return list(self.skills_by_id.values())

    def list_reports(self) -> list[ReportSkill]:
        """返回所有 ReportSkill。"""
        return [s for s in self.skills_by_id.values() if isinstance(s, ReportSkill)]

    def get_cards_by_key(self) -> Mapping[tuple[str, str], Any]:
        """获取 SkillCard 映射（P2）。

        P1 阶段未构建时返回空映射。
        """
        return self.cards_by_key or {}

    def list_cards(self) -> list[Any]:
        """返回所有 SkillCard 列表（P3，§8.1）。

        供 HardFilterChain 遍历候选 SkillCard。
        P1/P2 阶段未构建 cards_by_key 时返回空列表（降级）。
        """
        if not self.cards_by_key:
            return []
        return list(self.cards_by_key.values())

    def get_card(self, skill_id: str) -> Any | None:
        """按 skill_id 查询 SkillCard（P3，§8.1）。

        供 HardFilterChain 在依赖级租户校验时查询依赖 Skill 的 Card。
        优先从 cards_by_key 查找；未构建时返回 None。
        """
        if not self.cards_by_key:
            return None
        for (sid, _version), card in self.cards_by_key.items():
            if sid == skill_id:
                return card
        return None

    def get_manifest(self, skill_id: str) -> Any | None:
        """按 skill_id 查询 SkillManifest（P3，§8.2）。

        供 HardFilterChain 在 feature_flag 过滤时查询 governance。
        P3 阶段未构建 manifests_by_id 时返回 None（feature_flag 层跳过）。
        """
        if not self.manifests_by_id:
            return None
        return self.manifests_by_id.get(skill_id)


def generate_revision() -> str:
    """生成 Catalog revision（格式：catalog_YYYYMMDDHHMMSS）。

    revision 在同一次 reload 内唯一，用于审计追踪"本次请求用了哪个 Catalog 版本"。
    多 Worker 环境下 revision 不保证全局唯一，但配合 checksum 可检测内容差异。
    """
    now = datetime.now(timezone.utc)
    return f"catalog_{now.strftime('%Y%m%d%H%M%S')}"


def compute_checksum(skills: list[SkillBase]) -> str:
    """计算 Skill 集合的 SHA256 校验和（§7.1 checksum）。

    将 skill_id + version 排序后拼接，取 SHA256 前 16 字符。
    排序确保相同 Skill 集合（无论加载顺序）产生相同 checksum。

    Args:
        skills: Skill 列表

    Returns:
        str: 16 字符的十六进制校验和
    """
    # 按 skill_id 排序，拼接 "skill_id:version"
    sorted_entries = sorted(
        (f"{skill.skill_id}:{skill.version}" for skill in skills),
    )
    content = "|".join(sorted_entries)
    return sha256(content.encode("utf-8")).hexdigest()[:16]


# 向后兼容别名：P3 模块（hard_filter.py 等）使用 CatalogSnapshot 简称
CatalogSnapshot = SkillCatalogSnapshot
