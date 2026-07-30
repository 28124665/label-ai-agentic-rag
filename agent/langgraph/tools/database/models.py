"""Structured database query models for DataSkill-controlled access."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StructuredQuery(BaseModel):
    """A validated query representation that is safe to compile."""

    db_id: str
    table_name: str
    dimensions: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    time_field: str | None = None
    time_range: dict[str, Any] | None = None
    time_granularity: str | None = None
    order_by: list[str] = Field(default_factory=list)
    limit: int = 1000
    query_template_id: str | None = None
    data_skill_id: str | None = None
    columns: list[str] = Field(default_factory=list)


class DBQueryResult(BaseModel):
    """Normalized outcome from a structured database query."""

    success: bool
    db_id: str
    table_name: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict] = Field(default_factory=list)
    row_count: int = 0
    query_id: str = ""
    latency_ms: int = 0
    sql: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    error_message: str = ""
    error_code: str = ""
    masked_fields: list[str] = Field(default_factory=list)
