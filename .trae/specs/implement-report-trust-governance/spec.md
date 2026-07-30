# 报告可信治理与人机协同补强设计 Spec

## Why
当前 `ReportTool` 已落地基础报告生成（结构化章节、图表、表格、Markdown/HTML 导出、敏感扫描、Verifer 10 项检查），但仍存在三项生产级缺口：
1. **证据血缘不完整** — 已有 Evidence 和章节级 `evidence_refs`，但缺少 Claim 级血缘对象与 `Claim → Evidence → query_id/chunk_id → 表/文档` 闭环
2. **缺少人机协同** — 报告生成链路完全自动化，无草稿审核节点、无低置信度人工核实标记、无用户反馈回流
3. **Skill 治理不足** — 只有 `enabled` / `required_permissions`，无 Skill 生命周期（draft/pending_review/approved/active/deprecated）、无权限交集机制、无变更审计

下一期核心目标：让每份报告都"可信、可审计、可审核、可治理"，每份报告都能说清数字来自哪个库表/哪个查询/哪几行，每份高敏报告都能进入人工审核，Skill 成为受治理的企业资产。

## What Changes
- **新增** `agent/langgraph/evidence/provenance.py` — EvidenceProvenance 统一血缘模型
- **新增** `agent/langgraph/tools/report/claims.py` — Claim Builder / Claim Verifier
- **新增** `agent/langgraph/tools/report/data_sources.py` — DataSourceRef 收集 + source_summary 构造器
- **新增** `agent/langgraph/tools/report/human_review.py` — HumanReview 节点逻辑（可被 LangGraph 调用）
- **新增** `agent/langgraph/feedback/collector.py` — ReportFeedback 模型与采集器
- **新增** `agent/langgraph/skills/governance.py` — Skill 生命周期状态机 + 权限交集
- **扩展** `agent/langgraph/evidence/models.py` — Evidence.metadata 标准化 provenance
- **扩展** `agent/langgraph/tools/report/models.py` — ReportArtifact / Section / ChartSpec / TableSpec 增加 `claim_refs` / `data_sources` / `source_summary` / `human_review_result` / `publish_status` / `claims` 字段
- **扩展** `agent/langgraph/tools/report/verifier.py` — 集成 Claim Lineage Check / 标"未经验证"/"需人工核实"
- **扩展** `agent/langgraph/tools/report/exporter.py` — Markdown / HTML 数据来源区块
- **扩展** `agent/langgraph/state.py` — 新增 report_human_review / report_claims / report_data_sources / report_publish_status 字段
- **扩展** `agent/langgraph/nodes/final_answer.py` — 输出含 `publish_status` / `needs_human_review`
- **新增** `config/report_governance.yaml` — 治理与人审配置
- **新增** `test/agent/langgraph/tools/report/test_*.py`（claims / data_sources / human_review / governance / provenance）

## Impact
- Affected specs:
  - `implement-report-tool`（已完成）— 当前 ReportTool 是本 Spec 的基础，需扩展不破坏
  - `implement-restricted-react-subgraph`（已完成）— ReAct 子图 plan 也可触发 ReportTool
  - `implement-plan-executor`（已完成）— Plan Executor 继续负责分发 report 步骤
- Affected code:
  - `agent/langgraph/evidence/provenance.py` — 新增
  - `agent/langgraph/evidence/models.py` — Evidence.metadata 标准化
  - `agent/langgraph/tools/report/claims.py` — 新增
  - `agent/langgraph/tools/report/data_sources.py` — 新增
  - `agent/langgraph/tools/report/human_review.py` — 新增
  - `agent/langgraph/feedback/collector.py` — 新增
  - `agent/langgraph/skills/governance.py` — 新增
  - `agent/langgraph/tools/report/models.py` — 扩展
  - `agent/langgraph/tools/report/verifier.py` — 扩展
  - `agent/langgraph/tools/report/exporter.py` — 扩展
  - `agent/langgraph/tools/report/report_tool.py` — 串联 Claim Builder / DataSourceRef / HumanReview
  - `agent/langgraph/state.py` — 扩展
  - `agent/langgraph/nodes/final_answer.py` — 扩展
  - `config/report_governance.yaml` — 新增
  - `test/agent/langgraph/tools/report/*` — 新增测试

## ADDED Requirements

