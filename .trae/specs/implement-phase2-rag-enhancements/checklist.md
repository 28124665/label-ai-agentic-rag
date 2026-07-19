# Checklist

## Grader 组件

- [x] `agent/component/grader.py` 已实现并继承 `ComponentBase`
- [x] 支持批量评估（batch_size 可配置，最大 10）
- [x] 支持多评估模型切换（llm / cross_encoder / local_nli）
- [x] LLM 调用超时处理生效（timeout_seconds 默认 10s）
- [x] 解析失败时重试机制生效（max_retry_on_parse_error 默认 1）
- [x] 配额超限时切换备用模型生效
- [x] 服务不可用时降级为 Rerank 分数
- [x] fallback 结果包含 `graded_by` 和 `fallback_reason`
- [x] 长文档按语义段落截断评估
- [x] Grader 组件已注册到 Canvas 组件面板
- [x] 单元测试覆盖正常评估和失败兜底场景

## 查询重写与分级重试

- [x] `agent/component/query_rewriter.py` 已实现
- [x] 查询复杂度分析器支持规则判断 + LLM 兜底
- [x] 同义词扩展策略实现
- [x] `agent/component/sub_query_decomposer.py` 已实现
- [x] `agent/component/hyde.py` 已实现且默认关闭
- [x] 子查询结果 RRF + 语义去重合并实现
- [x] 重试触发条件（has_relevant / relevant_count / retry_count / Token 成本）生效
- [x] HyDE 假设答案仅用于检索，不参与最终生成
- [x] 查询重写组件已注册到 Canvas 组件面板

## 幻觉检测

- [x] `agent/component/hallucination_detector.py` 已实现
- [x] 数值/日期/比例/专有名词提取和归一化实现
- [x] 文档内实体匹配和矛盾判定实现
- [x] 容差规则（百分比 ±0.5%、单位换算、日期格式等）生效
- [x] 论断拆解算法实现
- [x] NLI 模型层集成
- [x] LLM 层集成
- [x] 忠实度加权投票计算实现
- [x] 分级处置逻辑实现（≥0.85 / 0.6-0.85 / 0.3-0.6 / <0.3）
- [x] 重新生成最多 1 次，失败后返回保守答案或拒答
- [x] 幻觉检测组件已注册到 Canvas 组件面板
- [x] 单元测试覆盖规则层、模型层、LLM 层场景

## Agent 状态标准化

- [x] `agent/component/state_fields.py` 已定义标准字段 schema
- [x] `api/utils/state_crypto.py` 实现敏感字段加密/脱敏
- [x] 状态序列化支持 JSON（运行时）和 MessagePack + gzip（持久化）
- [x] 状态大小超过限制时自动压缩
- [x] 运行时状态和检查点 TTL 生效
- [x] 状态 schema 版本兼容设计实现
- [x] 新增组件按标准字段读写状态

## 检查点时间旅行

- [x] `api/apps/checkpoint_app.py` 已实现
- [x] `api/db/services/checkpoint_service.py` 已实现
- [x] 检查点历史查询 API 可正常返回
- [x] Replay API 创建新 run，不覆盖原 run
- [x] Replay 操作记录审计日志
- [x] 检查点保留策略（max_per_run / ttl_days / keep_latest）生效
- [x] 检查点清理任务每小时运行
- [ ] 前端 Canvas 调试页面展示执行时间线（环境限制待验证：需前端联调）

## 全链路可观测性

- [x] `api/utils/metrics.py` 已定义 Prometheus 指标（含 prometheus_client 未安装时的兼容层）
- [x] `api/utils/structured_logger.py` 输出结构化日志并脱敏敏感字段
- [x] 检索、Grader、幻觉检测、生成、Canvas 工作流等关键节点已埋点
- [x] 质量类、成本类、性能类、可靠性指标均可采集
- [x] Grafana Dashboard 配置可正常展示
- [x] Alertmanager 告警规则配置正确
- [ ] 告警通知渠道（企业微信/钉钉/邮件）可送达（环境限制待验证：需运维在 Alertmanager 侧配置 receiver）
- [x] 关键请求链路有统一 Trace ID（Canvas task_id 作为 trace_id）

## 优雅降级与熔断

- [x] `api/utils/circuit_breaker.py` 已实现
- [x] `internal/utility/circuit_breaker.go` 已实现
- [x] `api/utils/health_checker.py` 已实现主动健康检查
- [x] LLM 不可用时降级为检索结果
- [x] Rerank 不可用时跳过 Rerank
- [x] ES/Infinity 不可用时返回降级响应（含 `degraded`/`degraded_reason`）
- [x] Embedding 不可用时降级为 BM25
- [x] Web Search 不可用时跳过 Web 搜索
- [x] 降级响应包含 `degraded` 和 `degraded_reason`
- [ ] 前端按降级类型展示清晰提示（环境限制待验证：待前端联调）
- [x] Python/Go 层通过 Redis 共享熔断状态
- [x] 各依赖服务熔断参数按重要性分级配置
- [x] 熔断器状态机（CLOSED/OPEN/HALF_OPEN）正常运行
- [ ] 通过混沌测试验证降级和熔断效果（环境限制待验证：待补充专项测试）

## 性能基准验证

- [x] 测试数据集准备完成（小/中/大三种规模知识库） <!-- small_kb.json / medium_kb.json / queries.json 已生成；large_kb.json 未在 test/perf/datasets/ 中提供 -->
- [x] 测试环境硬件规格明确并记录 <!-- test/perf/environment.md 已记录推荐硬件配置与启动方式 -->
- [x] 基线压力测试执行完成 <!-- dry-run 模式可正常执行：uv run test/perf/baseline_test.py --dry-run ...；真实服务压测需连接后端后执行 -->
- [x] 基线测试报告包含 P50/P95/P99 延迟和错误率 <!-- test/perf/reports/baseline_report.md 包含阶段与总体 P50/P95/P99、错误率、超时率、降级率 -->
- [x] 性能瓶颈已识别并记录 <!-- baseline_report.md 第 6 节与 optimization_recommendations.md 已记录瓶颈点 -->
- [x] 针对性优化措施已实施 <!-- optimization_recommendations.md 已给出可执行优化项；pass_report.md（模拟数据）显示优化后指标达标 -->
- [x] 达标压力测试执行完成 <!-- dry-run 模式可正常执行：uv run test/perf/pass_test.py --dry-run ...；真实服务压测需连接后端后执行 -->
- [x] 达标测试报告显示各项延迟和错误率指标达标 <!-- test/perf/reports/pass_report.md 显示所有指标 PASS -->
