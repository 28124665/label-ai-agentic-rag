"""Build validated structured queries from DataSkill query templates."""
from __future__ import annotations

from typing import Any

from agent.langgraph.skills.models import DataSkill
from agent.langgraph.tools.database.models import StructuredQuery
from agent.langgraph.tools.database.policy import DatabasePolicyError, StructuredQueryPolicy


class StructuredQueryBuildError(DatabasePolicyError):
    """Raised when a DataSkill template cannot produce a safe query."""


class StructuredQueryBuilder:
    """Resolve one declared query template using trusted runtime context."""

    def __init__(self, data_skill: DataSkill) -> None:
        self.data_skill = data_skill

    def build(self, query_template_id: str, context: dict[str, Any]) -> StructuredQuery:
        """Build a query only from the DataSkill's declared target and table."""
        template = self._find_template(query_template_id)
        target = self._find_target(template.get("target_id", ""))
        table = self._find_table(target, template.get("table_name", ""))
        constraints = self.data_skill.policy_constraints
        tenant_id = context.get("tenant_id")
        filters = dict(context.get("filters", {}))

        for field, value in template.get("filters", {}).items():
            resolved_value = self._resolve_template_value(value, context)
            if resolved_value is not None:
                filters.setdefault(field, resolved_value)

        if constraints.get("require_tenant_filter"):
            if not tenant_id:
                raise StructuredQueryBuildError(
                    "TENANT_FILTER_REQUIRED", "Context must contain tenant_id"
                )
            filters["tenant_id"] = tenant_id

        time_field, time_range = self._resolve_time_filter(template, context)
        if time_field and time_range is not None:
            filters.pop(time_field, None)

        default_limit = constraints.get("default_limit", 1000)
        max_rows = constraints.get("max_rows", default_limit)
        requested_limit = context.get("limit", default_limit)
        limit = min(max(1, int(requested_limit)), int(max_rows))
        dimensions = list(template.get("dimensions", []))
        metrics = list(template.get("metrics", []))
        columns = list(dict.fromkeys([*dimensions, *metrics]))
        query = StructuredQuery(
            db_id=target.get("db_id", ""),
            table_name=table.get("table_name", ""),
            dimensions=dimensions,
            metrics=metrics,
            filters=filters,
            time_field=time_field,
            time_range=time_range,
            time_granularity=template.get("time_granularity"),
            order_by=list(template.get("order_by", [])),
            limit=limit,
            query_template_id=query_template_id,
            data_skill_id=self.data_skill.skill_id,
            columns=columns,
        )
        try:
            StructuredQueryPolicy(table, constraints).validate_query(query)
        except DatabasePolicyError as exc:
            raise StructuredQueryBuildError(exc.code, str(exc)) from exc
        return query

    def _find_template(self, template_id: str) -> dict[str, Any]:
        for template in self.data_skill.query_templates:
            if template.get("template_id") == template_id:
                return template
        raise StructuredQueryBuildError(
            "QUERY_TEMPLATE_NOT_FOUND", f"Template '{template_id}' is not declared"
        )

    @staticmethod
    def _find_target_in_targets(
        template_target_id: str, targets: list[dict[str, Any]]
    ) -> dict[str, Any]:
        for target in targets:
            if target.get("target_id") == template_target_id:
                return target
        raise StructuredQueryBuildError(
            "DB_TARGET_NOT_ALLOWED", f"Target '{template_target_id}' is not declared"
        )

    def _find_target(self, template_target_id: str) -> dict[str, Any]:
        return self._find_target_in_targets(template_target_id, self.data_skill.db_targets)

    def policy_for(self, query: StructuredQuery) -> StructuredQueryPolicy:
        """Return the policy attached to the query's declared target and table."""
        template = self._find_template(query.query_template_id or "")
        target = self._find_target(template.get("target_id", ""))
        table = self._find_table(target, query.table_name)
        return StructuredQueryPolicy(table, self.data_skill.policy_constraints)

    @staticmethod
    def _find_table(target: dict[str, Any], table_name: str) -> dict[str, Any]:
        for table in target.get("tables", []):
            if table.get("table_name") == table_name:
                return table
        raise StructuredQueryBuildError("TABLE_NOT_ALLOWED", f"Table '{table_name}' is not declared")

    @staticmethod
    def _resolve_template_value(value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            return context.get(value[1:-1])
        return value

    @staticmethod
    def _resolve_time_filter(
        template: dict[str, Any], context: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any] | None]:
        for field, value in template.get("filters", {}).items():
            if value == "{time_range}":
                time_range = context.get("time_range")
                if time_range is None and context.get("report_date"):
                    report_date = context["report_date"]
                    time_range = {"start": report_date, "end": report_date}
                return field, time_range
        return None, None
