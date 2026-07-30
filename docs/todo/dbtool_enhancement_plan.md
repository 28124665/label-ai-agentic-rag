# DBTool 能力增强 TODO 计划

> **目标**：支撑 ReportSkill / DataSkill / RetrievalSkill 的生产级数据查询与证据生成  
> **背景**：当前 DataSkill 已定义 db_targets、tables、metric_bindings、query_templates、policy_constraints，但现有 DBTool 能力尚不足以完整执行这些计划  
> **优先级原则**：先保证只读安全、结构化查询、指标可追溯，再增强跨库聚合和自然语言查询能力

---

## 1. 当前 DBTool 能力缺口

当前 DBTool 主要支持数据库查询入口，但 DataSkill 需要以下能力：

1. 根据 `db_id` 选择指定数据库实例。
2. 根据 `table_name` 和字段白名单执行结构化查询。
3. 根据 `query_templates` 生成参数化查询。
4. 支持维度、指标、过滤条件、时间粒度的标准化表达。
5. 支持指标绑定 `metric_bindings`。
6. 支持查询结果转 Evidence。
7. 支持表级、字段级、租户级权限校验。
8. 支持敏感字段脱敏。
9. 支持查询审计和追溯。
10. 支持跨库结果聚合，但不允许 DBTool 直接跨库 JOIN。

---

## 2. P0：必须优先实现

### 2.1 多数据库实例选择

**TODO**

- [ ] 支持 `db_id` 到数据库连接配置的映射。
- [ ] 支持 `qms_prod`、`mes_prod`、`oms_prod`、`erp_prod`、`hr_prod`、`scm_prod` 等逻辑实例。
- [ ] 支持按租户隔离连接配置。
- [ ] 未授权 `db_id` 直接拒绝。

**验收标准**

- DataSkill 中的 `db_id` 可以映射到真实连接。
- 未授权租户不能访问其他 `db_id`。

### 2.2 表和字段白名单

**TODO**

- [ ] 支持表级白名单。
- [ ] 支持字段级白名单。
- [ ] 支持 DataSkill `required_fields` 校验。
- [ ] 支持敏感字段标记和脱敏。
- [ ] 查询超出白名单字段时拒绝或裁剪。

**验收标准**

- `quality_exception.customer_id` 可按策略脱敏。
- 未声明字段不能被查询。

### 2.3 参数化结构化查询

**TODO**

- [ ] 定义结构化查询模型 `StructuredQuery`。
- [ ] 支持 `dimensions`、`metrics`、`filters`、`time_range`、`limit`、`order_by`。
- [ ] 禁止字符串拼接用户输入生成 SQL。
- [ ] 所有过滤值参数化绑定。

**验收标准**

- DataSkill 的 `query_templates` 可直接转换为 `StructuredQuery`。
- 用户输入无法注入 SQL。

### 2.4 时间范围和时间粒度

**TODO**

- [ ] 支持 `time_range` 标准结构。
- [ ] 支持 `day/week/month/quarter/year` 粒度。
- [ ] 支持字段映射：不同表的时间字段不同。
- [ ] 支持 `report_date` 单日查询。

**验收标准**

- `production_daily` 可按 `report_date` 查询。
- `quality_report` 可按月聚合异常趋势。

### 2.5 查询结果标准化

**TODO**

- [ ] 返回统一的 `DBQueryResult`。
- [ ] 包含 `columns`、`rows`、`row_count`、`db_id`、`table_name`、`query_id`、`latency_ms`。
- [ ] 保留指标单位、字段类型和来源表。
- [ ] 支持结果转 Evidence。

**验收标准**

- ReportTool 可以直接消费 DBTool 结果生成 Evidence。

---

## 3. P1：报表和指标必需能力

### 3.1 指标绑定执行

**TODO**

- [ ] 支持 `metric_bindings.aggregation`。
- [ ] 支持 `sum/count/avg/min/max/rank`。
- [ ] 支持简单条件聚合，例如 `count(case when delay_days > 0 then order_id end)`。
- [ ] 支持派生指标依赖检查。
- [ ] 禁止执行任意表达式或 `eval`。

**验收标准**

- `exception_rate`、`delivery_on_time_rate`、`cost_variance_rate` 可由基础指标计算。
- 派生指标记录计算依赖和来源。

### 3.2 查询模板执行

**TODO**

- [ ] 支持加载 DataSkill `query_templates`。
- [ ] 根据上下文变量填充模板。
- [ ] 校验必需上下文，如 `tenant_id`、`time_range`、`report_date`。
- [ ] 缺失上下文时返回类型化错误。