### Requirement: EvidenceProvenance 统一血缘模型
The system SHALL 在 `agent/langgraph/evidence/provenance.py` 定义 `EvidenceProvenance`（TypedDict），DB 血缘字段 `db_id` / `table_name` / `query_id` / `sql_fingerprint` / `row_keys`，RAG 血缘字段 `kb_id` / `doc_id` / `chunk_id` / `doc_title` / `doc_uri` / `retrieval_query`，通用字段 `source_target_id` / `query_template_id` / `skill_id` / `step_id`。`Evidence.metadata["provenance"]` 必须符合此结构。

#### Scenario: DB Evidence 携带 query_id
- **WHEN** DBTool 生成 Evidence
- **THEN** Evidence.metadata.provenance 包含 `db_id` / `table_name` / `query_id` / `row_keys`

#### Scenario: RAG Evidence 携带 chunk_id
- **WHEN** RAGTool 生成 Evidence
- **THEN** Evidence.metadata.provenance 包含 `kb_id` / `doc_id` / `chunk_id` / `doc_title`

### Requirement: Claim 级血缘对象
The system SHALL 在 `agent/langgraph/tools/report/claims.py` 定义 `Claim`（TypedDict），含 `claim_id` / `text` / `claim_type`（fact/metric/comparison/trend/recommendation） / `evidence_refs` / `evidence_sources`（EvidenceProvenance 摘要） / `support_status`（pending/supported/partially_supported/unsupported） / `verification_notes` / `confidence` / `needs_human_review`。`ReportSection` / `ChartSpec` / `TableSpec` 必须有 `claim_refs: list[str]`。

#### Scenario: Claim 绑定 Evidence
- **WHEN** Claim Builder 从 LLM 输出生成 Claim
- **THEN** Claim.evidence_refs 至少 1 个 evidence_id，evidence_sources 为对应 provenance 摘要

#### Scenario: Section 绑定 Claim
- **WHEN** SectionGenerator 生成章节
- **THEN** Section.claim_refs 至少 1 个 claim_id，章节与 Claim 双向引用

### Requirement: 未验证 Claim 标记与过滤
The system SHALL 在 `ReportVerifier` 中执行 Claim Lineage Check：metric/comparison/trend 类型 Claim 若 `evidence_refs` 为空则 **直接删除**；fact 默认删除；recommendation 可保留但显式标记"未经验证"。

#### Scenario: Metric Claim 无 Evidence 被删除
- **WHEN** Claim.claim_type="metric" 且 evidence_refs=[]
- **THEN** Claim Builder 丢弃该 Claim，Verifier issues 记录 `{"action": "removed"}`

#### Scenario: Recommendation 无 Evidence 保留并标记
- **WHEN** Claim.claim_type="recommendation" 且 evidence_refs=[]
- **THEN** Claim.support_status="unsupported"，导出时显示"（未经验证）"标记

### Requirement: ReportArtifact 输出数据来源摘要
The system SHALL 在 `ReportArtifact` 中输出 `data_sources: list[DataSourceRef]` 与 `source_summary: list[dict]`。每个 `DataSourceRef` 含 source_type / db_id / table_name / query_id / kb_id / doc_id / chunk_id / `used_by_sections` / `used_by_charts` / `used_by_tables` / `used_by_claims`。

#### Scenario: 报告末尾含数据来源区块
- **WHEN** 导出 Markdown 报告
- **THEN** 末尾增加 `## 数据来源` 区块，列出每个 DB 来源（db_id + table_name + query_id）和 RAG 来源（kb_id + doc_id + chunk_id）

#### Scenario: HTML 报告含可折叠数据来源面板
- **WHEN** 导出 HTML 报告
- **THEN** 报告底部含 `<details><summary>数据来源</summary>...</details>` 可折叠面板

### Requirement: HumanReview 节点
The system SHALL 在 `agent/langgraph/tools/report/human_review.py` 提供 `HumanReviewNode` 异步函数，接收 `ReportArtifact` 输出 `HumanReviewResult`（action / reviewer_id / reviewed_at / comments / edited_sections / approved / publish_allowed）。支持 `approve` / `reject` / `edit` / `comment` / `request_regenerate` 5 种动作。`ReportArtifact` 增加 `human_review_result` 与 `publish_status` 字段（draft/pending_review/approved/published/rejected）。

#### Scenario: 高敏报告进入 HumanReview
- **WHEN** 报告 publish_policy.require_human_review=True 或低置信 Claim 超过阈值
- **THEN** ReportTool 标记 publish_status="pending_review"，不生成正式下载链接

