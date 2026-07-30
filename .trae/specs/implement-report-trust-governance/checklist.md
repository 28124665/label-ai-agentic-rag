# Checklist

## EvidenceProvenance 血缘模型
- [x] agent/langgraph/evidence/provenance.py 包含 EvidenceProvenance TypedDict（DB / RAG / 通用字段）
- [x] DB Evidence 的 metadata.provenance 含 db_id / table_name / query_id / row_keys
- [x] RAG Evidence 的 metadata.provenance 含 kb_id / doc_id / chunk_id / doc_title
- [x] extract_provenance_summary() 工具函数可用

## Claim 级血缘
- [x] agent/langgraph/tools/report/claims.py 包含 Claim / ClaimType / SupportStatus
- [x] ClaimBuilder 生成 Claim 并双向绑定 section.claim_refs / chart.claim_refs / table.claim_refs
- [x] metric/comparison/trend Claim 无 evidence 直接删除（不进入 artifact）
- [x] fact Claim 无 evidence 默认删除
- [x] recommendation Claim 无 evidence 保留并标记 unsupported
- [x] 导出时显示"（未经验证）"标记

## DataSourceRef 与 source_summary
- [x] agent/langgraph/tools/report/data_sources.py 包含 DataSourceRef TypedDict
- [x] DataSourceCollector 收集所有 evidence 来源并标注 used_by_sections / charts / tables / claims
- [x] build_source_summary() 输出人类可读 summary（label / source / query_id / used_for）
- [x] ReportArtifact.data_sources / source_summary 字段填充正确

## Verifier 扩展
- [x] agent/langgraph/tools/report/verifier.py 增加 _check_claim_lineage() 方法
- [x] 校验 metric/comparison/trend / fact Claim.evidence_refs ≥ 1
- [x] recommendation Claim 无 evidence 标记 unsupported（不阻断导出）
- [x] 低置信度（confidence < low_confidence_threshold）Claim 标记 needs_human_review=True
- [x] review_reason 写入 Verifier issues
- [x] 现有 10 项检查逻辑不破坏
- [x] 检查 1 调整：recommendation 章节允许无 evidence_refs（仅记 warning，不阻断验证）

## Exporter 扩展
- [x] Markdown 导出末尾追加 "## 数据来源" 区块
- [x] HTML 导出底部追加可折叠 `<details>` 面板
- [x] "未经验证" / "需人工核实" 标记在导出文本中展示
- [x] HTML 标签转义不破坏

## HumanReview 节点
- [x] agent/langgraph/tools/report/human_review.py 包含 HumanReviewNode 类
- [x] HumanReviewResult TypedDict 含 action / reviewer_id / approved / publish_allowed
- [x] publish_policy.require_human_review=True 强制进入 HumanReview
- [x] 高敏 Skill 类型（财报/成本/质量/EHS/人事）默认进入 HumanReview
- [x] 低置信 Claim 超阈值强制进入 HumanReview
- [x] approve → publish_status="approved" + download_url
- [x] reject → publish_status="rejected"
- [x] 未审核报告 publish_status="draft"，不生成正式下载链接

## ReportTool 主流程串联
- [x] agent/langgraph/tools/report/models.py ReportArtifact 增加 claims / data_sources / source_summary / human_review_result / publish_status
- [x] agent/langgraph/tools/report/report_tool.py invoke 流程串联 Claim Builder → DataSourceCollector → Verifier(含 Lineage) → HumanReview Gate → Exporter
- [x] publish_status 转换：draft → pending_review → approved → published
- [x] error_code 增加 REPORT_PUBLISH_PENDING / REPORT_REJECTED

## Skill 治理
- [x] agent/langgraph/skills/governance.py 包含 SkillGovernance TypedDict
- [x] 状态机校验：draft → pending_review → approved → active → deprecated
- [x] deprecated Skill 不被 Resolver 选择
- [x] PermissionIntersection 计算 user_permissions ∩ skill_required_permissions
- [x] 权限交集不等于 Skill 要求全集时 Skill 不可用
- [x] 变更审计 change_log 字段

## 用户反馈闭环
- [x] agent/langgraph/feedback/collector.py 包含 ReportFeedback TypedDict
- [x] FeedbackCollector.collect() 记录反馈
- [x] FeedbackCollector.summarize_optimization_suggestions() 生成 Skill 优化建议

## 主干集成
- [x] agent/langgraph/state.py 增加 report_claims / report_data_sources / report_human_review / report_publish_status
- [x] agent/langgraph/nodes/final_answer.py 报告类型响应包含 publish_status / needs_human_review
- [x] agent/langgraph/tools/report/storage.py artifact 元数据增加 claims / data_sources / publish_status

## 配置
- [x] config/report_governance.yaml 包含 human_review / governance / lineage / claim_rules 配置

