"""组合 Skill 清单与融合规则单元测试（设计文档 v1.1 §9.3 / §9.6）。"""
from __future__ import annotations

from agent.langgraph.skills.composite_manifest import CompositeManifestBuilder
from agent.langgraph.skills.dependency_graph import SkillDependencyGraph
from agent.langgraph.skills.models import DataSkill, ReportSkill, RetrievalSkill


# ========== 辅助构造 ==========


def _make_report(
    skill_id: str = "report_1",
    claim_rules: list[dict] | None = None,
    section_templates: list[dict] | None = None,
) -> ReportSkill:
    """构造测试用 ReportSkill。"""
    return ReportSkill(
        skill_id=skill_id,
        skill_type="report",
        version="1.0.0",
        name=skill_id,
        description="测试报告 Skill",
        enabled=True,
        report_type="test_analysis",
        claim_rules=claim_rules or [],
        section_templates=section_templates or [],
    )


def _make_data(
    skill_id: str = "data_1",
    evidence_requirements: list[dict] | None = None,
) -> DataSkill:
    """构造测试用 DataSkill。"""
    return DataSkill(
        skill_id=skill_id,
        skill_type="data",
        version="1.0.0",
        name=skill_id,
        description="测试数据 Skill",
        enabled=True,
        linked_report_skill_id="report_1",
        evidence_requirements=evidence_requirements or [],
    )


def _make_retrieval(
    skill_id: str = "retrieval_1",
    evidence_requirements: list[dict] | None = None,
) -> RetrievalSkill:
    """构造测试用 RetrievalSkill。"""
    return RetrievalSkill(
        skill_id=skill_id,
        skill_type="retrieval",
        version="1.0.0",
        name=skill_id,
        description="测试检索 Skill",
        enabled=True,
        linked_report_skill_id="report_1",
        rag_targets=[{"target_id": "t1", "kb_id": "kb1"}],
        evidence_requirements=evidence_requirements or [],
    )


# ========== 基础构建 ==========


def test_build_composite_manifest_basic():
    """从 report + data + retrieval 构建组合清单。"""
    builder = CompositeManifestBuilder()
    report = _make_report()
    data = _make_data()
    retrieval = _make_retrieval()
    graph = SkillDependencyGraph()

    composite = builder.build_composite_manifest(
        report_skills=[report],
        graph=graph,
        data_skills=[data],
        retrieval_skills=[retrieval],
    )
    assert len(composite.report_skills) == 1
    assert len(composite.data_skills) == 1
    assert len(composite.retrieval_skills) == 1
    assert composite.report_skills[0].skill_id == "report_1"


# ========== 证据规格融合 ==========


def test_fuse_evidence_spec_takes_max():
    """min_freshness_score 取各 Skill 的最大值。"""
    builder = CompositeManifestBuilder()
    data_a = _make_data(evidence_requirements=[{"min_freshness_score": 0.5}])
    data_b = _make_data(
        skill_id="data_2", evidence_requirements=[{"min_freshness_score": 0.8}]
    )
    spec = builder._fuse_evidence_spec([data_a, data_b], [])
    assert spec.min_freshness_score == 0.8


def test_fuse_evidence_spec_source_quota_union():
    """source_quota 取并集（同 key 取 max）。"""
    builder = CompositeManifestBuilder()
    data_a = _make_data(
        evidence_requirements=[{"source_quota": {"db": 3}}]
    )
    data_b = _make_data(
        skill_id="data_2",
        evidence_requirements=[{"source_quota": {"db": 5, "rag": 2}}],
    )
    spec = builder._fuse_evidence_spec([data_a, data_b], [])
    assert spec.source_quota == {"db": 5, "rag": 2}


# ========== 断言规则融合 ==========


def test_fuse_claim_rules_merges():
    """claim_rules 合并：is_required 取并集（任一 True 即 True）。"""
    builder = CompositeManifestBuilder()
    report_a = _make_report(
        claim_rules=[{"claim_id": "c1", "is_required": True}]
    )
    report_b = _make_report(
        skill_id="report_2",
        claim_rules=[{"claim_id": "c1", "is_required": False, "threshold": 0.9}],
    )
    rules = builder._fuse_claim_rules([report_a, report_b])
    assert len(rules) == 1
    assert rules[0]["claim_id"] == "c1"
    assert rules[0]["is_required"] is True


# ========== 章节模板融合 ==========


def test_fuse_section_templates_merges():
    """section_templates 合并：按 section_id 去重。"""
    builder = CompositeManifestBuilder()
    report_a = _make_report(
        section_templates=[{"section_id": "s1", "title": "overview"}]
    )
    report_b = _make_report(
        skill_id="report_2",
        section_templates=[{"section_id": "s1"}, {"section_id": "s2"}],
    )
    templates = builder._fuse_section_templates([report_a, report_b])
    ids = [t["section_id"] for t in templates]
    assert ids == ["s1", "s2"]


# ========== 组合置信度评估 ==========


def test_assess_composition_high_confidence():
    """report + data + retrieval 全在场且无环 → 高置信度。"""
    builder = CompositeManifestBuilder()
    graph = SkillDependencyGraph()
    confidence, reason = builder._assess_composition(
        report_skills=[_make_report()],
        data_skills=[_make_data()],
        retrieval_skills=[_make_retrieval()],
        graph=graph,
    )
    assert confidence == 1.0
    assert "DataSkill" in reason
    assert "RetrievalSkill" in reason


def test_assess_composition_missing_data():
    """缺少 data skill 时组合仍有效，理由不包含 DataSkill。"""
    builder = CompositeManifestBuilder()
    graph = SkillDependencyGraph()
    confidence, reason = builder._assess_composition(
        report_skills=[_make_report()],
        data_skills=[],
        retrieval_skills=[],
        graph=graph,
    )
    assert confidence == 1.0
    assert "DataSkill" not in reason
