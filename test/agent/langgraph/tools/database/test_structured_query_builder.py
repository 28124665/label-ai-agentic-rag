import pytest

from agent.langgraph.skills.models import DataSkill
from agent.langgraph.skills.registry import SkillRegistry
from agent.langgraph.tools.database.structured_query import (
    StructuredQueryBuildError,
    StructuredQueryBuilder,
)


def _quality_skill() -> DataSkill:
    return DataSkill.model_validate(
        {
            "skill_id": "quality_data_access",
            "skill_type": "data",
            "version": "1.0",
            "name": "Quality",
            "description": "Quality data",
            "enabled": True,
            "db_targets": [
                {
                    "target_id": "qms_quality",
                    "db_id": "qms_prod",
                    "tables": [
                        {
                            "table_name": "quality_exception",
                            "required_fields": [
                                "tenant_id",
                                "occurred_at",
                                "factory_id",
                                "exception_type",
                                "exception_count",
                            ],
                            "allowed_filters": ["tenant_id", "occurred_at", "factory_id"],
                            "sensitive_fields": ["customer_id"],
                        }
                    ],
                }
            ],
            "query_templates": [
                {
                    "template_id": "quality_exception_detail",
                    "target_id": "qms_quality",
                    "table_name": "quality_exception",
                    "dimensions": ["occurred_at", "factory_id", "exception_type"],
                    "metrics": ["exception_count"],
                    "filters": {
                        "tenant_id": "{tenant_id}",
                        "occurred_at": "{time_range}",
                    },
                }
            ],
            "policy_constraints": {
                "default_limit": 100,
                "max_rows": 500,
                "require_tenant_filter": True,
            },
        }
    )


def test_builds_quality_exception_query_from_template() -> None:
    data_skill = SkillRegistry().get_by_id("quality_data_access")
    assert isinstance(data_skill, DataSkill)
    query = StructuredQueryBuilder(data_skill).build(
        "quality_exception_detail",
        {
            "tenant_id": "tenant-a",
            "time_range": {"start": "2026-01-01", "end": "2026-01-31"},
        },
    )

    assert query.db_id == "qms_prod"
    assert query.table_name == "quality_exception"
    assert query.dimensions == [
        "occurred_at",
        "factory_id",
        "line_id",
        "product_id",
        "exception_type",
        "root_cause_category",
        "status",
    ]
    assert query.metrics == ["exception_count"]
    assert query.filters["tenant_id"] == "tenant-a"
    assert query.time_field == "occurred_at"
    assert query.time_range == {"start": "2026-01-01", "end": "2026-01-31"}
    assert query.limit == 1000


def test_forces_tenant_filter_from_context() -> None:
    query = StructuredQueryBuilder(_quality_skill()).build(
        "quality_exception_detail",
        {
            "tenant_id": "tenant-a",
            "time_range": {"start": "2026-01-01", "end": "2026-01-31"},
            "filters": {"tenant_id": "untrusted-tenant", "factory_id": "F01"},
        },
    )

    assert query.filters["tenant_id"] == "tenant-a"
    assert query.filters["factory_id"] == "F01"


def test_rejects_unknown_table() -> None:
    skill = _quality_skill()
    skill.query_templates[0]["table_name"] = "unknown_table"

    with pytest.raises(StructuredQueryBuildError, match="TABLE_NOT_ALLOWED"):
        StructuredQueryBuilder(skill).build(
            "quality_exception_detail", {"tenant_id": "tenant-a"}
        )


def test_rejects_unknown_context_filter() -> None:
    with pytest.raises(StructuredQueryBuildError, match="FIELD_NOT_ALLOWED"):
        StructuredQueryBuilder(_quality_skill()).build(
            "quality_exception_detail",
            {"tenant_id": "tenant-a", "filters": {"untrusted_field": "value"}},
        )
