# 实现报告生成 Tool Spec

## Why
当前 Agentic RAG 系统已具备 LangGraph 主干、受限 ReAct 子图、Plan Executor、Evidence 标准化等能力，但**报告/报表生成**需求（多源数据分析报告、业务趋势报告、管理层汇报材料）仍依赖"LLM 自由生成文本"，存在以下问题：
1. 数字无证据追溯（幻觉风险高）
2. 无结构化章节、图表、表格
3. 无法导出 Markdown / HTML / PDF / Excel / PPTX 等可交付格式
4. 无租户隔离、敏感信息扫描等安全机制
5. 无多阶段章节独立评分与降级

本设计按 `docs/报告生成Tool设计.md` 落地 `ReportTool`，作为**受控工具**接入 Plan Executor 调度体系，与现有 RAG / DB / Web 工具并列，不重造独立 Agent。

## What Changes
- **新增** `agent/langgraph/tools/report/` 模块（report_tool / models / planner / generator / chart_builder / table_builder / verifier / exporter / storage / templates / sensitive_scanner）
- **扩展** `agent/langgraph/routers/models.py` — `PlanStep.tool` 增加 `"report"` 字面量
- **扩展** `agent/langgraph/executor/tool_dispatcher.py` — 新增 `_execute_report()` 分发分支
- **扩展** `agent/langgraph/executor/result_aggregator.py` — 聚合 `report_artifacts` / `report_summary` / `report_quality_score`
- **扩展** `agent/langgraph/state.py` — 新增 `report_artifacts` / `report_summary` / `report_quality_score` / `report_error` 字段
- **扩展** `agent/langgraph/evidence/models.py` — `Evidence.source_type` 增加 `"report"` 字面量
- **不破坏** 现有主干（intent_router / plan_executor / react_subgraph / quality_check / prompt_assembly / llm_generate / hallucination）向后兼容
- **新增** `config/report.yaml` — Report 工具配置（允许格式、最大章节数、敏感扫描开关等）
- **新增** `test/agent/langgraph/tools/report/` 单元测试（≥ 30 个用例全部通过）

## Impact
- Affected specs:
  - `implement-restricted-react-subgraph`（已完成）— ReAct 子图不直接生成报告，但可在 PlanStep 中触发 ReportTool
  - `implement-plan-executor`（已完成）— Plan Executor 通过 ToolDispatcher 分发 `tool="report"` 步骤
  - `reflect-and-clarify`（已完成）— final_answer 节点新增 `report_generated` / `report_failed` 类型最终答案
- Affected code:
  - `agent/langgraph/routers/models.py` — PlanStep.tool Literal 扩展
  - `agent/langgraph/executor/tool_dispatcher.py` — _execute_report 分发
  - `agent/langgraph/executor/result_aggregator.py` — report 字段聚合
  - `agent/langgraph/state.py` — report_* 字段
  - `agent/langgraph/evidence/models.py` — Evidence.source_type Literal 扩展
  - `agent/langgraph/tools/report/*` — 新增目录
  - `config/report.yaml` — 新增配置
  - `rag/prompts/report_*.md` — 新增 Prompt 模板
  - `test/agent/langgraph/tools/report/*` — 新增测试

## ADDED Requirements

### Requirement: ReportTool 作为受控工具接入 Plan Executor
The system SHALL 提供 `ReportTool` 作为受控工具，**不直接访问数据源、不绕过权限**，仅基于已收集的 `evidence` 列表生成结构化报告。

#### Scenario: PlanStep 含 report 步骤被正常分发
- **WHEN** ExecutionPlan 包含 `tool="report"` 的步骤，且 `depends_on` 已完成
- **THEN** `ToolDispatcher.dispatch` 调用 `_execute_report`，ReportTool 生成 ReportArtifact 并写回 `state.report_artifacts`

#### Scenario: ReportTool 拒绝无证据的报告
- **WHEN** `state.evidence` 为空或不足
- **THEN** ReportTool 返回 `success=False` + `error_code="REPORT_EVIDENCE_INSUFFICIENT"`，**不生成正式报告**

