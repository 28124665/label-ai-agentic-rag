"""ReportTool skill-aware behavior tests."""
from __future__ import annotations

import shutil
import tempfile

import pytest

from agent.langgraph.skills import SkillRegistry
from agent.langgraph.tools.report.planner import ReportPlanner
from agent.langgraph.tools.report.report_tool import (
    REPORT_EVIDENCE_INSUFFICIENT,
    ReportTool,
)
from agent.langgraph.tools.report.verifier import ReportVerifier


def _db_evidence(evidence_id: str, table_name: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "db",
        "title": table_name,
        "content": "质量指标数据",
        "structured_data": {
            "columns": ["month", "exception_count"],
            "rows": [{"month": "2026-07", "exception_count": 10}],
        },
        "metadata": {"table_name": table_name},
    }


def _rag_evidence() -> dict:
    return {
        "evidence_id": "quality_sop_refs",
        "source_type": "rag",
        "title": "质量 SOP",
        "content": "质量异常纠正预防措施规范",
        "structured_data": {},
        "metadata": {"source_target_id": "quality_sop"},
    }


@pytest.fixture
def storage_root() -> str:
    root = tempfile.mkdtemp()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def quality_evidence() -> list[dict]:
    return [
        _db_evidence("quality_exception_rows", "quality_exception"),
        _db_evidence("production_output_rows", "production_output"),
        _db_evidence("inspection_rows", "quality_inspection_result"),
        _rag_evidence(),
    ]


def test_quality_skill_templates_drive_core_section_plan(quality_evidence):
    registry = SkillRegistry()
    report_skill = registry.get_by_id("quality_report")

    plan = ReportPlanner().plan_from_skill(
        report_skill=report_skill,
        title="质量报告",
        evidence=quality_evidence,
    )

    assert [section["section_id"] for section in plan["sections"]] == report_skill.core_sections


@pytest.mark.asyncio
async def test_skill_artifact_records_skill_identity(storage_root, quality_evidence):
    result = await ReportTool(
        config={
            "storage_root": storage_root,
            "safety": {"scan_sensitive_data": False, "require_verified_evidence": True},
        }
    ).invoke(
        {
            "title": "质量报告",
            "skill_id": "quality_report",
            "tenant_id": "tenant_001",
            "format": "markdown",
            "evidence": quality_evidence,
        }
    )

    assert result["success"] is True
    assert result["artifact"]["metadata"]["skill_id"] == "quality_report"
    assert result["artifact"]["metadata"]["skill_version"] == "1.0"


@pytest.mark.asyncio
async def test_legacy_report_type_still_generates_report(storage_root):
    result = await ReportTool(
        config={
            "storage_root": storage_root,
            "safety": {"scan_sensitive_data": False, "require_verified_evidence": True},
        }
    ).invoke(
        {
            "report_type": "business_analysis",
            "tenant_id": "tenant_001",
            "evidence": [
                _db_evidence("legacy_db", "business_data"),
                {
                    "evidence_id": "legacy_rag",
                    "source_type": "rag",
                    "title": "业务知识",
                    "content": "业务背景说明",
                    "structured_data": {},
                },
            ],
        }
    )

    assert result["success"] is True
    assert result["artifact"]["metadata"].get("skill_id") is None


@pytest.mark.parametrize("report_type", ["cost_analysis", "production_daily"])
def test_configured_report_types_are_accepted(report_type):
    assert ReportTool()._validate_input(
        {"report_type": report_type, "tenant_id": "tenant_001"}
    ) is None


@pytest.mark.asyncio
async def test_skill_missing_required_evidence_uses_summary_only(storage_root):
    result = await ReportTool(
        config={
            "storage_root": storage_root,
            "safety": {"scan_sensitive_data": False, "require_verified_evidence": True},
        }
    ).invoke(
        {
            "skill_id": "quality_report",
            "tenant_id": "tenant_001",
            "evidence": [_db_evidence("quality_exception_rows", "quality_exception")],
        }
    )

    assert result["success"] is False
    assert result["error_code"] == REPORT_EVIDENCE_INSUFFICIENT
    assert result["partial"] is True
    assert result["publish_mode"] == "summary_only"
    assert "production_output_rows" in result["missing_required_evidence"]


def test_publish_policy_requires_all_core_sections():
    result = ReportVerifier().verify(
        artifact={
            "sections": [
                {
                    "section_id": "overview",
                    "title": "概况",
                    "section_type": "overview",
                    "content": "质量数据",
                    "evidence_refs": ["quality_exception_rows"],
                }
            ],
            "charts": [],
            "tables": [],
        },
        evidence=[_db_evidence("quality_exception_rows", "quality_exception")],
        publish_policy={"require_all_core_sections": True},
        core_sections=["overview", "conclusion"],
    )

    assert result["passed"] is False
    assert any(
        issue["code"] == "VER_SKILL_CORE_SECTION_MISSING"
        for issue in result["issues"]
    )
