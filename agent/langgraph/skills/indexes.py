"""Skill Catalog 索引构建器（P1，§7.2 / §15.2）。

设计文档 §7.2 索引要求：
    - ``(skill_id, version)`` 精确索引
    - ``skill_id -> active_version`` 索引
    - ``report_type -> candidates`` 索引
    - 依赖和反向依赖索引

设计文档 §15.2 时间复杂度目标：
    | 操作 | 当前 | 目标 |
    | report type 查询 | O(N) | O(1) 或 O(k) |
    | 反向依赖查询 | O(N) | O(1) 或 O(k) |
    | 重复 ID 校验 | O(N^2) | O(N) |

P1 阶段实现的索引：
    - ``skills_by_id``：skill_id → SkillBase（O(1) 精确查询）
    - ``report_type_index``：report_type → ReportSkill（O(1) report type 查询）
    - ``data_skill_index``：linked_report_skill_id → DataSkill（O(1) 反向依赖查询）
    - ``retrieval_skill_index``：linked_report_skill_id → RetrievalSkill（O(1) 反向依赖查询）

类比 Java：
    索引构建 ≈ ``@PostConstruct`` 时构建 ``Map<Key, Value>`` 缓存，
    查询时 O(1) ``map.get(key)`` 替代 O(N) ``stream().filter().findFirst()``。
"""
from __future__ import annotations

from typing import NamedTuple

from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill, SkillBase


class CatalogIndexes(NamedTuple):
    """Catalog 索引集合（不可变，由 ``build_indexes`` 一次性构建）。

    所有索引在 Snapshot 构建时一次性构建，运行时只读，保证 O(1)/O(k) 查询。
    """

    skills_by_id: dict[str, SkillBase]
    report_type_index: dict[str, ReportSkill]
    data_skill_index: dict[str, DataSkill]
    retrieval_skill_index: dict[str, RetrievalSkill]


def build_indexes(skills: list[SkillBase]) -> CatalogIndexes:
    """从 Skill 列表一次性构建所有索引（O(N)）。

    构建顺序：
        1. skills_by_id：遍历一次建立 skill_id → SkillBase 映射
        2. report_type_index：遍历 ReportSkill 建立 report_type → ReportSkill 映射
        3. data_skill_index：遍历 DataSkill 建立 linked_report_skill_id → DataSkill 映射
        4. retrieval_skill_index：遍历 RetrievalSkill 建立 linked_report_skill_id → RetrievalSkill 映射

    注意：
        - 重复 skill_id / report_type 的检测由 validator.py 负责（O(N)）
        - 此函数假设输入已通过校验，不重复检测
        - 反向依赖索引按 linked_report_skill_id 建立一对一映射
          （P1 阶段保持现有"一个 ReportSkill 绑定一个 DataSkill/RetrievalSkill"语义，
           P3 阶段引入依赖图后改为一对多映射）

    Args:
        skills: 已通过校验的 Skill 列表

    Returns:
        CatalogIndexes: 构建完成的索引集合
    """
    skills_by_id: dict[str, SkillBase] = {}
    report_type_index: dict[str, ReportSkill] = {}
    data_skill_index: dict[str, DataSkill] = {}
    retrieval_skill_index: dict[str, RetrievalSkill] = {}

    for skill in skills:
        # 1. skill_id → SkillBase（O(1) 精确查询）
        skills_by_id[skill.skill_id] = skill

        # 2. report_type → ReportSkill（O(1) report type 查询）
        if isinstance(skill, ReportSkill):
            report_type_index[skill.report_type] = skill

        # 3. linked_report_skill_id → DataSkill（O(1) 反向依赖查询）
        if isinstance(skill, DataSkill) and skill.linked_report_skill_id:
            data_skill_index[skill.linked_report_skill_id] = skill

        # 4. linked_report_skill_id → RetrievalSkill（O(1) 反向依赖查询）
        if isinstance(skill, RetrievalSkill) and skill.linked_report_skill_id:
            retrieval_skill_index[skill.linked_report_skill_id] = skill

    return CatalogIndexes(
        skills_by_id=skills_by_id,
        report_type_index=report_type_index,
        data_skill_index=data_skill_index,
        retrieval_skill_index=retrieval_skill_index,
    )
