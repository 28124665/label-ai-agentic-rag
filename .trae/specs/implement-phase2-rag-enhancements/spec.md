# 二期 RAG 能力增强 Spec

## Why

当前项目基于 RAGFlow 已具备基础的 Agent 编排、检索、生成能力，但缺少检索结果评估、查询重写、幻觉检测等关键闭环能力。二期需求旨在补齐这些能力，提升回答质量、可观测性和生产稳定性。

## What Changes

- 新增 Grader Agent 组件，用于评估检索结果相关性
- 新增查询重写组件（同义词扩展、子查询拆解、HyDE）
- 新增幻觉检测组件，实现事实一致性验证
- 标准化 Agent 共享状态字段，支持新组件间数据传递
- 扩展检查点机制，支持时间旅行调试
- 接入 Prometheus/Grafana，建立业务指标和告警体系
- 实现优雅降级与熔断机制，提升依赖服务异常时的可用性
- 完成性能基线测试与达标验证

## Impact

- 受影响模块：`agent/component`、`api/apps`、`api/utils`、`rag/llm`、`rag/nlp`、`internal/utility`
- 受影响流程：Agent Canvas 工作流执行、检索增强生成链路、运维监控
- 新增文件较多，需与 RAGFlow 原有组件注册机制保持一致

## ADDED Requirements

### Requirement: 检索结果相关性评估（Grader）

系统 SHALL 提供独立的 Grader Agent 组件，对用户查询和候选文档进行相关性评估。

#### Scenario: 正常评估
- **WHEN** Grader 组件接收到 query 和 retrieved_docs
- **THEN** 返回每个文档的 relevance（relevant/not_relevant）和 score

#### Scenario: LLM 超时/失败兜底
- **WHEN** LLM 调用超时、配额超限、解析失败或服务不可用
- **THEN** 降级使用 Rerank 分数完成评估，并记录 fallback_reason

### Requirement: 查询重写与分级重试

系统 SHALL 根据查询复杂度动态选择重写策略，并在检索结果不相关时触发重试。

#### Scenario: 查询复杂度判断
- **WHEN** 检索结果未通过 Grader 评估
- **THEN** 根据 query 特征判断复杂度，选择 synonym_rewrite / sub_query_decompose / hyde 策略

#### Scenario: 子查询结果合并
- **WHEN** 子查询拆解后分别检索
- **THEN** 使用 RRF + 语义去重合并结果，并标注来源

#### Scenario: 成本控制
- **WHEN** 重试阶段累计 Token 超过 max_retry_tokens
- **THEN** 停止重试，进入 Web 搜索兜底或直接拒答

### Requirement: 幻觉检测与忠实度验证

系统 SHALL 在答案生成后验证答案是否被检索文档支撑。

#### Scenario: 规则层精确校验
- **WHEN** 答案包含数值、日期、比例等可精确验证实体
- **THEN** 优先使用规则层匹配，判定 SUPPORTED / NOT_SUPPORTED / CONTRADICTED

#### Scenario: 多层投票
- **WHEN** 规则层无法覆盖的复杂论断
- **THEN** 综合 NLI 模型和 LLM 判断，计算忠实度分数

#### Scenario: 幻觉处置
- **WHEN** 忠实度分数 < 0.85
- **THEN** 根据分数区间返回保守答案、重生成或标准拒答

### Requirement: Agent 状态标准化

系统 SHALL 定义标准化的 Agent 共享状态字段，支持新组件读写和持久化。

#### Scenario: 状态读写
- **WHEN** 组件执行时
- **THEN** 按标准字段名读取输入、写入输出

#### Scenario: 状态安全
- **WHEN** 状态持久化到 Redis/DB
- **THEN** 敏感字段加密或脱敏，超过大小限制时压缩

### Requirement: 检查点时间旅行

系统 SHALL 支持查看历史检查点并回溯执行。

#### Scenario: 检查点查询
- **WHEN** 调用检查点历史查询 API
- **THEN** 返回指定 run 的检查点列表

#### Scenario: Replay
- **WHEN** 从指定检查点 Replay
- **THEN** 创建新 run，不覆盖原 run，并记录审计日志

### Requirement: 全链路可观测性

系统 SHALL 暴露 Prometheus 指标、结构化日志和 Grafana Dashboard。

#### Scenario: 指标采集
- **WHEN** 请求经过检索、重试、生成、幻觉检测等节点
- **THEN** 记录延迟、命中率、成本、错误率等指标

#### Scenario: 告警
- **WHEN** 指标超过阈值
- **THEN** 通过企业微信/钉钉/邮件通知

### Requirement: 优雅降级与熔断

系统 SHALL 在依赖服务异常时降级返回，并通过熔断器防止持续重试。

#### Scenario: 服务降级
- **WHEN** LLM/Rerank/ES/Embedding/Web Search 不可用
- **THEN** 按预定策略降级，并返回清晰的降级提示

#### Scenario: 熔断恢复
- **WHEN** 依赖服务连续失败达到阈值
- **THEN** 熔断器 OPEN，一段时间后进入 HALF_OPEN 探测

### Requirement: 性能基准验证

系统 SHALL 完成压力测试，验证延迟和吞吐量达标。

#### Scenario: 基线测试
- **WHEN** 在指定硬件配置下运行压力测试
- **THEN** 输出 P50/P95/P99 延迟分布和错误率

## MODIFIED Requirements

### Requirement: Agent 组件注册

原有组件注册机制保持不变，新增组件通过 `agent/component/__init__.py` 注册到 Canvas 组件面板。

## REMOVED Requirements

无。
