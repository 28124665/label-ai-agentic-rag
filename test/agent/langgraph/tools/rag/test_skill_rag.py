"""Tests for RetrievalSkill-guided RAG request preparation and invocation."""

from unittest.mock import AsyncMock, patch

import pytest

from agent.langgraph.tools.rag.skill_rag import (
    SkillRAGContextError,
    SkillRAGExecutor,
)
from agent.langgraph.executor.tool_dispatcher import ToolDispatcher
from agent.langgraph.routers.models import PlanStep, StepArgs


def _quality_sop_request(**overrides: object) -> dict:
    request = {
        "retrieval_skill_id": "quality_knowledge_retrieval",
        "rag_target_id": "quality_sop",
        "query_template_id": "corrective_action_standard",
        "tenant_id": "tenant-1",
        "time_range": {"start": "2026-01-01", "end": "2026-01-31"},
        "root_cause_category": "设备参数波动",
    }
    request.update(overrides)
    return request


class TestSkillRAGExecutor:
    """Verify RetrievalSkill constraints are translated before RAG invocation."""

    def test_builds_quality_sop_request_from_declared_target(self):
        prepared = SkillRAGExecutor().prepare(_quality_sop_request())

        assert prepared is not None
        assert prepared.input_data["query"] == "质量异常纠正预防措施 设备参数波动"
        assert prepared.input_data["kb_ids"] == ["quality_sop_kb"]
        assert prepared.input_data["top_k"] == 6
        assert prepared.input_data["document_filters"] == {
            "doc_type": ["sop", "quality_standard"],
            "status": ["active"],
        }
        assert prepared.input_data["metadata_filters"] == {"tenant_id": "tenant-1"}

    def test_fills_query_template_with_root_cause_category(self):
        prepared = SkillRAGExecutor().prepare(_quality_sop_request())

        assert "设备参数波动" in prepared.input_data["query"]
        assert "{root_cause_category}" not in prepared.input_data["query"]

    def test_required_target_missing_template_context_fails_clearly(self):
        with pytest.raises(
            SkillRAGContextError,
            match="quality_sop.*root_cause_category",
        ):
            SkillRAGExecutor().prepare(
                _quality_sop_request(root_cause_category=None)
            )

    def test_optional_target_missing_template_context_is_skipped(self):
        prepared = SkillRAGExecutor().prepare(
            _quality_sop_request(
                rag_target_id="quality_history",
                query_template_id="eight_d_reference",
                product_name=None,
                exception_type=None,
            )
        )

        assert prepared is None

    def test_target_kb_cannot_be_overridden_by_request(self):
        prepared = SkillRAGExecutor().prepare(
            _quality_sop_request(kb_ids=["unapproved-kb"])
        )

        assert prepared is not None
        assert prepared.input_data["kb_ids"] == ["quality_sop_kb"]

    @pytest.mark.asyncio
    async def test_invocation_uses_target_top_k(self):
        mock_tool = AsyncMock()
        mock_tool.invoke.return_value = {
            "docs": [],
            "quality_score": 0.0,
            "has_relevant": False,
            "relevant_count": 0,
            "top_score": 0.0,
        }
        with patch(
            "agent.langgraph.tools.rag.skill_rag.get_rag_tool",
            return_value=mock_tool,
        ):
            await SkillRAGExecutor().invoke(_quality_sop_request(top_k=99))

        assert mock_tool.invoke.await_args.args[0]["top_k"] == 6

    def test_rejects_document_missing_required_filter_field(self):
        assert not SkillRAGExecutor._matches_filters(
            {"metadata": {"tenant_id": "tenant-1"}},
            {"status": ["active"]},
            {"tenant_id": "tenant-1"},
        )

    def test_rejects_document_missing_required_tenant_id(self):
        assert not SkillRAGExecutor._matches_filters(
            {"metadata": {"status": "active"}},
            {"status": ["active"]},
            {"tenant_id": "tenant-1"},
        )

    @pytest.mark.asyncio
    async def test_dispatcher_uses_skill_path_when_retrieval_skill_is_supplied(self):
        step = PlanStep(
            step_id="retrieve_sop",
            tool="rag",
            args=StepArgs(
                query="ignored legacy query",
                kb_ids=["unapproved-kb"],
                extra={
                    "retrieval_skill_id": "quality_knowledge_retrieval",
                    "rag_target_id": "quality_sop",
                    "query_template_id": "corrective_action_standard",
                    "root_cause_category": "设备参数波动",
                },
            ),
        )
        state = {
            "tenant_id": "tenant-1",
            "time_range": {"start": "2026-01-01", "end": "2026-01-31"},
            "user_question": "生成质量报告",
        }
        mock_tool = AsyncMock()
        mock_tool.invoke.return_value = {
            "docs": [],
            "quality_score": 0.0,
            "has_relevant": False,
            "relevant_count": 0,
            "top_score": 0.0,
        }

        with patch(
            "agent.langgraph.tools.rag.skill_rag.get_rag_tool",
            return_value=mock_tool,
        ):
            result = await ToolDispatcher()._execute_rag(step, state)

        assert mock_tool.invoke.await_args.args[0]["kb_ids"] == ["quality_sop_kb"]
        assert result["kb_ids"] == ["quality_sop_kb"]
