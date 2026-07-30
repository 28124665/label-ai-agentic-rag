"""Pydantic models for skill configuration and resolution."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SkillBase(BaseModel):
    """Fields shared by all runtime skill types."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    skill_type: Literal["report", "data", "retrieval"]
    version: str
    name: str
    description: str
    enabled: bool
    linked_report_skill_id: str | None = None
    required_permissions: list[str] = Field(default_factory=list)


class ReportSkill(SkillBase):
    """Configuration that defines a report structure and its requirements."""

    report_type: str
    # Optional forward links; runtime primarily uses reverse links via
    # DataSkill/RetrievalSkill.linked_report_skill_id.
    linked_skills: dict[str, str] = Field(default_factory=dict)
    intent_keywords: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    allowed_tenants: list[str] = Field(default_factory=list)
    section_templates: list[dict[str, Any]] = Field(default_factory=list)
    core_sections: list[str] = Field(default_factory=list)
    optional_sections: list[str] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    min_evidence_count: int | None = None
    min_db_evidence_count: int | None = None
    min_rag_evidence_count: int | None = None
    evidence_filters: dict[str, Any] = Field(default_factory=dict)
    metric_definitions: list[dict[str, Any]] = Field(default_factory=list)
    metric_formulas: dict[str, Any] = Field(default_factory=dict)
    numeric_tolerance: float | None = None
    time_granularity: str | None = None
    prompt_profile: str | None = None
    writing_style: dict[str, Any] = Field(default_factory=dict)
    forbidden_claims: list[str] = Field(default_factory=list)
    required_disclaimers: list[str] = Field(default_factory=list)
    claim_rules: list[dict[str, Any]] = Field(default_factory=list)
    section_quality_thresholds: dict[str, Any] = Field(default_factory=dict)
    publish_policy: dict[str, Any] = Field(default_factory=dict)
    default_format: str | None = None
    allowed_formats: list[str] = Field(default_factory=list)
    export_templates: dict[str, Any] = Field(default_factory=dict)
    style_config: dict[str, Any] = Field(default_factory=dict)


class DataSkill(SkillBase):
    """Configuration constraining database access for a report."""

    db_targets: list[dict[str, Any]] = Field(default_factory=list)
    metric_bindings: dict[str, Any] = Field(default_factory=dict)
    query_templates: list[dict[str, Any]] = Field(default_factory=list)
    evidence_requirements: list[dict[str, Any]] = Field(default_factory=list)
    policy_constraints: dict[str, Any] = Field(default_factory=dict)
    validation_rules: list[dict[str, Any]] = Field(default_factory=list)
    # SQL Agent 协作接口（docs/数据库Tool渐进式披露LLM化落地设计.md §5.3）：
    # exploration_hints — 无匹配 query_template 且允许探索时，注入 SQL Agent
    #   System Prompt 的业务域提示（preferred_tables / metric_bindings /
    #   table_aliases / business_glossary）
    # fallback_mode — 有 DataSkill 但无匹配 query_template 时：
    #   "sql_agent" = 降级到 SQL Agent 探索（注入 hints）；"reject" = 拒绝（默认）
    exploration_hints: dict[str, Any] = Field(default_factory=dict)
    fallback_mode: Literal["sql_agent", "reject"] = "reject"


class RetrievalSkill(SkillBase):
    """Configuration constraining knowledge retrieval for a report."""

    rag_targets: list[dict[str, Any]] = Field(default_factory=list)
    web_targets: list[dict[str, Any]] = Field(default_factory=list)
    evidence_requirements: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
    query_context_requirements: dict[str, Any] = Field(default_factory=dict)
    section_binding: dict[str, Any] = Field(default_factory=dict)
    validation_rules: list[dict[str, Any]] = Field(default_factory=list)


class ResolvedSkillSet(BaseModel):
    """The resolved skill combination used by downstream runtime components."""

    report_skill: ReportSkill
    data_skill: DataSkill | None = None
    retrieval_skill: RetrievalSkill | None = None
    resolution_source: Literal[
        "skill_id", "report_type", "keyword", "tenant_default", "fallback"
    ]
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class SkillResolveContext(BaseModel):
    """Inputs used to select an applicable report skill."""

    user_question: str
    tenant_id: str
    skill_id: str | None = None
    report_type: str | None = None
    route_decision: dict[str, Any] | None = None
    agent_config: dict[str, Any] | None = None


class SkillResolveResult(BaseModel):
    """Outcome of skill resolution."""

    skill_set: ResolvedSkillSet | None = None
    resolved: bool
    fallback_used: bool = False
    reason: str
    warnings: list[str] = Field(default_factory=list)
