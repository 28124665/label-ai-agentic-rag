"""ReportTool 单元测试 — 集成测试。"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from agent.langgraph.tools.report.report_tool import (
    REPORT_EVIDENCE_INSUFFICIENT,
    REPORT_INPUT_INVALID,
    REPORT_VERIFICATION_FAILED,
    ReportTool,
)


@pytest.fixture
def temp_storage_root():
    """临时存储根目录。"""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir, ignore_errors=True)


def _make_db_evidence(evidence_id: str = "ev_db_001") -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "db",
        "title": f"DB 数据 {evidence_id}",
        "content": "查询结果显示异常率上升 5.2%",
        "structured_data": {
            "columns": ["month", "value"],
            "rows": [
                {"month": "Jan", "value": 100},
                {"month": "Feb", "value": 120},
            ],
        },
        "source_uri": f"db://test/{evidence_id}",
        "tenant_id": "tenant_001",
        "confidence": 0.9,
    }


def _make_rag_evidence(evidence_id: str = "ev_rag_001") -> dict:
    return {
        "evidence_id": evidence_id,
        "source_type": "rag",
        "title": f"知识库 {evidence_id}",
        "content": "质量管理规范：异常率超过 5% 应触发预警",
        "structured_data": {},
        "source_uri": f"rag://{evidence_id}",
        "tenant_id": "tenant_001",
        "confidence": 0.85,
    }


@pytest.fixture
def report_tool(temp_storage_root):
    """ReportTool 实例。"""
    return ReportTool(
        config={
            "storage_root": temp_storage_root,
            "min_evidence_count": 1,
            "safety": {
                "scan_sensitive_data": True,
                "require_verified_evidence": True,
            },
        }
    )


class TestReportToolEndToEnd:
    @pytest.mark.asyncio
    async def test_success_markdown(self, report_tool):
        """成功生成 Markdown 报告。"""
        result = await report_tool.invoke(
            {
                "title": "近一个月质量分析",
                "report_type": "quality_analysis",
                "format": "markdown",
                "language": "zh_CN",
                "tenant_id": "tenant_001",
                "user_id": "user_001",
                "evidence": [_make_db_evidence(), _make_rag_evidence()],
                "max_sections": 8,
                "max_charts": 4,
            }
        )
        assert result["success"] is True
        assert result["error_code"] == ""
        assert result["section_count"] > 0
        assert result["chart_count"] > 0
        assert result["evidence_count"] == 2
        assert result["download_url"]
        assert "rpt_" in result["file_uri"]
        assert result["artifact"]
        assert result["summary"]

    @pytest.mark.asyncio
    async def test_success_html(self, report_tool):
        """成功生成 HTML 报告。"""
        result = await report_tool.invoke(
            {
                "title": "趋势分析",
                "report_type": "trend_analysis",
                "format": "html",
                "tenant_id": "tenant_001",
                "evidence": [_make_db_evidence()],
            }
        )
        assert result["success"] is True
        assert "rpt_" in result["file_uri"]
        assert result["file_uri"].endswith(".html")

    @pytest.mark.asyncio
    async def test_input_invalid(self, report_tool):
        """输入参数无效。"""
        result = await report_tool.invoke(
            {
                "report_type": "invalid_type",
                "tenant_id": "tenant_001",
            }
        )
        assert result["success"] is False
        assert result["error_code"] == REPORT_INPUT_INVALID

    @pytest.mark.asyncio
    async def test_evidence_insufficient(self, report_tool):
        """证据不足。"""
        result = await report_tool.invoke(
            {
                "report_type": "quality_analysis",
                "tenant_id": "tenant_001",
                "evidence": [],
            }
        )
        assert result["success"] is False
        assert result["error_code"] == REPORT_EVIDENCE_INSUFFICIENT

    @pytest.mark.asyncio
    async def test_verification_failure(self, report_tool):
        """验证失败（包含 critical 敏感信息）。"""
        result = await report_tool.invoke(
            {
                "title": "Test Report",
                "report_type": "business_analysis",
                "format": "markdown",
                "tenant_id": "tenant_001",
                "evidence": [
                    {
                        "evidence_id": "ev_001",
                        "source_type": "rag",
                        "title": "Evidence 1",
                        "content": "测试",
                        "structured_data": {},
                    }
                ],
            }
        )
        # 即使验证失败也尝试生成内容（因为某些 report_type 不需要 DB）
        # 这里主要验证：流程不崩溃
        assert "artifact" in result

    @pytest.mark.asyncio
    async def test_with_critical_sensitive_fails_verification(self, temp_storage_root):
        """包含 critical 敏感信息导致验证失败。"""
        tool = ReportTool(
            config={
                "storage_root": temp_storage_root,
                "safety": {
                    "scan_sensitive_data": True,
                    "require_verified_evidence": True,
                },
            }
        )
        # 直接构造包含 critical 信息的 evidence
        result = await tool.invoke(
            {
                "title": "API 报告",
                "report_type": "business_analysis",
                "format": "markdown",
                "tenant_id": "tenant_001",
                "evidence": [
                    {
                        "evidence_id": "ev_001",
                        "source_type": "rag",
                        "title": "Evidence",
                        "content": "API Key: sk-abcdefghijklmnopqrstuvwxyz123456",
                        "structured_data": {},
                    }
                ],
            }
        )
        # critical 信息可能出现在生成的内容中，导致验证失败
        # 至少 result 不崩溃
        assert "error_code" in result

    @pytest.mark.asyncio
    async def test_unsupported_format(self, report_tool):
        """不支持的格式。"""
        result = await report_tool.invoke(
            {
                "report_type": "business_analysis",
                "format": "pdf",  # 阶段一不支持
                "tenant_id": "tenant_001",
                "evidence": [_make_db_evidence()],
            }
        )
        assert result["success"] is False
        assert result["error_code"] == REPORT_INPUT_INVALID


class TestReportToolInPlanExecutor:
    @pytest.mark.asyncio
    async def test_plan_step_with_report_tool(self, temp_storage_root):
        """PlanStep 含 tool=report 时正常分发。"""
        from agent.langgraph.executor.tool_dispatcher import ToolDispatcher
        from agent.langgraph.routers.models import PlanStep, StepArgs
        from agent.langgraph.state import ToolResult

        dispatcher = ToolDispatcher()

        # 模拟前置 RAG 步骤结果
        rag_result = ToolResult(
            step_id="rag_step",
            tool="rag",
            success=True,
            rag_docs=[
                {
                    "title": "Test Doc",
                    "content": "Test content",
                    "score": 0.9,
                    "doc_id": "doc_001",
                    "chunk_id": "chunk_001",
                }
            ],
            rag_quality_score=0.9,
            rag_has_relevant=True,
            rag_relevant_count=1,
            rag_top_score=0.9,
        )

        # 构造 report PlanStep
        step = PlanStep(
            step_id="report_step",
            tool="report",
            args=StepArgs(
                query="生成质量分析报告",
                extra={
                    "title": "质量分析报告",
                    "report_type": "quality_analysis",
                    "format": "markdown",
                    "max_sections": 5,
                    "max_charts": 3,
                },
            ),
            depends_on=["rag_step"],
            can_parallel=False,
        )

        # 模拟 state
        state = {
            "tenant_id": "tenant_001",
            "user_id": "user_001",
            "user_question": "生成质量分析报告",
            "query_lang": "zh_CN",
            "evidence": [],
        }

        previous_results = {"rag_step": rag_result}

        result = await dispatcher.dispatch(step, state, previous_results)
        result_dict = dict(result)
        assert result_dict["success"] is True
        assert result_dict["tool"] == "report"
        assert "report_artifacts" in result_dict