### Requirement: 报告生成严格基于 Evidence，禁止凭空生成
The system SHALL 保证报告中**每个数字、每个结论、每个图表数据**都可追溯到 `evidence_id`，不允许 LLM 自由编造。

#### Scenario: 数字必须来自结构化 Evidence
- **WHEN** Report Verifier 检测到 Section 中的数字无法在 evidence 中找到对应记录
- **THEN** Verifier 返回 `passed=False` + 详细 issues，报告**不得导出**

#### Scenario: 图表数据必须绑定 Evidence
- **WHEN** Chart Builder 生成图表
- **THEN** 图表的 `data` 字段必须来自 DB Evidence 的 `structured_data`，且 `evidence_refs` 至少包含 1 个 `evidence_id`

### Requirement: 报告验证器执行结构性 + 引用性检查
The system SHALL 在 Report Verifier 中至少执行以下 10 项检查：
1. 每个章节有 `evidence_refs`
2. 每个数字来自结构化 Evidence
3. 每个图表绑定 Evidence
4. 每个表格绑定 Evidence
5. 引用 ID 真实存在
6. 不包含敏感字段（身份证、手机号、邮箱等）
7. 不包含未授权数据
8. 内容语言一致
9. 章节不重复
10. 未超 max_sections / max_charts / max_tables 限制

#### Scenario: 验证失败时阻止导出
- **WHEN** 任意检查不通过
- **THEN** Verifier 返回 `passed=False` + `issues[]`，Exporter 拒绝生成最终文件，仅返回 `ReportToolOutput.summary` 给用户

### Requirement: 支持 Markdown 和 HTML 两种导出格式（阶段一）
The system SHALL 在阶段一优先支持 Markdown（字符串模板）和 HTML（Jinja2 渲染）两种格式，其他格式（Excel/PDF/PPTX）作为阶段二/三/四扩展。

#### Scenario: 导出 Markdown 报告
- **WHEN** `format="markdown"`
- **THEN** Exporter 将 sections/charts/tables 拼接为 Markdown 文本，写入 Artifact Storage

#### Scenario: 导出 HTML 报告
- **WHEN** `format="html"`
- **THEN** Exporter 使用 Jinja2 模板渲染，HTML 中用户内容**必须转义**，禁止外部脚本

### Requirement: 报告文件租户隔离 + 下载链接鉴权
The system SHALL 保证报告文件路径包含 `tenant_id` 和 `report_id`，下载链接短时有效，且不允许跨租户访问。

#### Scenario: 跨租户下载被拒绝
- **WHEN** 用户 A 尝试用 `report_id` 下载用户 B 的报告
- **THEN** 存储层返回 403 / 拒绝访问，且路径校验阻止路径穿越

### Requirement: 敏感信息扫描与脱敏
The system SHALL 在导出前扫描报告内容，识别身份证号、手机号、邮箱、API Key、密码等敏感信息，**高风险信息直接拒绝导出**。

#### Scenario: 检测到身份证号
- **WHEN** Verifier 扫描发现章节中包含 18 位身份证号
- **THEN** 拒绝导出，返回 `error_code="REPORT_VERIFICATION_FAILED"` + `issues[]` 详细位置

### Requirement: 报告生成结果进入 LangGraph 主干
The system SHALL 保证 ReportTool 的 `ReportArtifact` 通过 `ResultAggregator` 写回 `state.report_artifacts`，并由 `final_answer` 节点识别为 `type="report_generated"` 类型响应。

#### Scenario: 最终答案包含下载链接
- **WHEN** 报告成功生成
- **THEN** final_answer 返回 `type: "report_generated"` + `download_url` + `summary` + `verification.passed`

## MODIFIED Requirements
无（本设计不修改现有 ReAct / Plan Executor / Evidence 模块的核心行为，仅扩展 PlanStep.tool 字面量和 Evidence.source_type 字面量，向后兼容）。

## REMOVED Requirements
无（本设计为纯增量实现）。
