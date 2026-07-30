from unittest.mock import AsyncMock, patch

import pytest

from agent.langgraph.tools.database.executor import (
    DatabaseToolStructuredBackend,
    StructuredQueryExecutor,
)
from agent.langgraph.tools.database.models import StructuredQuery
from agent.langgraph.tools.database.policy import DatabasePolicyError, StructuredQueryPolicy
from agent.langgraph.executor.tool_dispatcher import ToolDispatcher
from agent.langgraph.routers.models import PlanStep, StepArgs


def _policy() -> StructuredQueryPolicy:
    return StructuredQueryPolicy(
        table_definition={
            "table_name": "quality_exception",
            "required_fields": ["tenant_id", "customer_id", "exception_count"],
            "allowed_filters": ["tenant_id", "occurred_at"],
            "sensitive_fields": ["customer_id"],
        },
        policy_constraints={
            "require_tenant_filter": True,
            "sensitive_field_policy": {"customer_id": "mask"},
        },
    )


def _query() -> StructuredQuery:
    return StructuredQuery(
        db_id="qms_prod",
        table_name="quality_exception",
        dimensions=["customer_id"],
        metrics=["exception_count"],
        filters={"tenant_id": "tenant-a"},
        time_field="occurred_at",
        time_range={"start": "2026-01-01", "end": "2026-01-31"},
        columns=["customer_id", "exception_count"],
    )


def test_compiles_parameterized_sql_without_raw_filter_values() -> None:
    sql, params = StructuredQueryExecutor.compile(_query(), _policy())

    assert ":tenant_id" in sql
    assert "tenant-a" not in sql
    assert params["tenant_id"] == "tenant-a"


def test_executes_mock_backend_and_masks_sensitive_fields() -> None:
    calls: list[tuple[str, str, dict]] = []

    def execute_sql(db_id: str, sql: str, params: dict) -> list[dict]:
        calls.append((db_id, sql, params))
        return [{"customer_id": "customer-123", "exception_count": 3}]

    result = StructuredQueryExecutor(execute_sql).execute(_query(), _policy())

    assert result.success is True
    assert result.rows == [{"customer_id": "***", "exception_count": 3}]
    assert result.masked_fields == ["customer_id"]
    assert result.row_count == 1
    assert result.query_id
    assert calls[0][0] == "qms_prod"


def test_rejects_non_readonly_metric_injection() -> None:
    query = _query()
    query.metrics = ["exception_count; DELETE FROM quality_exception"]
    query.columns = ["exception_count; DELETE FROM quality_exception"]

    with pytest.raises(DatabasePolicyError, match="FIELD_NOT_ALLOWED"):
        StructuredQueryExecutor.compile(query, _policy())


@pytest.mark.asyncio
async def test_database_tool_backend_uses_existing_mcp_sql_path() -> None:
    database_tool = type(
        "DatabaseToolStub",
        (),
        {
            "_init_mcp_session": lambda self, tenant_id, server_name: None,
            "_execute_sql": AsyncMock(return_value=[{"exception_count": 3}]),
        },
    )()
    backend = DatabaseToolStructuredBackend("tenant-a", "database-mcp")

    with patch(
        "agent.langgraph.tools.database.executor.get_database_tool",
        return_value=database_tool,
    ):
        rows = await backend("qms_prod", "SELECT exception_count LIMIT :limit", {"limit": 1})

    assert rows == [{"exception_count": 3}]
    database_tool._execute_sql.assert_awaited_once_with(
        "qms_prod", "SELECT exception_count LIMIT 1"
    )


def test_default_backend_returns_backend_unavailable_error() -> None:
    result = StructuredQueryExecutor().execute(_query(), _policy())

    assert result.success is False
    assert result.error_code == "BACKEND_UNAVAILABLE"


@pytest.mark.asyncio
async def test_dispatcher_returns_structured_backend_failure_without_crashing() -> None:
    failed_query = type(
        "FailedQuery",
        (),
        {
            "success": False,
            "db_id": "qms_prod",
            "error_code": "BACKEND_UNAVAILABLE",
            "error_message": "MCP backend is unavailable",
            "model_dump": lambda self: {
                "success": False,
                "error_code": "BACKEND_UNAVAILABLE",
                "error_message": "MCP backend is unavailable",
            },
        },
    )()
    step = PlanStep(
        step_id="inspection_rows",
        tool="database",
        args=StepArgs(
            extra={
                "data_skill_id": "quality_data_access",
                "query_template_id": "quality_inspection_for_fpy",
            }
        ),
    )

    with patch.object(
        StructuredQueryExecutor,
        "execute_async",
        new=AsyncMock(return_value=failed_query),
    ):
        result = await ToolDispatcher().dispatch(
            step,
            {
                "tenant_id": "tenant-a",
                "time_range": {"start": "2026-01-01", "end": "2026-01-31"},
            },
            {},
        )

    assert result["success"] is False
    assert result["error_code"] == "BACKEND_UNAVAILABLE"
