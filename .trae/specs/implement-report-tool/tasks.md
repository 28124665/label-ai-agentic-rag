# Tasks

- [x] Task 1: 创建 ReportTool 数据模型与配置
  - [x] SubTask 1.1: 创建 agent/langgraph/tools/report/models.py（ReportToolInput/Output/Artifact/Section/ChartSpec/TableSpec/VerificationResult 数据结构）
  - [x] SubTask 1.2: 创建 agent/langgraph/tools/report/__init__.py
  - [x] SubTask 1.3: 创建 config/report.yaml（enabled / allowed_formats / max_sections / safety 等）
  - [x] SubTask 1.4: 创建 config_loader 加载 report 配置

- [x] Task 2: 创建 Report Planner 章节规划器
  - [x] SubTask 2.1: 创建 agent/langgraph/tools/report/planner.py，根据 report_type 和 evidence 生成 Section 计划
  - [x] SubTask 2.2: 实现 quality_analysis / business_analysis / trend_analysis / knowledge_summary 等基础模板的章节拆分

- [x] Task 3: 创建 Section / Chart / Table 生成器
  - [x] SubTask 3.1: 创建 agent/langgraph/tools/report/generator.py（Section Generator，受 evidence 约束生成章节内容）
  - [x] SubTask 3.2: 创建 agent/langgraph/tools/report/chart_builder.py（从 DB Evidence structured_data 生成 bar/line/pie/table/kpi 图表）
  - [x] SubTask 3.3: 创建 agent/langgraph/tools/report/table_builder.py（从 DB Evidence 生成 TableSpec）

- [x] Task 4: 创建 Report Verifier
  - [x] SubTask 4.1: 创建 agent/langgraph/tools/report/verifier.py
  - [x] SubTask 4.2: 实现 10 项检查（数字来源、引用绑定、敏感信息、语言一致性、章节重复、数量限制）

- [x] Task 5: 创建 Sensitive Scanner
  - [x] SubTask 5.1: 创建 agent/langgraph/tools/report/sensitive_scanner.py
  - [x] SubTask 5.2: 识别身份证号、手机号、邮箱、API Key、密码、数据库连接串、内网地址

- [x] Task 6: 创建 Report Exporter 与 Artifact Storage
  - [x] SubTask 6.1: 创建 agent/langgraph/tools/report/exporter.py（Markdown + HTML 导出）
  - [x] SubTask 6.2: 创建 agent/langgraph/tools/report/storage.py（本地文件存储，路径含 tenant_id）
  - [x] SubTask 6.3: 创建 agent/langgraph/tools/report/templates.py（内置模板 + Jinja2 渲染）

- [x] Task 7: 创建 ReportTool 主入口
  - [x] SubTask 7.1: 创建 agent/langgraph/tools/report/report_tool.py
  - [x] SubTask 7.2: 串联 Input Validator → Evidence Readiness Check → Planner → Generators → Verifier → Exporter → Storage
  - [x] SubTask 7.3: 实现错误码体系（REPORT_INPUT_INVALID / REPORT_EVIDENCE_INSUFFICIENT / REPORT_VERIFICATION_FAILED 等）

- [x] Task 8: 集成到 LangGraph 主干
  - [x] SubTask 8.1: 扩展 agent/langgraph/routers/models.py — PlanStep.tool Literal 增加 "report"
  - [x] SubTask 8.2: 扩展 agent/langgraph/executor/tool_dispatcher.py — 新增 _execute_report() 分发
  - [x] SubTask 8.3: 扩展 agent/langgraph/executor/result_aggregator.py — 聚合 report_artifacts / report_summary
  - [x] SubTask 8.4: 扩展 agent/langgraph/state.py — 新增 report_artifacts / report_summary / report_quality_score / report_error 字段
  - [x] SubTask 8.5: 扩展 agent/langgraph/evidence/models.py — Evidence.source_type Literal 增加 "report"
  - [x] SubTask 8.6: 扩展 agent/langgraph/nodes/final_answer.py — 支持 report_generated / report_failed 类型最终答案

- [x] Task 9: 编写单元测试
  - [x] SubTask 9.1: Report Planner 测试（章节拆分 / 模板选择 / 缺失 evidence 警告）
  - [x] SubTask 9.2: Chart/Table Builder 测试（从 DB Evidence 提取数据 / 绑定 refs）
  - [x] SubTask 9.3: Verifier 测试（10 项检查 / 失败场景）
  - [x] SubTask 9.4: Sensitive Scanner 测试（身份证 / 手机号 / 邮箱 / API Key 检测）
  - [x] SubTask 9.5: Exporter 测试（Markdown / HTML 生成 / HTML 转义）
  - [x] SubTask 9.6: Storage 测试（路径租户隔离 / 路径穿越防护）
  - [x] SubTask 9.7: ReportTool 集成测试（完整流程 / 错误码 / 降级）
  - [x] SubTask 9.8: ToolDispatcher + PlanStep 集成测试（tool="report" 步骤分发）

# Task Dependencies
- Task 1 独立，无依赖
- Task 2 depends on Task 1
- Task 3 depends on Task 1, Task 2
- Task 4 depends on Task 3
- Task 5 独立
- Task 6 depends on Task 1
- Task 7 depends on Task 2, Task 3, Task 4, Task 5, Task 6
- Task 8 depends on Task 7
- Task 9 depends on Task 1-8

# 实施总结
- 全部 9 个任务（31 个子任务）已完成
- 单元测试：91 个用例全部通过
- 模块文件清单：
  - agent/langgraph/tools/report/__init__.py
  - agent/langgraph/tools/report/models.py
  - agent/langgraph/tools/report/planner.py
  - agent/langgraph/tools/report/templates.py
  - agent/langgraph/tools/report/generator.py
  - agent/langgraph/tools/report/chart_builder.py
  - agent/langgraph/tools/report/table_builder.py
  - agent/langgraph/tools/report/verifier.py
  - agent/langgraph/tools/report/sensitive_scanner.py
  - agent/langgraph/tools/report/exporter.py
  - agent/langgraph/tools/report/storage.py
  - agent/langgraph/tools/report/report_tool.py
- 集成点：
  - agent/langgraph/routers/models.py（PlanStep.tool）
  - agent/langgraph/executor/tool_dispatcher.py（_execute_report）
  - agent/langgraph/executor/result_aggregator.py（report_* 字段聚合）
  - agent/langgraph/state.py（report_* 状态字段）
  - agent/langgraph/evidence/models.py（source_type 增加 "report"）
  - agent/langgraph/nodes/final_answer.py（report_generated / report_failed）
- 配置文件：config/report.yaml
- 测试文件：test/agent/langgraph/tools/report/*（8 个测试文件）
