import pytest

from agent.langgraph.tools.database.models import StructuredQuery
from agent.langgraph.tools.database.policy import DatabasePolicyError, StructuredQueryPolicy


def test_requires_tenant_filter_when_policy_requires_it() -> None:
    policy = StructuredQueryPolicy(
        table_definition={
            "table_name": "quality_exception",
            "required_fields": ["tenant_id", "exception_count"],
            "allowed_filters": ["tenant_id"],
        },
        policy_constraints={"require_tenant_filter": True},
    )

    with pytest.raises(DatabasePolicyError, match="TENANT_FILTER_REQUIRED"):
        policy.validate_query(
            StructuredQuery(
                db_id="qms_prod",
                table_name="quality_exception",
                metrics=["exception_count"],
                columns=["exception_count"],
            )
        )


def test_rejects_unknown_fields_in_structured_query() -> None:
    policy = StructuredQueryPolicy(
        table_definition={
            "table_name": "quality_exception",
            "required_fields": ["tenant_id", "exception_count"],
            "allowed_filters": ["tenant_id"],
        },
        policy_constraints={},
    )

    with pytest.raises(DatabasePolicyError, match="FIELD_NOT_ALLOWED"):
        policy.validate_query(
            StructuredQuery(
                db_id="qms_prod",
                table_name="quality_exception",
                metrics=["untrusted_metric"],
                columns=["untrusted_metric"],
            )
        )
