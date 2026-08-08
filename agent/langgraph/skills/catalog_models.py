#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""Catalog 模型层（设计文档 v1.1 §8）。

定义 Skill 规模化路由所需的核心数据模型，与现有 ``models.py`` 共存：

- 现有 ``SkillBase`` / ``ReportSkill`` / ``DataSkill`` / ``RetrievalSkill`` 保持不变，
  兼容 19 个现有 YAML 配置（``extra="forbid"``）。
- 本模块新增 ``SkillCard`` / ``SkillManifest`` / ``SkillGovernance`` /
  ``SkillDependency`` / ``ResolvedSkillRef`` / ``CatalogRevision``，
  作为 Catalog 构建和规模化路由的基础模型。

类比 Java 中的领域模型分层：
    ``SkillBase`` ≈ ``@MappedSuperclass``（持久化层基类，对应 YAML）
    ``SkillCard``  ≈ ``@Entity @Dto``（检索/路由层 DTO，Catalog 编译产物）
    ``SkillManifest`` ≈ ``@Entity @AggregateRoot``（聚合根，选中后加载的完整定义）
    ``ResolvedSkillRef`` ≈ ``@Embeddable``（值对象，不可变引用）
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent.langgraph.skills.models import (
    DataSkill,
    ReportSkill,
    ResolvedSkillSet,
    RetrievalSkill,
)


class ConflictLevel(str, Enum):
    """``conflicts_with`` 语义分级（设计文档 v1.1 §9.4）。

    - ``CANDIDATE``：候选级互斥，硬过滤阶段排除，不可同时进入候选集
    - ``EXECUTION``：执行级排他，可同时选中，step 执行时由 Policy Guard 拦截
    """

    CANDIDATE = "candidate"
    EXECUTION = "execution"


class SkillDependency(BaseModel):
    """Skill 依赖声明（设计文档 §9.1）。

    类比 Java 中的 ``@ManyToOne`` 关系声明，带 semver 版本约束。
    """

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    version_constraint: str = "*"  # semver 约束，如 ">=1.0,<2.0"
    required: bool = True  # False = 可选依赖（软依赖）


class SkillGovernance(BaseModel):
    """Skill 治理信息（设计文档 §8.3，生命周期唯一 Owner）。

    作为 ``SkillManifest`` 顶层字段，与 ``card`` 同级。
    ``SkillCard`` 中的 ``lifecycle_status`` 由 Catalog Builder 从此处只读投影，
    不允许人工在 Card 中直接修改。

    类比 Java 中的 ``@Embedded`` 值对象，归属在 Manifest 聚合根下。
    """

    model_config = ConfigDict(extra="forbid")

    lifecycle_status: Literal["draft", "review", "active", "deprecated", "archived"] = "draft"
    owner_team: str = ""
    approval_chain: list[str] = Field(default_factory=list)
    canary_tenants: list[str] = Field(default_factory=list)
    rollout_percentage: float = 100.0


class SkillEvidenceSpec(BaseModel):
    """Skill 证据要求（设计文档 §12.5.1，注入 EvidenceSnapshot 过滤条件）。

    融合规则（§9.3）：多 Skill 组合时阈值取严格值（max），配额取并集。
    """

    model_config = ConfigDict(extra="forbid")

    min_freshness_score: float = 0.0  # 低于此值的 Evidence 在 fusion 阶段丢弃
    min_authority_score: float = 0.0
    min_relevance_score: float = 0.0
    source_quota: dict[str, int] = Field(default_factory=dict)  # {source_type: max_count}


class SkillPolicySpec(BaseModel):
    """Skill 策略约束（设计文档 §8.2 policy_spec）。

    生成 ``SkillPolicyRules`` 供 Policy Guard 使用。
    """

    model_config = ConfigDict(extra="forbid")

    max_db_queries: int | None = None
    max_rag_chunks: int | None = None
    max_web_results: int | None = None
    allowed_sql_operations: list[str] = Field(default_factory=list)  # SELECT/INSERT/...
    forbidden_tables: list[str] = Field(default_factory=list)
    forbidden_url_patterns: list[str] = Field(default_factory=list)