**验收标准**

- 每个 DataSkill 至少能执行一个核心查询模板。
- 上下文缺失不会生成错误 SQL。

### 3.3 聚合与分组

**TODO**

- [ ] 支持 `group by` 维度。
- [ ] 支持多维度聚合。
- [ ] 支持按时间字段分组。
- [ ] 支持排序和 TopN。

**验收标准**

- `top_exception_types`、`delay_reason_distribution`、`cost_driver_ranking` 可以生成。

### 3.4 查询证据生成

**TODO**

- [ ] DBTool 输出自动转 `Evidence`。
- [ ] Evidence 包含 `source_type=db`、`db_id`、`table_name`、`fields`、`time_range`。
- [ ] Evidence 包含 `structured_data`。
- [ ] Evidence 支持 Claim 校验。

**验收标准**

- Report Verifier 可检查报告数字是否来自 DB Evidence。

### 3.5 结果采样与大结果控制

**TODO**

- [ ] 默认 limit。
- [ ] 最大行数限制。
- [ ] 超时控制。
- [ ] 大结果聚合优先，明细采样返回。

**验收标准**

- 大表查询不会阻塞 LangGraph 主流程。
- 超时返回类型化错误。

---

## 4. P2：安全、权限与审计

### 4.1 SQL 安全校验

**TODO**

- [ ] 禁止 `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE`。
- [ ] 禁止多语句。
- [ ] 禁止危险函数。
- [ ] 禁止访问系统表。
- [ ] 只允许 SELECT 或 WITH SELECT。

**验收标准**

- 非只读 SQL 全部拒绝。

### 4.2 租户隔离

**TODO**

- [ ] 所有查询自动注入 `tenant_id` 条件。
- [ ] 无法注入租户条件的表必须标记风险并拒绝。
- [ ] 跨租户查询需要显式管理员权限。

**验收标准**

- 查询结果不包含其他租户数据。

### 4.3 敏感字段策略

**TODO**

- [ ] 支持 `mask/hash/remove` 策略。
- [ ] 支持客户、员工、金额、联系方式等字段策略。
- [ ] 支持报告导出前的二次扫描。

**验收标准**

- `customer_id`、员工身份、薪资等敏感数据不进入报告明文。

### 4.4 查询审计

**TODO**

- [ ] 记录 `query_id`、`trace_id`、`tenant_id`、`db_id`、`table_name`、`fields`、`latency_ms`、`row_count`。
- [ ] 不记录完整敏感结果集。
- [ ] 支持查询失败审计。

**验收标准**

- 每个报告 Evidence 可以追溯到查询记录。

---

## 5. P3：智能查询增强

### 5.1 Schema 发现

**TODO**

- [ ] 支持查询表结构。
- [ ] 支持字段注释和业务含义。
- [ ] 支持表关系元数据。
- [ ] 支持按 DataSkill 白名单过滤 schema。

**验收标准**

- Planner 可以基于 schema 生成更准确查询。

### 5.2 NL-to-SQL 辅助

**TODO**

- [ ] 在 DataSkill 无法覆盖时，支持 NL-to-SQL。
- [ ] NL-to-SQL 必须经过白名单和 SQL 安全校验。
- [ ] 生成 SQL 必须返回可解释结构。
- [ ] 不允许直接执行未验证 SQL。

**验收标准**

- NL-to-SQL 仅作为 fallback，不替代 DataSkill。

### 5.3 查询自愈

**TODO**

- [ ] 字段不存在时尝试同义字段映射。
- [ ] 表不存在时返回建议表。
- [ ] SQL 执行失败时返回可修复错误。
- [ ] 限制最大自愈次数。

**验收标准**

- 查询失败不会产生无限重试。

### 5.4 多库结果聚合

**TODO**

- [ ] DBTool 不直接跨库 JOIN。
- [ ] Plan Executor 并行查询多个 DB。
- [ ] ResultAggregator 按租户、时间、工厂、产品等维度对齐。
- [ ] 聚合过程记录来源。

**验收标准**

- 运营复盘可以合并 MES、QMS、OMS、ERP、HR 数据。

---

## 6. 与 DataSkill 的集成 TODO

### 6.1 DataSkill Loader

- [ ] 新增 `DataSkillRegistry`。
- [ ] 校验 `db_targets`、`tables`、`metric_bindings`、`query_templates`。
- [ ] 校验表字段是否存在。
- [ ] 校验指标依赖是否完整。

