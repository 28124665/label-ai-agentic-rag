"""Parameterized SQL compilation and execution for structured queries."""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from inspect import isawaitable
from typing import Any

from agent.langgraph.tools.database.models import DBQueryResult, StructuredQuery
from agent.langgraph.tools.database.policy import StructuredQueryPolicy
from agent.langgraph.tools.db_runtime import get_db_runtime

ExecuteSql = Callable[[str, str, dict[str, Any]], list[dict]]


class BackendUnavailableError(RuntimeError):
    """Raised when the configured structured-query backend cannot be reached."""


class DatabaseToolStructuredBackend:
    """Adapt the shared DbRuntime (MCP-backed) to structured SQL execution."""

    def __init__(self, tenant_id: str, mcp_server_name: str) -> None:
        self._tenant_id = tenant_id
        self._mcp_server_name = mcp_server_name

    async def __call__(
        self, db_id: str, sql: str, params: dict[str, Any]
    ) -> list[dict]:
        """Initialize the shared MCP session and execute rendered SQL."""
        db_runtime = get_db_runtime()
        try:
            db_runtime.init_session(self._tenant_id, self._mcp_server_name)
            rows = db_runtime.execute_sql(
                db_id, self._render_sql_parameters(sql, params)
            )
            if isawaitable(rows):
                rows = await rows
            return rows
        except Exception as exc:
            raise BackendUnavailableError(
                f"Structured database backend is unavailable: {exc}"
            ) from exc

    @staticmethod
    def _render_sql_parameters(sql: str, params: dict[str, Any]) -> str:
        """Render already-validated bound parameters for the legacy MCP SQL API."""
        rendered = sql
        for name, value in params.items():
            placeholder = f":{name}"
            if isinstance(value, bool):
                replacement = "TRUE" if value else "FALSE"
            elif value is None:
                replacement = "NULL"
            elif isinstance(value, (int, float)):
                replacement = str(value)
            else:
                replacement = "'" + str(value).replace("'", "''") + "'"
            rendered = rendered.replace(placeholder, replacement)
        return rendered


class StructuredQueryExecutor:
    """Compile a policy-validated query and call an injected read-only backend."""

    def __init__(self, execute_sql: ExecuteSql | None = None) -> None:
        self._execute_sql = execute_sql or self._default_backend

    @staticmethod
    def compile(
        query: StructuredQuery, policy: StructuredQueryPolicy
    ) -> tuple[str, dict[str, Any]]:
        """Compile identifiers from the whitelist and bind every value as a parameter."""
        policy.validate_query(query)
        columns = query.columns or list(dict.fromkeys([*query.dimensions, *query.metrics]))
        if not columns:
            columns = ["*"]
        select_clause = ", ".join(columns)
        sql = f"SELECT {select_clause} FROM {query.table_name}"
        predicates: list[str] = []
        params: dict[str, Any] = {}

        for field, value in query.filters.items():
            parameter_name = field
            predicates.append(f"{field} = :{parameter_name}")
            params[parameter_name] = value
        if query.time_field and query.time_range:
            start = query.time_range.get("start")
            end = query.time_range.get("end")
            if start is not None:
                predicates.append(f"{query.time_field} >= :{query.time_field}_start")
                params[f"{query.time_field}_start"] = start
            if end is not None:
                predicates.append(f"{query.time_field} <= :{query.time_field}_end")
                params[f"{query.time_field}_end"] = end
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        if query.dimensions and query.metrics:
            sql += " GROUP BY " + ", ".join(query.dimensions)
        if query.order_by:
            sql += " ORDER BY " + ", ".join(query.order_by)
        sql += " LIMIT :limit"
        params["limit"] = query.limit
        return sql, params

    def execute(self, query: StructuredQuery, policy: StructuredQueryPolicy) -> DBQueryResult:
        """Execute a structured query and normalize its outcome."""
        started_at = time.perf_counter()
        sql, params = self.compile(query, policy)
        try:
            rows = self._execute_sql(query.db_id, sql, params)
            masked_rows, masked_fields = policy.mask_rows(rows)
            columns = list(masked_rows[0].keys()) if masked_rows else query.columns
            return DBQueryResult(
                success=True,
                db_id=query.db_id,
                table_name=query.table_name,
                columns=columns,
                rows=masked_rows,
                row_count=len(masked_rows),
                query_id=str(uuid.uuid4()),
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                sql=sql,
                params=params,
                masked_fields=masked_fields,
            )
        except Exception as exc:
            return self._error_result(query, started_at, sql, params, exc)

    async def execute_async(
        self, query: StructuredQuery, policy: StructuredQueryPolicy
    ) -> DBQueryResult:
        """Execute with a backend that may be asynchronous."""
        started_at = time.perf_counter()
        sql, params = self.compile(query, policy)
        try:
            rows = self._execute_sql(query.db_id, sql, params)
            if isawaitable(rows):
                rows = await rows
            masked_rows, masked_fields = policy.mask_rows(rows)
            columns = list(masked_rows[0].keys()) if masked_rows else query.columns
            return DBQueryResult(
                success=True,
                db_id=query.db_id,
                table_name=query.table_name,
                columns=columns,
                rows=masked_rows,
                row_count=len(masked_rows),
                query_id=str(uuid.uuid4()),
                latency_ms=int((time.perf_counter() - started_at) * 1000),
                sql=sql,
                params=params,
                masked_fields=masked_fields,
            )
        except Exception as exc:
            return self._error_result(query, started_at, sql, params, exc)

    @staticmethod
    def _error_result(
        query: StructuredQuery,
        started_at: float,
        sql: str,
        params: dict[str, Any],
        error: Exception,
    ) -> DBQueryResult:
        error_code = (
            "BACKEND_UNAVAILABLE"
            if isinstance(error, BackendUnavailableError)
            else "DATABASE_EXECUTION_FAILED"
        )
        return DBQueryResult(
            success=False,
            db_id=query.db_id,
            table_name=query.table_name,
            query_id=str(uuid.uuid4()),
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            sql=sql,
            params=params,
            error_message=str(error),
            error_code=error_code,
        )

    @staticmethod
    def _default_backend(db_id: str, sql: str, params: dict[str, Any]) -> list[dict]:
        raise BackendUnavailableError(
            "No structured database backend was configured for this request"
        )
