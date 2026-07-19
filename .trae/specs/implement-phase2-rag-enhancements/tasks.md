# Tasks

## Sprint 1：状态标准化 + Grader 组件

- [x] Task 1: 定义 Agent 标准状态字段
  - [x] SubTask 1.1: 创建 `agent/component/state_fields.py`，定义标准字段 schema
  - [x] SubTask 1.2: 创建 `api/utils/state_crypto.py`，实现敏感字段加密/脱敏
  - [x] SubTask 1.3: 在 Canvas 执行流程中集成状态序列化、大小限制、TTL

- [x] Task 2: 实现 Grader Agent 组件
  - [x] SubTask 2.1: 创建 `agent/component/grader.py`，支持批量评估和多评估模型
  - [x] SubTask 2.2: 实现 LLM 调用超时、解析失败重试、配额超限切换备用模型、Rerank fallback
  - [x] SubTask 2.3: 在 `agent/component/__init__.py` 注册 Grader 组件
  - [x] SubTask 2.4: 编写 Grader 单元测试

## Sprint 2：查询重写与分级重试

- [x] Task 3: 实现查询复杂度分析器
  - [x] SubTask 3.1: 在 `agent/component/query_rewriter.py` 中实现基于规则的复杂度判断
  - [x] SubTask 3.2: 集成轻量级 LLM 兜底判断

- [x] Task 4: 实现查询重写组件
  - [x] SubTask 4.1: 实现同义词扩展策略
  - [x] SubTask 4.2: 实现子查询拆解组件 `agent/component/sub_query_decomposer.py`
  - [x] SubTask 4.3: 实现 HyDE 组件 `agent/component/hyde.py`（默认关闭）
  - [x] SubTask 4.4: 在 `agent/component/__init__.py` 注册组件
  - [x] SubTask 4.5: 实现子查询结果 RRF + 语义去重合并
  - [x] SubTask 4.6: 实现重试触发条件和 Token 成本上限控制

## Sprint 3：幻觉检测与忠实度验证

- [x] Task 5: 实现规则层精确校验
  - [x] SubTask 5.1: 实现数值、日期、比例、专有名词提取和归一化
  - [x] SubTask 5.2: 实现文档内实体匹配和矛盾判定
  - [x] SubTask 5.3: 定义容差规则（百分比 ±0.5%、单位换算等）

- [x] Task 6: 实现幻觉检测组件
  - [x] SubTask 6.1: 创建 `agent/component/hallucination_detector.py`
  - [x] SubTask 6.2: 实现论断拆解算法
  - [x] SubTask 6.3: 集成 NLI 模型层和 LLM 层
  - [x] SubTask 6.4: 实现忠实度分数加权投票和分级处置逻辑
  - [x] SubTask 6.5: 在 `agent/component/__init__.py` 注册组件
  - [x] SubTask 6.6: 编写幻觉检测单元测试

## Sprint 4：降级熔断 + 性能基线

- [x] Task 7: 实现优雅降级与熔断机制
  - [x] SubTask 7.1: 创建 `api/utils/circuit_breaker.py`（Python 层）
  - [x] SubTask 7.2: 创建 `internal/utility/circuit_breaker.go`（Go 层）
  - [x] SubTask 7.3: 创建 `api/utils/health_checker.py`，实现主动健康检查
  - [x] SubTask 7.4: 在 LLM、Rerank、ES、Embedding、Web Search 调用处集成降级逻辑
  - [x] SubTask 7.5: 实现统一降级响应格式和前端展示
  - [x] SubTask 7.6: 通过 Redis 共享 Python/Go 熔断状态

- [x] Task 8: 性能基线测试
  - [x] SubTask 8.1: 准备测试数据集和测试环境
  - [x] SubTask 8.2: 使用 Locust/wrk 执行基线压力测试
  - [x] SubTask 8.3: 输出基线测试报告（P50/P95/P99、错误率）

## Sprint 5：可观测性 + 检查点时间旅行 + 性能达标

- [x] Task 9: 实现全链路可观测性
  - [x] SubTask 9.1: 创建 `api/utils/metrics.py`，定义 Prometheus 指标
  - [x] SubTask 9.2: 创建 `api/utils/structured_logger.py`，输出结构化日志
  - [x] SubTask 9.3: 在关键节点埋点（检索、Rerank、Grader、重写、幻觉检测、生成）
  - [x] SubTask 9.4: 创建 Grafana Dashboard 配置
  - [x] SubTask 9.5: 创建 Alertmanager 告警规则

- [x] Task 10: 实现检查点时间旅行
  - [x] SubTask 10.1: 创建 `api/apps/checkpoint_app.py` 和 `api/db/services/checkpoint_service.py`
  - [x] SubTask 10.2: 实现检查点历史查询 API
  - [x] SubTask 10.3: 实现 Replay API（fork 新 run + 审计日志）
  - [x] SubTask 10.4: 实现检查点保留策略和清理任务
  - [x] SubTask 10.5: 前端 Canvas 调试页面增加时间线展示

- [x] Task 11: 性能达标测试
  - [x] SubTask 11.1: 根据基线结果进行针对性优化
  - [x] SubTask 11.2: 执行达标压力测试
  - [x] SubTask 11.3: 输出达标测试报告

# Task Dependencies

- Task 2 依赖 Task 1
- Task 4 依赖 Task 2
- Task 6 依赖 Task 1、Task 5
- Task 7 可并行于 Task 1-6
- Task 8 依赖 Task 1-4 完成基础链路
- Task 9 依赖 Task 1-6 完成关键节点定义
- Task 10 依赖 Task 1
- Task 11 依赖 Task 8
