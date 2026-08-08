"""Registry for loading and querying configured runtime skills.

P1 重构（§18.2 / §19 阶段1 "无行为变化重构"）：
    - 内部持有 ``SkillCatalogSnapshot`` 引用，替代直接 dict
    - reload 时构建新 Snapshot，构建失败保留旧 Snapshot（§7.1 约束3）
    - 所有查询委托给 Snapshot 的 O(1) 索引
    - 保持外部接口完全兼容（get_by_id / get_report_by_type / ...）
    - 新增 ``current_snapshot`` 属性供外部访问 revision / checksum

设计文档 §15.2 时间复杂度目标：
    | 操作 | 原 SkillRegistry | P1 Snapshot |
    | report type 查询 | O(N) | O(1) |
    | 反向依赖查询 | O(N) | O(1) |
    | Registry 初始化 | 每实例全量加载 | 启动/发布构建一次 Snapshot |

类比 Java：
    ``SkillRegistry`` ≈ ``@Repository``，持有 ``AtomicReference<Snapshot>``，
    reload 时 ``compareAndSet`` 原子替换。查询委托给 Snapshot 的索引（``Map.get``）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent.langgraph.skills.catalog import SkillCatalogSnapshot
from agent.langgraph.skills.loader import load_catalog
from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill, SkillBase
from agent.langgraph.skills.validator import SkillValidationError

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Load and provide indexed access to report, data, and retrieval skills.

    P1 重构：内部持有不可变 ``SkillCatalogSnapshot``，所有查询委托给 Snapshot。
    reload 时构建新 Snapshot，构建失败保留旧 Snapshot（§7.1 约束3）。
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[3] / "config" / "report_skills"
        # _snapshot 持有当前活跃的不可变快照
        # reload 时原子替换（Python GIL 保证单线程内的引用赋值原子性）
        self._snapshot: SkillCatalogSnapshot | None = None
        self.reload()

    @property
    def current_snapshot(self) -> SkillCatalogSnapshot:
        """当前活跃的不可变 Catalog Snapshot。

        供外部访问 revision / checksum / created_at，用于审计追踪
        （§5.5 路由结果必须可复现：catalog_revision 固定）。
        """
        if self._snapshot is None:
            raise RuntimeError("SkillRegistry has no loaded snapshot")
        return self._snapshot

    @property
    def revision(self) -> str:
        """当前 Snapshot 的 revision（便捷访问）。"""
        return self.current_snapshot.revision

    @property
    def checksum(self) -> str:
        """当前 Snapshot 的 checksum（便捷访问）。"""
        return self.current_snapshot.checksum

    def reload(self) -> None:
        """Reload YAML configurations and build a new immutable Snapshot.

        §7.1 约束3：构建失败继续使用上一 Snapshot，禁止部分更新。
        构建成功后原子替换 ``self._snapshot``，失败时保留旧值并记录告警。
        """
        previous_revision = self._snapshot.revision if self._snapshot else None
        try:
            new_snapshot = load_catalog(self.root)
        except (SkillValidationError, ValueError) as e:
            # §7.1 约束3：构建失败保留旧 Snapshot
            if self._snapshot is not None:
                logger.warning(
                    "[SkillRegistry] Catalog reload 失败，保留旧 Snapshot revision=%s: %s",
                    previous_revision,
                    e,
                )
                return
            # 首次加载失败无旧 Snapshot 可用，抛出异常（服务启动失败）
            raise
        # 原子替换（Python GIL 保证引用赋值原子性）
        self._snapshot = new_snapshot
        logger.info(
            "[SkillRegistry] Catalog reload 成功: %s -> %s (checksum=%s, skills=%d)",
            previous_revision,
            new_snapshot.revision,
            new_snapshot.checksum,
            new_snapshot.skill_count,
        )

    def get_by_id(self, skill_id: str) -> SkillBase | None:
        """Return a skill by identifier when it exists.

        P1：委托给 Snapshot.get_by_id（O(1)）。
        """
        return self.current_snapshot.get_by_id(skill_id)

    def get_report_by_type(self, report_type: str) -> ReportSkill | None:
        """Return the configured report skill for a report type.

        P1：委托给 Snapshot.get_report_by_type（O(1)，替代原 O(N) 线性扫描）。
        """
        return self.current_snapshot.get_report_by_type(report_type)

    def get_data_for_report(self, report_skill_id: str) -> DataSkill | None:
        """Return the data skill reverse-linked to a report skill.

        P1：委托给 Snapshot.get_data_for_report（O(1)，替代原 O(N) 线性扫描）。
        """
        return self.current_snapshot.get_data_for_report(report_skill_id)

    def get_retrieval_for_report(self, report_skill_id: str) -> RetrievalSkill | None:
        """Return the retrieval skill reverse-linked to a report skill.

        P1：委托给 Snapshot.get_retrieval_for_report（O(1)，替代原 O(N) 线性扫描）。
        """
        return self.current_snapshot.get_retrieval_for_report(report_skill_id)

    def list_all(self) -> list[SkillBase]:
        """Return all loaded skills in configuration load order.

        P1：委托给 Snapshot.list_all。
        """
        return self.current_snapshot.list_all()