class ConflictDeclaration(BaseModel):
    """``conflicts_with`` 单条声明（设计文档 §9.4）。"""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    level: ConflictLevel = ConflictLevel.CANDIDATE


class SkillCard(BaseModel):
    """SkillCard — Catalog 编译产物，用于检索和硬过滤（设计文档 §8.1）。

    是 Catalog Builder 从 ``SkillManifest`` 投影生成的只读 DTO，
    **不是第二份人工维护配置**。控制在可检索的小体积，不含章节模板、SQL 模板。

    类比 Java 中的 ``@Dto`` / ``@Projection``：
        ``SkillCard`` 是 ``SkillManifest`` 聚合根的投影，用于检索层（BM25/Embedding），
        类似 JPA 的 ``@EntityGraph`` 只加载需要的字段。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)  # frozen=True 不可变

    # 基础标识
    skill_id: str
    version: str
    skill_type: Literal["report", "data", "retrieval"]
    namespace: str  # 如 "quality.report.exception_analysis"
    name: str
    description: str

    # 检索维度
    domains: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)  # database/rag/web/report
    intents: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)  # 强信号匹配（§10.4）
    languages: list[str] = Field(default_factory=list)

    # 检索语料
    positive_examples: list[str] = Field(default_factory=list)
    negative_examples: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    exclusion_rules: list[dict[str, Any]] = Field(default_factory=list)

    # 治理投影（从 governance/policy_spec 只读投影）
    required_permissions: list[str] = Field(default_factory=list)
    allowed_tenants: list[str] = Field(default_factory=list)
    lifecycle_status: str = "active"  # 从 governance 投影
    tenant_overrides: dict[str, bool] = Field(default_factory=dict)

    # 风险与成本
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    cost_class: Literal["low", "medium", "high"] = "low"
    latency_class: Literal["interactive", "batch"] = "interactive"

    # 依赖与冲突
    conflicts_with: list[ConflictDeclaration] = Field(default_factory=list)
    dependencies_healthy: bool = True  # 依赖健康状态投影
    required_dependency_ids: list[str] = Field(default_factory=list)  # 依赖级租户校验用

    # 执行模式（§10.3.1 runtime capability gate 用）
    execution_mode: Literal["main_chain", "react_only"] = "main_chain"

    # 路由投影：report_type 仅用于 §10.4 Stage 2 强信号匹配（report_type → SkillCard 定位）
    # 不属于检索维度，执行定义仍由 SkillManifest.report_spec 管理（§8.3 字段所有权）
    report_type: str | None = None


class SkillManifest(BaseModel):
    """SkillManifest — 完整 Skill 定义，选中后加载（设计文档 §8.2）。

    聚合根，包含 Card + 各类 spec + governance。
    复用现有 ``ReportSkill`` / ``DataSkill`` / ``RetrievalSkill`` 作为 spec，
    避免重复定义 19 个 YAML 已有的字段。

    类比 Java 中的 ``@Entity @AggregateRoot``：
        ``SkillManifest`` 是聚合根，``SkillCard`` 是它的投影 DTO，
        ``ReportSkill`` / ``DataSkill`` / ``RetrievalSkill`` 是 ``@Embedded`` 值对象。
    """

    model_config = ConfigDict(extra="forbid")

    card: SkillCard
    dependencies: list[SkillDependency] = Field(default_factory=list)
    governance: SkillGovernance = Field(default_factory=SkillGovernance)

    # 复用现有模型作为 spec（与 19 个 YAML 兼容）
    report_skill: ReportSkill | None = None
    data_skill: DataSkill | None = None
    retrieval_skill: RetrievalSkill | None = None

    # 新增 spec
    evidence_spec: SkillEvidenceSpec = Field(default_factory=SkillEvidenceSpec)
    policy_spec: SkillPolicySpec = Field(default_factory=SkillPolicySpec)
    invocation_examples: list[dict[str, Any]] = Field(default_factory=list)


class ResolvedSkillRef(BaseModel):
    """不可变 Skill 引用（设计文档 v1.1 §11.1，版本化核心）。

    替代旧 ``ResolvedSkillSet`` 作为 state 中的权威引用。
    一次请求固定一个 ``catalog_revision`` + ``skill_version``，不得在执行中漂移。

    过渡期兼容（§11.6.2）：
        ``skill_set`` 字段保留旧 ``ResolvedSkillSet`` 引用，供 ReAct Policy Guard
        等已接入方回退使用。迁移完成后移除。

    类比 Java 中的 ``@Embeddable`` 值对象 + 版本化引用：
        ``ResolvedSkillRef`` ≈ ``EntityReference<Skill>``，只持有 ID + version，
        需要完整对象时通过 ``catalog_revision`` 从 Snapshot 还原。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)  # frozen=True 不可变

    skill_id: str
    skill_version: str
    catalog_revision: str  # 绑定的 Catalog 版本
    skill_type: Literal["report", "data", "retrieval"] = "report"
    resolution_source: Literal[
        "exact_match",  # 唯一精确 alias 命中（§10.4 强信号）
        "semantic_recall",  # 语义召回 + Reranker
        "rule_keyword",  # Tier1 规则关键词
        "explicit_skill_id",  # 显式指定 skill_id
        "fallback",  # generic fallback
        "no_skill",  # 无需 Skill（chitchat 等）
    ] = "semantic_recall"
    confidence: float = 0.0  # 路由置信度
    fallback_used: bool = False

    # 过渡期：保留旧 ResolvedSkillSet 引用，供已接入方回退
    # 迁移完成后移除（§11.6.2 约束2）
    skill_set: ResolvedSkillSet | None = None

    # 组合 Skill（多 Skill 选中时）
    companion_refs: list["ResolvedSkillRef"] = Field(default_factory=list)