### 6.2 Planner 集成

- [ ] Planner 优先读取 DataSkill 生成 DB `PlanStep`。
- [ ] Planner 不得绕过 DataSkill 选择未声明 DB / 表 / 字段。
- [ ] DataSkill 缺失时才允许 NL-to-SQL fallback。

### 6.3 Policy Guard 集成

- [ ] 校验 `db_id` 权限。
- [ ] 校验表权限。
- [ ] 校验字段权限。
- [ ] 校验敏感字段策略。
- [ ] 校验查询行数和超时预算。

### 6.4 Evidence Normalizer 集成

- [ ] 将 DBTool 输出转标准 Evidence。
- [ ] 支持 DB Evidence 与 ReportSkill 章节绑定。
- [ ] 支持 Claim 数字一致性校验。

---

## 7. 测试计划

### 7.1 单元测试

- [ ] `db_id` 映射。
- [ ] 表白名单。
- [ ] 字段白名单。
- [ ] 敏感字段脱敏。
- [ ] 结构化查询生成。
- [ ] SQL 安全拦截。
- [ ] 时间范围注入。
- [ ] 指标公式依赖检查。

### 7.2 集成测试

- [ ] 质量报告 DataSkill 查询。
- [ ] 运营复盘 DataSkill 查询。
- [ ] 交付绩效 DataSkill 查询。
- [ ] 生产日报 DataSkill 查询。
- [ ] 成本分析 DataSkill 查询。
- [ ] 工厂绩效 DataSkill 查询。

### 7.3 安全测试

- [ ] SQL 注入。
- [ ] 跨租户访问。
- [ ] 未授权表访问。
- [ ] 未授权字段访问。
- [ ] 非只读 SQL。
- [ ] 大结果集攻击。
- [ ] 敏感字段泄露。

---

## 8. 分阶段里程碑

### 阶段一：只读结构化 DBTool

目标：支撑 DataSkill 的核心查询。

交付：

- 多 `db_id` 支持。
- 表字段白名单。
- 参数化结构化查询。
- 时间范围支持。
- 标准 `DBQueryResult`。
- 基础 SQL 安全校验。

### 阶段二：指标和 Evidence

目标：支撑报告数字可信。

交付：

- 指标绑定。
- 查询模板。
- 聚合分组。
- DB Evidence。
- 查询审计。
- 敏感字段脱敏。

### 阶段三：Planner 集成

目标：Planner 根据 DataSkill 自动生成 DB PlanStep。

交付：

- DataSkillRegistry。
- Planner 集成。
- Policy Guard 集成。
- Evidence Normalizer 集成。

### 阶段四：智能增强

目标：处理 DataSkill 未覆盖场景。

交付：

- Schema 发现。
- NL-to-SQL fallback。
- 查询自愈。
- 多库结果聚合。

---

## 9. 当前六个 DataSkill 对 DBTool 的最低要求

| DataSkill | 最低 DBTool 能力 |
|-----------|------------------|
| `quality_data_access` | QMS + MES 查询、质量趋势、检验汇总、纠正措施 |
| `operations_data_access` | MES + QMS + OMS + ERP + HR 查询 |
| `factory_performance_data_access` | MES OEE、产量、工序节拍、QMS FPY |
| `delivery_data_access` | OMS 订单、交付异常、SCM 履约环节 |
| `production_daily_data_access` | MES 日报、停机、HR 出勤、QMS 当日质量 |
| `cost_data_access` | ERP 成本、HR 人工成本、SCM 物料成本、QMS 质量成本 |

---

## 10. 关键风险

1. **库表为推测模型**：当前 DataSkill 中的库表名称是合理猜测，落地前必须与实际数据库 schema 对齐。
2. **跨库口径不统一**：MES、QMS、ERP、HR 的时间、工厂、产品编码可能不同，需要维度映射。
3. **指标口径风险**：如 FPY、OEE、准交率、成本偏差率必须由业务确认。
4. **敏感数据风险**：客户、员工、成本金额必须做权限和脱敏。
5. **NL-to-SQL 风险**：不能作为主要路径，只能作为 DataSkill 缺失时的受限 fallback。

---

## 11. 下一步建议

1. 先实现 `StructuredQuery` 与 `DBQueryResult`。
2. 先接入 `quality_data_access` 作为端到端样例。
3. 用 `quality_report + quality_data_access + quality_knowledge_retrieval` 打通第一条报告链路。
4. 再复制到运营复盘、生产日报、交付绩效、成本分析和工厂绩效。
