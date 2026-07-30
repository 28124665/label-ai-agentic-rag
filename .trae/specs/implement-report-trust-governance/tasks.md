# Tasks

- [x] Task 1: 创建 EvidenceProvenance 统一血缘模型
  - [x] SubTask 1.1: 创建 agent/langgraph/evidence/provenance.py — EvidenceProvenance TypedDict（DB / RAG / 通用字段）
  - [x] SubTask 1.2: 在 provenance.py 内集成 `attach_provenance_to_evidence()` / `extract_provenance_summary()` 辅助函数（合并为单文件）
  - [x] SubTask 1.3: 扩展 agent/langgraph/evidence/models.py — 在 EvidenceNormalizer 流程中接入 provenance 写入（normalize_db_evidence / normalize_rag_evidence 写入 metadata.provenance）

- [x] Task 2: 创建 Claim 数据模型与 Claim Builder
  - [x] SubTask 2.1: 创建 agent/langgraph/tools/report/claims.py — Claim / ClaimType / SupportStatus TypedDict
  - [x] SubTask 2.2: 实现 `ClaimBuilder` 类：接收 sections / charts / tables 与 evidence 列表，输出 claims 列表 + 双向绑定 (section.claim_refs / chart.claim_refs / table.claim_refs)
  - [x] SubTask 2.3: 实现"未验证 Claim 标记与过滤"逻辑：metric/comparison/trend 无 evidence 删除，fact 默认删除，recommendation 保留并标记 unsupported

- [x] Task 3: 创建 DataSourceRef 与 source_summary 构造器
  - [x] SubTask 3.1: 创建 agent/langgraph/tools/report/data_sources.py — DataSourceRef TypedDict
  - [x] SubTask 3.2: 实现 `DataSourceCollector` 类：从 evidence 收集所有 DataSourceRef，标注 used_by_sections / used_by_charts / used_by_tables / used_by_claims
  - [x] SubTask 3.3: 实现 `build_source_summary()`：生成人类可读 summary（label / source / query_id / used_for）

- [x] Task 4: 扩展 Verifier 集成 Claim Lineage Check + 低置信度"需人工核实"
  - [x] SubTask 4.1: 扩展 agent/langgraph/tools/report/verifier.py — 增加 `_check_claim_lineage()` 私有方法
  - [x] SubTask 4.2: 实现 Claim.evidence_refs 校验（metric/comparison/trend 必须 ≥ 1，fact ≥ 1，recommendation 允许 0 但标记 unsupported）
  - [x] SubTask 4.3: 实现低置信度标记：confidence < low_confidence_threshold → needs_human_review=True + review_reason
  - [x] SubTask 4.4: 在 verify() 中串联现有 10 项检查 + Claim Lineage Check + 低置信度标记
  - [x] SubTask 4.5: 调整检查 1（section_has_evidence_refs）— recommendation 章节允许无 evidence_refs（仅记 warning，不阻断验证）

- [x] Task 5: 扩展 Exporter 输出"数据来源"区块
  - [x] SubTask 5.1: 扩展 agent/langgraph/tools/report/exporter.py — Markdown 导出末尾追加 `## 数据来源` 区块
  - [x] SubTask 5.2: HTML 导出底部追加 `<details><summary>数据来源</summary>...</details>` 面板
  - [x] SubTask 5.3: 实现"未经验证"/"需人工核实"标记在导出文本中的展示

- [x] Task 6: 创建 HumanReview 节点 + ReportTool 串联
  - [x] SubTask 6.1: 创建 agent/langgraph/tools/report/human_review.py — HumanReviewNode / HumanReviewResult TypedDict
  - [x] SubTask 6.2: 实现 publish_policy 判定：高敏 Skill / require_human_review=True / 低置信 Claim 超阈值 → 强制进入 HumanReview
  - [x] SubTask 6.3: 扩展 agent/langgraph/tools/report/models.py — ReportArtifact 增加 claims / data_sources / source_summary / human_review_result / publish_status 字段
  - [x] SubTask 6.4: 扩展 agent/langgraph/tools/report/report_tool.py — invoke 流程串联 Claim Builder → DataSourceCollector → Verifier(含 Lineage) → HumanReview Gate → Exporter
  - [x] SubTask 6.5: 实现 publish_status 转换（draft / pending_review / approved / published / rejected）

- [x] Task 7: 创建 Skill 治理与用户反馈模块
  - [x] SubTask 7.1: 在 agent/langgraph/skills/governance.py 实现 SkillGovernance TypedDict + 状态机校验
  - [x] SubTask 7.2: 实现 `SkillResolverLifecycleFilter` — 仅选择 status=active 或租户放开的 approved
  - [x] SubTask 7.3: 实现 `PermissionIntersection` — user_permissions ∩ skill_required_permissions 校验
  - [x] SubTask 7.4: 创建 agent/langgraph/feedback/collector.py — ReportFeedback TypedDict + FeedbackCollector 类
  - [x] SubTask 7.5: 实现 `summarize_optimization_suggestions()` — 从反馈生成 Skill 优化建议

- [x] Task 8: 扩展 AgentState 与 final_answer 节点
  - [x] SubTask 8.1: 扩展 agent/langgraph/state.py — 新增 report_claims / report_data_sources / report_human_review / report_publish_status 字段
  - [x] SubTask 8.2: 扩展 agent/langgraph/nodes/final_answer.py — 报告类型响应包含 publish_status / needs_human_review
  - [x] SubTask 8.3: 扩展 agent/langgraph/tools/report/storage.py — artifact 元数据增加 claims / data_sources / publish_status

- [x] Task 9: 配置与单元测试
  - [x] SubTask 9.1: 创建 config/report_governance.yaml — human_review / governance / lineage / claim_rules 配置
  - [x] SubTask 9.2: 创建 test/agent/langgraph/evidence/test_provenance.py — provenance 写入与摘要 ≥ 4 个用例
  - [x] SubTask 9.3: 创建 test/agent/langgraph/tools/report/test_claims.py — Claim Builder / 未验证过滤 ≥ 6 个用例
  - [x] SubTask 9.4: 创建 test/agent/langgraph/tools/report/test_data_sources.py — DataSourceCollector ≥ 4 个用例
  - [x] SubTask 9.5: 创建 test/agent/langgraph/tools/report/test_verifier_lineage.py — Lineage Check + 低置信度 ≥ 5 个用例
  - [x] SubTask 9.6: 创建 test/agent/langgraph/tools/report/test_human_review.py — publish_status 转换 ≥ 5 个用例
  - [x] SubTask 9.7: 创建 test/agent/langgraph/skills/test_governance.py — 状态机 / 权限交集 ≥ 4 个用例
  - [x] SubTask 9.8: 创建 test/agent/langgraph/feedback/test_collector.py — FeedbackCollector ≥ 3 个用例
  - [x] SubTask 9.9: 创建 test/agent/langgraph/tools/report/test_integration_governance.py — ReportTool 完整链路集成 ≥ 3 个用例

# Task Dependencies
- Task 1 独立
- Task 2 depends on Task 1
- Task 3 depends on Task 1
- Task 4 depends on Task 2
- Task 5 depends on Task 3
- Task 6 depends on Task 1-5
- Task 7 独立
- Task 8 depends on Task 6
- Task 9 depends on Task 1-8

# 任务完成度
- 总任务数：9
- 已完成：9
- 进度：100%