## 测试
- [x] test/agent/langgraph/evidence/test_provenance.py ≥ 4 个用例全部通过 (13 个用例)
- [x] test/agent/langgraph/tools/report/test_claims.py ≥ 6 个用例全部通过 (14 个用例)
- [x] test/agent/langgraph/tools/report/test_data_sources.py ≥ 4 个用例全部通过 (10 个用例)
- [x] test/agent/langgraph/tools/report/test_verifier_lineage.py ≥ 5 个用例全部通过 (8 个用例)
- [x] test/agent/langgraph/tools/report/test_human_review.py ≥ 5 个用例全部通过 (22 个用例)
- [x] test/agent/langgraph/skills/test_governance.py ≥ 4 个用例全部通过 (30 个用例)
- [x] test/agent/langgraph/feedback/test_collector.py ≥ 3 个用例全部通过 (11 个用例)
- [x] test/agent/langgraph/tools/report/test_integration_governance.py ≥ 3 个用例全部通过 (6 个用例)
- [x] 现有所有 ReportTool 测试不破坏

# 实施总结

## 完成度
- 任务总数：9
- 已完成：9
- 进度：100%
- 关联测试用例：112+ 个（全部通过）

## 新增模块
| 模块 | 文件 | 核心职责 |
|------|------|---------|
| EvidenceProvenance | `agent/langgraph/evidence/provenance.py` | DB/RAG 血缘统一模型 + 写入/摘要辅助 |
| Claim Builder | `agent/langgraph/tools/report/claims.py` | Claim 数据结构 + 自动构建 + 未验证过滤 |
| DataSource Collector | `agent/langgraph/tools/report/data_sources.py` | DataSourceRef 收集 + 双向引用 + 人类可读摘要 |
| Verifier 扩展 | `agent/langgraph/tools/report/verifier.py` | _check_claim_lineage() + recommendation 兼容 |
| Exporter 扩展 | `agent/langgraph/tools/report/exporter.py` | Markdown/HTML 数据来源区块 + 验证标记 |
| HumanReview 节点 | `agent/langgraph/tools/report/human_review.py` | 触发规则 + 状态机转换 + 高敏判定 |
| Skill 治理 | `agent/langgraph/skills/governance.py` | 生命周期状态机 + 权限交集 + 审计 |
| 反馈采集器 | `agent/langgraph/feedback/collector.py` | ReportFeedback + 优化建议生成 |
| 配置 | `config/report_governance.yaml` | human_review / governance / lineage / claim_rules |

## 关键设计落地
1. **Claim 级血缘**：每条 Claim 都可通过 `evidence_refs → evidence → metadata.provenance` 追溯到 db_id/table_name/query_id 或 kb_id/doc_id/chunk_id，实现"Claim → Evidence → query_id/chunk_id → 表/文档"四级追溯链。
2. **人机协同**：高敏 Skill 类型 + 低置信度超阈值 → publish_status=pending_review；reviewer 可执行 approve/reject/edit/comment/request_regenerate 五种动作；草稿/待审核/驳回状态下 download_url 置空。
3. **Skill 治理**：状态机 draft → pending_review → approved → active → deprecated 严格校验；SkillResolver 仅选择 active 或租户放开的 approved；权限交集不等于 Skill 要求全集时 Skill 不可用。
4. **反馈闭环**：FeedbackCollector 记录 like/dislike/edit/comment/regenerate 五种反馈，按 dislike/edit/regenerate 阈值聚合为 Skill 优化建议，按 (target_type, target_id, score) 排序输出。
5. **未验证标记**：metric/comparison/trend/fact Claim 无 evidence 直接删除；recommendation 无 evidence 保留并标 unsupported，导出时显示"（未经验证）"；低置信度 Claim 在导出时显示"（需人工核实）"。

## 与现有流程有机融合
- **不破坏现有 10 项检查**：Verifier 调整为"检查 1 对 recommendation 章节宽容 + 检查 13 追加 Claim Lineage Check"，原有测试全部通过。
- **ReAct 子图兼容**：ReAct plan 触发 ReportTool 时，HumanReview 仍按 report_type 触发高敏审核；evidence 已在 ReAct 中标准化为含 provenance 的 Evidence。
- **Skill 治理与 resolver 解耦**：通过 SkillGovernance 模块独立维护 status/permissions 字段，SkillResolver 调用 `is_skill_usable()` 综合判定。
- **SSE 事件流扩展**：final_answer 节点透传 report_publish_status / needs_human_review / claim_count / data_source_count 字段，前端可展示审核状态与血缘数。

## 后续可扩展
1. **审核工作台 UI**：当前仅提供后端逻辑，待前端 HumanReviewPanel 页面接入。
2. **Skill 治理后端存储**：SkillGovernance 元数据目前仅在内存，落地需 SkillRegistry 持久化与版本管理 API。
3. **敏感信息去标识化**：当前 SensitiveScanner 仅做检测与阻断，待补充自动脱敏（mask / hash）。
4. **跨报告血缘溯源**：当前血缘仅在单报告内闭环，多报告间相同 db_id 的数据一致性校验待补。
5. **可解释性报告导出**：当前 Exporter 仅展示数据来源 + 未验证标记，结构化解释（"为什么这条 Claim 被标为需审核"）待 LLM 生成。