class CatalogRevision(BaseModel):
    """Catalog 版本标识（设计文档 §5.3，不可变 Snapshot 的版本号）。

    每次 Catalog reload 生成新 revision，全局单调递增。
    一次请求固定一个 ``catalog_revision``，保证全链路 Skill 版本一致。

    类比 Java 中的 ``@Version`` 乐观锁 + 不可变事件溯源版本号。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str  # 如 "rev_20260807_001"
    created_at: str  # ISO8601
    checksum: str  # 全量 Manifest 内容的 SHA256，用于一致性校验
    skill_count: int = 0
    active_skill_count: int = 0


class RoutingTraceEvent(BaseModel):
    """路由追踪单条事件（设计文档 §13.1）。"""

    model_config = ConfigDict(extra="forbid")

    stage: str  # hard_filter / recall_bm25 / recall_embedding / rerank / decision
    skill_id: str | None = None
    score: float | None = None
    detail: str = ""
    timestamp_ms: int = 0  # 该阶段耗时


class SkillRoutingTrace(BaseModel):
    """Skill 路由追踪记录（设计文档 §13.1 + §13.4 三元审计链）。

    一次 Skill 路由的完整审计记录，由 ``observability`` 节点统一上报（§13.4.2 约束5）。
    关联 ``evidence_snapshot_id`` 形成三元审计链（§13.4）。
    """

    model_config = ConfigDict(extra="forbid")

    skill_route_trace_id: str  # 唯一追踪 ID
    catalog_revision: str  # 哪个 Skill 版本
    tenant_id: str = ""
    user_question_hash: str = ""  # 脱敏后的 query hash，不存原文（§13.4.2 约束6）

    # 路由过程
    filtered_reason_counts: dict[str, int] = Field(default_factory=dict)  # 硬过滤各原因计数
    events: list[RoutingTraceEvent] = Field(default_factory=list)
    candidates_after_filter: int = 0
    candidates_after_recall: int = 0

    # 路由结果
    resolved_skill_ref: ResolvedSkillRef | None = None
    route_degraded: bool = False  # 是否降级（Reranker 超时等）
    degraded_reason: str = ""

    # ★ v1.1 §13.4 三元审计链字段
    evidence_snapshot_id: str = ""  # 关联的证据快照（evidence_fusion 回写）
    evidence_snapshot_history: list[str] = Field(default_factory=list)  # retry 每次 attempt 的 snapshot_id

    # 性能
    total_latency_ms: int = 0