#### Scenario: 人工 approve 后发布
- **WHEN** HumanReviewResult.action="approve"
- **THEN** ReportTool 更新 publish_status="approved"，生成 download_url

#### Scenario: 人工 reject 触发重生成
- **WHEN** HumanReviewResult.action="reject"
- **THEN** publish_status="rejected"，用户可发起"重新生成"指令

### Requirement: 低置信度"需人工核实"标记
The system SHALL 在 `ReportVerifier` 中检查 Claim.confidence < `low_confidence_threshold`（默认 0.7），标记 `needs_human_review=True`，并写入 `review_reason`。导出时显式标注"（需人工核实）"。

#### Scenario: Claim 置信度低于阈值
- **WHEN** Claim.confidence=0.58 且 threshold=0.7
- **THEN** Claim.needs_human_review=True，review_reason="Claim 支撑 Evidence 置信度 0.58，低于阈值 0.70"

#### Scenario: HTML 报告高亮需人工核实
- **WHEN** 导出 HTML
- **THEN** Claim 文本末尾显示 `<span class="needs-review">需人工核实</span>`

### Requirement: 用户反馈闭环
The system SHALL 在 `agent/langgraph/feedback/collector.py` 定义 `ReportFeedback`（TypedDict，report_id / artifact_version / user_id / action / section_id / claim_id / comment / edited_content / created_at）。`FeedbackCollector` 提供 `collect()` / `summarize_optimization_suggestions()` 方法。

#### Scenario: 用户点踩某 Claim
- **WHEN** 用户对 report_claim_id=claim_123 执行 action="dislike"
- **THEN** FeedbackCollector 记录 ReportFeedback，并生成 Skill 优化建议（标低质量 Claim）

### Requirement: Skill 生命周期治理
The system SHALL 在 `agent/langgraph/skills/governance.py` 定义 `SkillGovernance`（TypedDict，skill_id / version / status / created_by / approved_by / created_at / approved_at / deprecated_at / deprecation_reason / change_log）。状态机：`draft → pending_review → approved → active → deprecated`。`SkillResolver` 只能选择 `active` 或租户明确灰度放开的 `approved` Skill。

#### Scenario: deprecated Skill 不被 Resolver 选择
- **WHEN** Skill.status="deprecated"
- **THEN** SkillResolver 跳过该 Skill，Resolver 候选列表为空时记录 warning

#### Scenario: Skill 权限与用户权限求交集
- **WHEN** Skill.required_permissions=["quality:read", "report:generate"] 且 user_permissions=["report:generate"]
- **THEN** effective_permissions=["report:generate"]，不等于 Skill 要求全集，Skill 不可用

### Requirement: 报告生成串联 Claim / DataSources / HumanReview
The system SHALL 修改 `ReportTool.invoke` 流程，新增 Step 3.5 (Claim Builder) / Step 4.5 (DataSourceRef 收集) / Step 6.5 (HumanReview Gate) / Step 7.5 (publish_status 转换)，与现有 Input Validator / Planner / Generator / Verifier / Exporter / Storage 串联。

#### Scenario: 报告流程跑通完整链路
- **WHEN** ReportTool.invoke 被 ToolDispatcher._execute_report 调用
- **THEN** 串联 Input Validator → Evidence Readiness → Planner → Claim Builder → Generators → DataSourceRef → Verifier+Lineage Check → HumanReview Gate → Exporter → Storage，输出 artifact 包含 claims / data_sources / human_review_result / publish_status

## MODIFIED Requirements

### Requirement: Verifier 增加 Claim Lineage Check
原 `ReportVerifier` 必须增加：1) 检查每个 Claim 的 `evidence_refs` 至少 1 个；2) 检查 Section / Chart / Table 的 `claim_refs` 与 ReportArtifact.claims 双向对齐；3) 未验证 Claim 标记（`action: "removed" | "marked"`）。**不修改**现有 10 项检查逻辑。

### Requirement: Exporter 增加数据来源区块
原 Markdown / HTML 导出器末尾必须增加 "## 数据来源" 区块（Markdown）或可折叠面板（HTML），列出每个 `DataSourceRef`。

## REMOVED Requirements
无（本设计为纯增量 + 扩展，不删除现有实现）。

# Task Dependencies
- Task 1 → Task 2 → Task 3 → Task 4 → Task 5
- Task 5 → Task 6
- Task 1 独立
- Task 7 depends on Task 1-6

