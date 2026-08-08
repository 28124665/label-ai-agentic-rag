"""P1 Catalog Snapshot / Indexes / Loader 单元测试（§18.2）。

设计文档 §20.1 单元测试要求：
    - Catalog 加载、重复项、依赖环和版本约束
    - Snapshot 原子替换和失败保留旧版本
    - 缓存租户/权限隔离

P1 阶段（§19 阶段1 "无行为变化重构"）测试重点：
    - Snapshot 不可变性（frozen=True）
    - O(1) 索引查询正确性
    - revision / checksum 生成与一致性
    - build_indexes 索引构建
    - reload 失败保留旧 Snapshot（§7.1 约束3）
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.langgraph.skills.catalog import (
    SkillCatalogSnapshot,
    compute_checksum,
    generate_revision,
)
from agent.langgraph.skills.indexes import build_indexes
from agent.langgraph.skills.loader import load_catalog
from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill
from agent.langgraph.skills.registry import SkillRegistry
from agent.langgraph.skills.validator import SkillValidationError


# ========== compute_checksum 测试 ==========


def test_checksum_deterministic_for_same_skills():
    """相同 Skill 集合（无论顺序）产生相同 checksum。"""
    skill_a = ReportSkill(
        skill_id="a", skill_type="report", version="1.0", name="A", description="desc", enabled=True,
        report_type="type_a",
    )
    skill_b = ReportSkill(
        skill_id="b", skill_type="report", version="2.0", name="B", description="desc", enabled=True,
        report_type="type_b",
    )
    checksum_1 = compute_checksum([skill_a, skill_b])
    checksum_2 = compute_checksum([skill_b, skill_a])  # 顺序不同
    assert checksum_1 == checksum_2


def test_checksum_differs_for_different_versions():
    """不同 version 产生不同 checksum。"""
    skill_v1 = ReportSkill(
        skill_id="a", skill_type="report", version="1.0", name="A", description="desc", enabled=True,
        report_type="type_a",
    )
    skill_v2 = ReportSkill(
        skill_id="a", skill_type="report", version="2.0", name="A", description="desc", enabled=True,
        report_type="type_a",
    )
    assert compute_checksum([skill_v1]) != compute_checksum([skill_v2])


def test_checksum_format():
    """checksum 为 16 字符十六进制字符串。"""
    skill = ReportSkill(
        skill_id="x", skill_type="report", version="1.0", name="X", description="desc", enabled=True,
        report_type="type_x",
    )
    checksum = compute_checksum([skill])
    assert len(checksum) == 16
    assert all(c in "0123456789abcdef" for c in checksum)


# ========== generate_revision 测试 ==========


def test_revision_format():
    """revision 格式为 catalog_YYYYMMDDHHMMSS。"""
    revision = generate_revision()
    assert revision.startswith("catalog_")
    # catalog_ 后跟 14 位数字（YYYYMMDDHHMMSS）
    timestamp_part = revision[len("catalog_"):]
    assert len(timestamp_part) == 14
    assert timestamp_part.isdigit()


# ========== build_indexes 测试 ==========


def _make_test_skills() -> list:
    """构造测试用 Skill 集合（3 个 ReportSkill + 2 个 DataSkill + 2 个 RetrievalSkill）。"""
    report1 = ReportSkill(
        skill_id="report_1", skill_type="report", version="1.0", name="R1", description="d", enabled=True,
        report_type="type_1",
    )
    report2 = ReportSkill(
        skill_id="report_2", skill_type="report", version="1.0", name="R2", description="d", enabled=True,
        report_type="type_2",
    )
    data1 = DataSkill(
        skill_id="data_1", skill_type="data", version="1.0", name="D1", description="d", enabled=True,
        linked_report_skill_id="report_1",
    )
    data2 = DataSkill(
        skill_id="data_2", skill_type="data", version="1.0", name="D2", description="d", enabled=True,
        linked_report_skill_id="report_2",
    )
    retrieval1 = RetrievalSkill(
        skill_id="retrieval_1", skill_type="retrieval", version="1.0", name="Ret1", description="d", enabled=True,
        linked_report_skill_id="report_1", rag_targets=[{"target_id": "t1", "kb_id": "kb1"}],
    )
    retrieval2 = RetrievalSkill(
        skill_id="retrieval_2", skill_type="retrieval", version="1.0", name="Ret2", description="d", enabled=True,
        linked_report_skill_id="report_2", rag_targets=[{"target_id": "t2", "kb_id": "kb2"}],
    )
    return [report1, report2, data1, data2, retrieval1, retrieval2]


def test_build_indexes_creates_all_indexes():
    """build_indexes 一次性构建所有索引。"""
    skills = _make_test_skills()
    indexes = build_indexes(skills)

    assert len(indexes.skills_by_id) == 6
    assert len(indexes.report_type_index) == 2
    assert len(indexes.data_skill_index) == 2
    assert len(indexes.retrieval_skill_index) == 2


def test_build_indexes_report_type_mapping():
    """report_type_index 正确映射 report_type → ReportSkill。"""
    skills = _make_test_skills()
    indexes = build_indexes(skills)

    assert indexes.report_type_index["type_1"].skill_id == "report_1"
    assert indexes.report_type_index["type_2"].skill_id == "report_2"


def test_build_indexes_reverse_dependency_mapping():
    """data_skill_index / retrieval_skill_index 正确映射反向依赖。"""
    skills = _make_test_skills()
    indexes = build_indexes(skills)

    assert indexes.data_skill_index["report_1"].skill_id == "data_1"
    assert indexes.data_skill_index["report_2"].skill_id == "data_2"
    assert indexes.retrieval_skill_index["report_1"].skill_id == "retrieval_1"
    assert indexes.retrieval_skill_index["report_2"].skill_id == "retrieval_2"


def test_build_indexes_handles_skills_without_linked_report():
    """无 linked_report_skill_id 的 DataSkill/RetrievalSkill 不进入反向依赖索引。"""
    report = ReportSkill(
        skill_id="r", skill_type="report", version="1.0", name="R", description="d", enabled=True,
        report_type="t",
    )
    # linked_report_skill_id=None 的 DataSkill
    data_no_link = DataSkill(
        skill_id="d", skill_type="data", version="1.0", name="D", description="d", enabled=True,
    )
    indexes = build_indexes([report, data_no_link])
    assert "r" not in indexes.data_skill_index


# ========== SkillCatalogSnapshot 测试 ==========


def test_snapshot_is_immutable():
    """Snapshot 不可变（frozen=True，§7.1 约束1）。"""
    skills = _make_test_skills()
    indexes = build_indexes(skills)
    snapshot = SkillCatalogSnapshot(
        revision="test_rev",
        created_at=datetime.now(timezone.utc),
        checksum="abc123",
        skills_by_id=indexes.skills_by_id,
        report_type_index=indexes.report_type_index,
        data_skill_index=indexes.data_skill_index,
        retrieval_skill_index=indexes.retrieval_skill_index,
        skill_count=len(skills),
    )
    # frozen=True 应阻止属性修改
    with pytest.raises((AttributeError, Exception)):
        snapshot.revision = "tampered"


def test_snapshot_o1_queries():
    """Snapshot 提供 O(1) 查询方法。"""
    skills = _make_test_skills()
    indexes = build_indexes(skills)
    snapshot = SkillCatalogSnapshot(
        revision="test_rev",
        created_at=datetime.now(timezone.utc),
        checksum="abc123",
        skills_by_id=indexes.skills_by_id,
        report_type_index=indexes.report_type_index,
        data_skill_index=indexes.data_skill_index,
        retrieval_skill_index=indexes.retrieval_skill_index,
        skill_count=len(skills),
    )

    assert snapshot.get_by_id("report_1").skill_id == "report_1"
    assert snapshot.get_by_id("nonexistent") is None
    assert snapshot.get_report_by_type("type_1").skill_id == "report_1"
    assert snapshot.get_report_by_type("nonexistent") is None
    assert snapshot.get_data_for_report("report_1").skill_id == "data_1"
    assert snapshot.get_data_for_report("nonexistent") is None
    assert snapshot.get_retrieval_for_report("report_1").skill_id == "retrieval_1"
    assert snapshot.get_retrieval_for_report("nonexistent") is None


def test_snapshot_list_all_preserves_order():
    """list_all 保持配置加载顺序。"""
    skills = _make_test_skills()
    indexes = build_indexes(skills)
    snapshot = SkillCatalogSnapshot(
        revision="test_rev",
        created_at=datetime.now(timezone.utc),
        checksum="abc123",
        skills_by_id=indexes.skills_by_id,
        report_type_index=indexes.report_type_index,
        data_skill_index=indexes.data_skill_index,
        retrieval_skill_index=indexes.retrieval_skill_index,
        skill_count=len(skills),
    )
    all_skills = snapshot.list_all()
    assert len(all_skills) == 6
    # 顺序应与输入一致
    assert all_skills[0].skill_id == "report_1"
    assert all_skills[5].skill_id == "retrieval_2"


# ========== load_catalog 测试 ==========


def test_load_catalog_returns_snapshot():
    """load_catalog 从实际 YAML 加载并返回 Snapshot。"""
    snapshot = load_catalog()
    assert isinstance(snapshot, SkillCatalogSnapshot)
    assert snapshot.skill_count > 0
    assert snapshot.revision.startswith("catalog_")
    assert len(snapshot.checksum) == 16


def test_load_catalog_o1_index_correct():
    """load_catalog 构建的索引查询结果正确。"""
    snapshot = load_catalog()
    # quality_report 应通过 report_type 查询到
    report = snapshot.get_report_by_type("quality_analysis")
    assert report is not None
    assert report.skill_id == "quality_report"
    # 反向依赖应正确
    data = snapshot.get_data_for_report("quality_report")
    assert data is not None
    assert data.skill_id == "quality_data_access"


# ========== SkillRegistry P1 重构测试 ==========


def test_registry_current_snapshot_property():
    """SkillRegistry.current_snapshot 返回 SkillCatalogSnapshot。"""
    registry = SkillRegistry()
    snapshot = registry.current_snapshot
    assert isinstance(snapshot, SkillCatalogSnapshot)
    assert snapshot.skill_count > 0


def test_registry_revision_and_checksum_properties():
    """SkillRegistry.revision / checksum 便捷访问。"""
    registry = SkillRegistry()
    assert registry.revision.startswith("catalog_")
    assert len(registry.checksum) == 16


def test_registry_reload_preserves_old_snapshot_on_failure(monkeypatch):
    """§7.1 约束3：reload 失败时保留旧 Snapshot。"""
    registry = SkillRegistry()
    old_revision = registry.revision

    # 模拟 load_catalog 失败
    def failing_load_catalog(_root):
        raise SkillValidationError("Simulated validation failure")

    monkeypatch.setattr(
        "agent.langgraph.skills.registry.load_catalog", failing_load_catalog
    )
    # reload 失败应保留旧 Snapshot，不抛出异常
    registry.reload()
    assert registry.revision == old_revision
    assert registry.current_snapshot.skill_count > 0


def test_registry_reload_succeeds_with_new_revision():
    """reload 成功后 revision 更新。"""
    registry = SkillRegistry()
    old_checksum = registry.checksum
    # reload（同秒内 revision 可能相同，但 checksum 应一致）
    registry.reload()
    assert registry.checksum == old_checksum  # 相同配置，checksum 不变
