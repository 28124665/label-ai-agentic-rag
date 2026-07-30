"""Whitelist, tenant, and sensitive-data policy for structured queries."""
from __future__ import annotations

import re
from typing import Any

from agent.langgraph.tools.database.models import StructuredQuery

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_OPERATIONS = frozenset(
    {"insert", "update", "delete", "drop", "alter", "truncate", "create", "merge", "execute"}
)


class DatabasePolicyError(ValueError):
    """Typed policy error that carries a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class StructuredQueryPolicy:
    """Validate a query against the resolved DataSkill table configuration."""

    def __init__(
        self,
        table_definition: dict[str, Any],
        policy_constraints: dict[str, Any],
    ) -> None:
        self.table_definition = table_definition
        self.policy_constraints = policy_constraints
        self.allowed_fields = set(table_definition.get("required_fields", []))
        self.allowed_filters = set(table_definition.get("allowed_filters", []))
        self.sensitive_fields = set(table_definition.get("sensitive_fields", []))
        self.sensitive_field_policy = policy_constraints.get("sensitive_field_policy", {})
        self.forbidden_operations = _FORBIDDEN_OPERATIONS | {
            str(operation).lower()
            for operation in policy_constraints.get("forbidden_operations", [])
        }

    def validate_query(self, query: StructuredQuery) -> None:
        """Ensure all query identifiers and tenant constraints are declared."""
        if query.table_name != self.table_definition.get("table_name"):
            raise DatabasePolicyError("TABLE_NOT_ALLOWED", f"Table '{query.table_name}' is not allowed")

        if self.policy_constraints.get("require_tenant_filter") and not query.filters.get(
            "tenant_id"
        ):
            raise DatabasePolicyError(
                "TENANT_FILTER_REQUIRED", "A tenant_id filter is required for this query"
            )

        for field in [*query.dimensions, *query.metrics, *query.columns, *query.order_by]:
            self._validate_field(field)
        for field in query.filters:
            if field not in self.allowed_filters:
                raise DatabasePolicyError("FIELD_NOT_ALLOWED", f"Filter field '{field}' is not allowed")
        if query.time_field is not None and query.time_field not in self.allowed_filters:
            raise DatabasePolicyError(
                "FIELD_NOT_ALLOWED", f"Time field '{query.time_field}' is not an allowed filter"
            )

    def mask_rows(self, rows: list[dict]) -> tuple[list[dict], list[str]]:
        """Apply configured masking without modifying backend-owned rows."""
        mask_fields = {
            field
            for field in self.sensitive_fields
            if self.sensitive_field_policy.get(field, "mask") == "mask"
        }
        present_fields = sorted(
            field for field in mask_fields if any(field in row for row in rows)
        )
        masked_rows = [
            {key: "***" if key in mask_fields else value for key, value in row.items()}
            for row in rows
        ]
        return masked_rows, present_fields

    def _validate_field(self, field: str) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(field) or field.lower() in self.forbidden_operations:
            raise DatabasePolicyError("FIELD_NOT_ALLOWED", f"Field '{field}' is not allowed")
        if field not in self.allowed_fields:
            raise DatabasePolicyError("FIELD_NOT_ALLOWED", f"Field '{field}' is not allowed")
