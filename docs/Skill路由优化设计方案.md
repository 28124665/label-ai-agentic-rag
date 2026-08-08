# Skill 路由优化设计方案：分层路由树 + 渐进式披露

> **项目名称**：Skill 路由与加载机制规模化演进
> **设计目标**：将 Skill 系统从"扁平全量加载 + 关键词单点匹配"升级为"分层路由树 + 三级渐进式披露 + 分层递进匹配"，支撑 50+ Skill 规模下的精准路由与低内存开销
> **适用范围**：`agent/langgraph/skills/*`、`config/report_skills/*`、`agent/langgraph/routers/planner.py`、`agent/langgraph/tools/report/report_tool.py`
> **版本**：v1.0
> **前置文档**：`docs/Skill整合落地设计.md`（Skill 体系基础）、`docs/数据库Tool渐进式披露LLM化落地设计.md`（渐进式披露理念来源）
> **后续开发约束**：后续实现以本文档为准；历史文档仅作为背景参考

---

## 1. 背景与目标

### 1.1 现状

当前 Skill 系统已实现完整的解析链（`SkillResolver` 5 级优先级）和 4 个适配器（Planner / ReportTool / ReAct Policy / Answerability），配置了 7 套完整技能。但存在 6 个规模化瓶颈：

| # | 瓶颈 | 代码位置 | skill=7 | skill=50 |
|---|------|---------|---------|----------|
| 1 | 关键词匹配冲突 | `resolver.py:_find_keyword_skill` 子串包含 + 长度打分 | 可控 | "成本"同时命中 3+ skill，打分相同时结果不确定 |
| 2 | 配置膨胀不可复用 | `cost_analysis.yaml` 395 行 | 2000 行总量 | 15000+ 行，policy_constraints 大量重复 |
| 3 | 三件套硬绑定 | `linked_report_skill_id` 1:1 绑定 | 可控 | 无法让"质量报告"复用"成本数据访问"的 DB 能力 |
| 4 | 全量内存加载 | `registry.py:reload()` 启动全量扫描 | 秒级 | 50 × 400 行 YAML 常驻内存，启动慢 |
| 5 | 治理未接入 | `governance.py` 全套状态机已写但 Resolver 只查 `enabled` | 无影响 | lifecycle / 灰度 / 审计全部空转 |
| 6 | 选择不可解释 | Resolver 无候选集日志 | 无感 | 无法回答"为什么选了 A 而非 B" |

### 1.2 目标

引入"分层路由树 + 渐进式披露"理念，实现：

1. **找得快**：分层路由树按业务域组织，避免全量遍历
2. **装得下**：三级渐进式披露，L1 元数据常驻、L2/L3 按需加载
3. **匹配准**：分层递进匹配（keyword 候选 → embedding 排序 → LLM 终选）
4. **管得住**：治理 lifecycle + 权限 + 灰度全链路接入
5. **看得清**：选择 trace 审计 + 效果回归闭环

### 1.3 与"Agent 自主加载"模式的区别

行业内的"分层路由树 + 渐进式披露"方案（如 `learn-agent-skills` / `SkillTree`）通常面向**通用 Agent 自主探索**模式：Agent 通过 `skill_load` 工具按需加载 skill，LLM 自主决定加载哪个。

本项目是**企业级确定性路由**模式：`SkillResolver` 确定性解析 → `SkillPlannerAdapter` 生成 DAG → `ToolDispatcher` 执行。两种模式有本质区别：

| 维度 | Agent 自主加载模式 | 本项目（系统路由模式） |
|------|-------------------|----------------------|
| 谁决定加载哪个 skill | LLM 自主判断 | Resolver 确定性解析 |
| 加载触发方式 | Agent 调用 skill_load 工具 | 系统在路由节点自动解析 |
| 适用场景 | 通用对话、开放域 | 企业报告、合规约束 |
| 可控性 | 低（LLM 可能选错） | 高（确定性 + 可审计） |

**本方案的适配策略**：取"分层组织 + 三级加载"的架构精华，融入当前确定性路由体系，**不引入 Agent 自主加载工具**。

---

## 2. 架构总览

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        用户问题 + 上下文                              │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  分层递进匹配引擎（SkillResolver 升级版）                              │
│                                                                       │
│  第0层  显式 skill_id          < 1ms   精确命中，直接返回              │
│  第1层  report_type 精确匹配   < 1ms   精确命中，直接返回              │
│  第2层  domain + keyword 候选  < 5ms   定位业务域 → 域内 keyword 过滤  │
│  第2.5层 embedding 语义排序    < 20ms  TEI embedding 余弦相似度 top-k │
│  第3层  LLM function calling   < 300ms 仅当 top-1 置信度 < 阈值时触发  │
│                                                                       │
│  每层产出：候选集（非单一结果） + 治理过滤 + trace 记录                 │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  三级渐进式披露                                                       │
│                                                                       │
│  L1 元数据（常驻内存）                                                │
│    skill_id / name / description / domain / intent_keywords /         │
│    report_type / status / version / required_permissions              │
│    ← 仅用于路由匹配，~200 bytes/skill                                  │
│                                                                       │
│  L2 核心配置（匹配后按需加载）                                        │
│    section_templates / metric_definitions / claim_rules /             │
│    writing_style / evidence_requirements / policy_constraints         │
│    ← Resolver 匹配后、Planner/ReportTool 使用时加载                   │
│                                                                       │
│  L3 执行资源（执行时按需加载）                                        │
│    query_templates 渲染后的 SQL / export_templates Jinja2 /           │
│    references 附属文件 / chart 配置                                   │
│    ← 仅在 ToolDispatcher / ReportTool 执行时按需加载                  │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  下游消费（4 个 Adapter，不变）                                       │
│  SkillPlannerAdapter → DAG 计划                                       │
│  ReportTool          → 报告生成 + evidence 校验                       │
│  SkillPolicyAdapter  → ReAct 策略拦截                                 │
│  SkillEvidenceAdapter → 可答性校验                                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 分层路由树设计

### 3.1 树形组织

当前 7 套 skill 是扁平列表，`SkillResolver._find_keyword_skill` 线性遍历全部 skill。演进为按业务域组织的树：

```text
ROOT
├── finance（财务域）
│   ├── cost_analysis         成本分析报告
│   ├── budget_execution      预算执行报告        ← 未来
│   └── financial_risk        财务风险报告        ← 未来
├── production（生产域）
│   ├── production_daily      生产日报
│   ├── factory_performance   工厂绩效报告
│   └── capacity_analysis     产能分析报告        ← 未来
├── quality（质量域）
│   ├── quality_report        质量报告
│   └── defect_analysis       缺陷分析报告        ← 未来
├── delivery（交付域）
│   ├── delivery_performance  交付绩效报告
│   └── inventory_analysis    库存分析报告        ← 未来
├── operations（运营域）
│   ├── operations_review     运营复盘报告
│   └── efficiency_analysis   效率分析报告        ← 未来
└── generic（通用域）
    └── generic_analysis      通用分析报告（兜底）
```

### 3.2 域定位策略

路由树的价值在于**先粗筛再精选**，避免全量遍历：

```python
# 第 2 层：domain + keyword 候选
# 1. 先用 domain_keywords 定位业务域（粗筛）
# 2. 再在域内用 intent_keywords 匹配（精选）

DOMAIN_KEYWORDS = {
    "finance":    ["成本", "费用", "预算", "财务", "资金", "利润"],
    "production": ["生产", "产量", "产能", "工时", "排产", "交付进度"],
    "quality":    ["质量", "缺陷", "返工", "报废", "客诉", "合格率"],
    "delivery":   ["交付", "发货", "物流", "库存", "周转"],
    "operations": ["运营", "效率", "OEE", "复盘", "指标", "绩效"],
    "generic":    [],
}
```

定位域后，只在域内（2~3 个 skill）做 keyword 匹配，而非全量 50 个。这是 B+ 树索引的思想——先定位分支再扫描叶子。

### 3.3 域配置结构

每个 skill 的 YAML 头部新增 `domain` 字段：

```yaml
# config/report_skills/cost_analysis.yaml
skill_id: cost_analysis
domain: finance              # ← 新增：所属业务域
version: "1.0"
name: 成本分析报告
description: 制造成本、人工成本、质量成本、物料成本分析
# ... 其余不变
```

域定义独立配置：

```yaml
# config/report_skills/_domains.yaml
domains:
  finance:
    name: 财务域
    description: 成本、预算、财务风险相关报告
    keywords: ["成本", "费用", "预算", "财务", "资金", "利润"]
  production:
    name: 生产域
    description: 生产、产量、产能相关报告
    keywords: ["生产", "产量", "产能", "工时", "排产"]
  # ...
```

---

## 4. 三级渐进式披露设计

### 4.1 三级划分

| 级别 | 内容 | 加载时机 | 内存开销 | 对应原配置字段 |
|------|------|---------|---------|--------------|
| **L1 元数据** | 路由匹配必需的最小信息 | 启动时常驻 | ~200 bytes/skill | skill_id, name, description, domain, intent_keywords, report_type, status, version, required_permissions |
| **L2 核心配置** | 计划生成 / 报告结构 / 策略约束 | Resolver 匹配后按需加载 | ~4KB/skill | section_templates, metric_definitions, claim_rules, writing_style, evidence_requirements, policy_constraints, publish_policy |
| **L3 执行资源** | 实际执行时的重资源 | ToolDispatcher / ReportTool 执行时加载 | 按需、用完可释放 | query_templates 渲染后 SQL, export_templates Jinja2, references 附属文件, chart 配置 |

### 4.2 内存对比

```text
当前（全量加载）:
  50 skill × 400 行 YAML ≈ 20000 行配置常驻内存
  含 section_templates（最大字段）、metric_definitions 等重型结构

演进后（三级披露）:
  L1 常驻:  50 skill × 200 bytes ≈ 10KB（仅元数据）
  L2 按需:  每次请求仅加载 1 个 skill 的 L2 ≈ 4KB
  L3 按需:  仅执行时加载，用完释放
```

### 4.3 L1 元数据模型

```python
class SkillMetadata(BaseModel):
    """L1 元数据 — 常驻内存，仅用于路由匹配。

    设计原则：字段精简到路由决策所需的最小集，
    不含任何重型结构（section_templates / metric_definitions 等）。
    """

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    domain: str                          # 所属业务域
    version: str
    name: str
    description: str                     # 用于 embedding 语义匹配
    report_type: str                     # 用于 report_type 精确匹配
    intent_keywords: list[str]           # 用于 keyword 候选过滤
    status: str = "active"               # 治理生命周期状态
    required_permissions: list[str] = Field(default_factory=list)
    enabled: bool = True

    # L2/L3 的加载入口（不持有数据，只持有路径）
    config_path: str = ""                # YAML 文件路径，L2 按需读取
```

### 4.4 L2 懒加载机制

```python
class SkillRegistry:
    def __init__(self, root: Path | None = None):
        self.root = root or ...
        self._metadata: dict[str, SkillMetadata] = {}    # L1 常驻
        self._full_skills: dict[str, ReportSkill] = {}    # L2 缓存（LRU）
        self._l2_cache_max = 20                           # L2 缓存上限

    def reload(self) -> None:
        """启动时仅加载 L1 元数据，不加载 L2/L3。"""
        for path in sorted(self.root.glob("*.yaml")):
            if path.name.startswith("_"):
                continue  # 跳过 _domains.yaml 等非 skill 文件
            metadata = self._parse_metadata_only(path)
            self._metadata[metadata.skill_id] = metadata

    def _parse_metadata_only(self, path: Path) -> SkillMetadata:
        """仅解析 YAML 的头部字段（L1），不反序列化重型结构。

        通过 yaml.safe_load 读取后只取 L1 字段，
        跳过 section_templates / metric_definitions 等大列表的反序列化。
        """
        with path.open(encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
        return SkillMetadata(
            skill_id=payload["skill_id"],
            domain=payload.get("domain", "generic"),
            version=payload.get("version", "1.0"),
            name=payload["name"],
            description=payload["description"],
            report_type=payload.get("report_type", ""),
            intent_keywords=payload.get("intent_keywords", []),
            status=payload.get("governance", {}).get("status", "active"),
            required_permissions=payload.get("required_permissions", []),
            enabled=payload.get("enabled", True),
            config_path=str(path),
        )

    def load_full_skill(self, skill_id: str) -> ReportSkill | None:
        """L2 按需加载：从 YAML 反序列化完整 ReportSkill。

        匹配后调用，加载后缓存到 LRU。
        超过 _l2_cache_max 时淘汰最久未访问的。
        """
        # 1. 命中缓存
        if skill_id in self._full_skills:
            return self._full_skills[skill_id]

        # 2. 从 L1 元数据找到文件路径
        metadata = self._metadata.get(skill_id)
        if metadata is None:
            return None

        # 3. 反序列化完整 YAML（L2 加载）
        path = Path(metadata.config_path)
        with path.open(encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}
        payload.setdefault("skill_type", "report")
        skill = ReportSkill.model_validate(payload)

        # 4. LRU 缓存
        self._lru_put(skill_id, skill)
        return skill
```

### 4.5 L3 执行时加载

L3 资源由下游 Adapter 在执行时按需加载，不在 Registry 层处理：

```python
# SkillPlannerAdapter._build_database_steps 中
# L3: query_templates 的 SQL 渲染在计划生成时按需执行
filters = template.get("filters", {})
rendered_filters = self._render_value(filters, template_context)  # ← L3 渲染

# ReportTool 中
# L3: export_templates 的 Jinja2 渲染在导出时按需执行
template = self._load_export_template(skill.default_format)  # ← L3 加载
```

---

## 5. 分层递进匹配引擎

### 5.1 匹配流程

```text
输入: user_question, tenant_id, skill_id?, report_type?, route_decision?

第0层: 显式 skill_id (< 1ms)
  ├─ 命中 → 治理过滤 → 返回
  └─ 未命中 ↓

第1层: report_type 精确匹配 (< 1ms)
  ├─ 命中 → 治理过滤 → 返回
  └─ 未命中 ↓

第2层: domain + keyword 候选集 (< 5ms)
  ├─ domain_keywords 定位业务域 → 域内 intent_keywords 匹配
  ├─ 候选数 == 1 → 治理过滤 → 返回
  ├─ 候选数 > 1 → 进入第2.5层
  └─ 候选数 == 0 → 进入第2.5层（全量 fallback）

第2.5层: embedding 语义排序 (< 20ms)
  ├─ question embedding ∩ 候选 description embedding 余弦相似度
  ├─ top-1 相似度 >= 0.80 → 治理过滤 → 返回
  └─ top-1 相似度 < 0.80 → 进入第3层

第3层: LLM function calling 终选 (< 300ms)
  ├─ 把 top-3 候选的 name+description 作为 tool schema 交给 LLM
  ├─ LLM 返回 skill_id → 治理过滤 → 返回
  └─ LLM 失败/超时 → tenant_default → fallback

兜底: generic_analysis (fallback_used=True)
```

### 5.2 第 2 层：domain + keyword 候选集

当前 `_find_keyword_skill` 返回单一 max 结果，改为返回**候选集**：

```python
def _find_keyword_candidates(
    self, question: str
) -> list[tuple[int, SkillMetadata]]:
    """keyword 候选过滤：先定位域，再域内匹配。

    返回所有命中关键词的候选及其得分（降序），
    而非只返回 max。消除匹配不确定性。
    """
    normalized = question.casefold()

    # 1. 定位业务域（粗筛）
    matched_domains = self._match_domains(normalized)

    # 2. 域内 keyword 匹配（精选）
    candidates: list[tuple[int, SkillMetadata]] = []
    for skill_id, metadata in self._metadata.items():
        if not metadata.enabled:
            continue
        # 如果定位到了域，只在域内匹配；否则全量（fallback）
        if matched_domains and metadata.domain not in matched_domains:
            continue
        score = sum(
            len(kw) for kw in metadata.intent_keywords
            if kw.casefold() in normalized
        )
        if score > 0:
            candidates.append((score, metadata))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates

def _match_domains(self, normalized_question: str) -> set[str]:
    """用 domain_keywords 定位业务域。"""
    matched = set()
    for domain, keywords in self._domain_keywords.items():
        if any(kw.casefold() in normalized_question for kw in keywords):
            matched.add(domain)
    return matched
```

### 5.3 第 2.5 层：embedding 语义排序

复用项目已有的 TEI embedding 服务（`gateways/retriever.py`），对候选 skill 的 `description` 做预计算 embedding：

```python
class SkillEmbeddingCache:
    """skill description embedding 预计算缓存。

    启动时对所有 L1 元数据的 description 做 embedding，
    缓存到内存。description 变更时通过文件监听刷新。
    """

    def __init__(self, registry: SkillRegistry):
        self._registry = registry
        self._embeddings: dict[str, list[float]] = {}

    async def warmup(self, tenant_id: str):
        """启动时预计算所有 skill description 的 embedding。"""
        resolver = get_gateway_resolver()
        gateway = await resolver.embedding_for(tenant_id)
        for skill_id, metadata in self._registry.list_metadata().items():
            if not metadata.description:
                continue
            result = await gateway.async_embed(
                tenant_id=tenant_id,
                texts=[metadata.description],
            )
            self._embeddings[skill_id] = result.vectors[0]

    async def rank(
        self,
        question: str,
        candidates: list[SkillMetadata],
        tenant_id: str,
        top_k: int = 3,
    ) -> list[tuple[float, SkillMetadata]]:
        """用 question embedding 与候选 description embedding 做余弦相似度。"""
        resolver = get_gateway_resolver()
        gateway = await resolver.embedding_for(tenant_id)
        q_result = await gateway.async_embed(tenant_id=tenant_id, texts=[question])
        q_vec = q_result.vectors[0]

        scored = []
        for metadata in candidates:
            s_vec = self._embeddings.get(metadata.skill_id)
            if s_vec is None:
                continue
            similarity = self._cosine_similarity(q_vec, s_vec)
            scored.append((similarity, metadata))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0
```

### 5.4 第 3 层：LLM function calling 终选

仅当 embedding top-1 相似度 < 0.80 时触发，把 top-3 候选交给 LLM：

```python
async def _llm_select(
    self,
    question: str,
    candidates: list[SkillMetadata],
    tenant_id: str,
) -> SkillMetadata | None:
    """LLM function calling 终选：把候选 skill 作为 tool schema 交给 LLM 选择。

    对标 OpenAI function calling：LLM 从候选中选择最匹配的 skill_id。
    复用项目 ModelGateway 调用链路。
    """
    resolver = get_gateway_resolver()
    gateway = await resolver.model_for(tenant_id)

    # 构造 function schema（仅 L1 元数据，不含 L2 重型配置）
    tools = [
        {
            "type": "function",
            "function": {
                "name": metadata.skill_id,
                "description": metadata.description,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for metadata in candidates
    ]

    system_prompt = (
        "根据用户问题从候选技能中选择最匹配的一个。"
        "只返回技能 ID，不要解释。"
    )

    result = await gateway.async_chat(
        tenant_id=tenant_id,
        llm_id="qwen3.6-27b",
        system=system_prompt,
        history=[{"role": "user", "content": question}],
        tools=tools,
        gen_conf={"temperature": 0.0, "max_tokens": 100},
    )

    # 解析 LLM 返回的 tool_call
    selected_id = self._parse_tool_call(result, candidates)
    if selected_id:
        return self._metadata.get(selected_id)
    return None
```

---

## 6. 治理接入

### 6.1 问题

`governance.py` 已实现完整状态机（draft → pending_review → approved → active → deprecated）、权限交集、审计日志，但 `SkillResolver._resolve_report_skill` 只查 `enabled`：

```python
# 当前（resolver.py:58）— 治理空转
if not report_skill.enabled or not self._has_permission(report_skill, context):
    return self._failure("SKILL_PERMISSION_DENIED")
```

### 6.2 改造

Resolver 接入 `is_skill_usable` 完整治理判定：

```python
from agent.langgraph.skills.governance import (
    is_skill_usable,
    SkillResolverLifecycleFilter,
)

class SkillResolver:
    def __init__(self, registry: SkillRegistry):
        self.registry = registry
        self._lifecycle_filter = SkillResolverLifecycleFilter()

    def _resolve_report_skill(
        self,
        metadata: SkillMetadata,
        source: str,
        context: SkillResolveContext,
        fallback_used: bool = False,
    ) -> SkillResolveResult:
        # ★ 接入完整治理判定（替代原 enabled 检查）
        governance = self._get_governance(metadata.skill_id)
        usable, reason = is_skill_usable(
            skill_status=metadata.status,
            skill_required_permissions=metadata.required_permissions,
            user_permissions=self._user_permissions(context),
            tenant_id=context.tenant_id,
            tenant_overrides=governance.get("tenant_overrides"),
            lifecycle_filter=self._lifecycle_filter,
        )
        if not usable:
            return self._failure(f"SKILL_GOVERNANCE_DENIED: {reason}")

        # 治理通过后，L2 按需加载完整 ReportSkill
        report_skill = self.registry.load_full_skill(metadata.skill_id)
        if not isinstance(report_skill, ReportSkill):
            return self._failure("SKILL_NOT_FOUND")

        # 拉取配套 DataSkill / RetrievalSkill（同样走治理过滤）
        data_skill = self._load_linked_skill_with_governance(
            registry.get_data_for_report(metadata.skill_id), context
        )
        retrieval_skill = self._load_linked_skill_with_governance(
            registry.get_retrieval_for_report(metadata.skill_id), context
        )

        # ... 后续组装 ResolvedSkillSet 逻辑不变
```

### 6.3 治理字段配置

skill YAML 新增 `governance` 段：

```yaml
skill_id: cost_analysis
domain: finance
version: "1.0"
# ... 原有字段 ...

governance:
  status: active                    # draft / pending_review / approved / active / deprecated
  created_by: system
  approved_by: admin@factory.com
  created_at: "2025-06-01T00:00:00Z"
  activated_at: "2025-06-05T00:00:00Z"
  tenant_overrides:
    tenant_B: true                  # tenant_B 灰度试用
  change_log:
    - timestamp: "2025-06-05T00:00:00Z"
      actor: admin@factory.com
      action: activate
      from_status: approved
      to_status: active
      reason: "正式上线"
```

---

## 7. 选择 Trace 与可观测性

### 7.1 Trace 模型

```python
class SkillResolveTrace(BaseModel):
    """skill 解析轨迹 — 每次 resolve 写一条，便于离线分析。"""

    request_id: str
    user_question: str
    tenant_id: str

    # 各层级命中情况
    explicit_skill_id: str | None = None        # 第0层
    report_type_match: str | None = None         # 第1层
    domain_matched: list[str] = Field(default_factory=list)  # 第2层域定位
    keyword_candidates: list[dict] = Field(default_factory=list)  # 第2层候选
    # [{skill_id, keyword_score}]
    semantic_rerank: list[dict] = Field(default_factory=list)     # 第2.5层
    # [{skill_id, similarity}]
    llm_selection: str | None = None             # 第3层 LLM 选择
    governance_filtered: list[str] = Field(default_factory=list)  # 被治理过滤的

    # 最终结果
    resolved_skill_id: str = ""
    resolution_source: str = ""                  # skill_id/report_type/keyword/semantic/llm/tenant_default/fallback
    fallback_used: bool = False
    elapsed_ms: int = 0
```

### 7.2 Trace 写入

```python
class SkillResolver:
    async def resolve(self, context: SkillResolveContext) -> SkillResolveResult:
        trace = SkillResolveTrace(
            request_id=context.request_id or "",
            user_question=context.user_question,
            tenant_id=context.tenant_id,
        )
        start = time.time()

        # ... 各层匹配逻辑，每层写入 trace.xxx ...

        trace.resolved_skill_id = result.skill_set.report_skill.skill_id if result.resolved else ""
        trace.resolution_source = result.skill_set.resolution_source if result.resolved else ""
        trace.fallback_used = result.fallback_used
        trace.elapsed_ms = int((time.time() - start) * 1000)

        # 写入日志 + Langfuse（复用项目 observability 链路）
        logger.info(f"[SkillResolveTrace] {trace.model_dump_json()}")
        self._emit_to_langfuse(trace)

        return result
```

### 7.3 效果闭环

Trace 与下游 ReportTool 的 `quality_score` 关联，建立反馈闭环：

```text
SkillResolveTrace.resolved_skill_id
        ↓ 关联
ReportTool.output.quality_score
        ↓ 离线分析
"cost_analysis skill 的 keyword '成本' 与 operations_review 冲突率高"
        ↓ 优化
调整 intent_keywords 或升级到 embedding 匹配
```

---

## 8. 配置 Fragment 复用

### 8.1 问题

7 套 skill 的 `policy_constraints` 几乎完全相同，50 套时重复量巨大。

### 8.2 Fragment 机制

```yaml
# config/report_skills/_fragments/common_db_policy.yaml
fragment_id: common_db_policy
policy_constraints:
  require_tenant_filter: true
  forbidden_operations: [insert, update, delete, drop, alter, truncate]
  max_rows: 30000
  timeout_ms: 60000
  allow_cross_db_join: false
```

skill 配置中 import：

```yaml
# config/report_skills/data/cost_data_access.yaml
skill_id: cost_data_access
imports:
  - fragment: common_db_policy
  - fragment: common_tenant_filter
db_targets:
  - target_id: erp_cost
    # ... 仅声明差异部分
```

### 8.3 Registry 加载合并

```python
class SkillRegistry:
    def _load_full_skill_from_path(self, path: Path) -> ReportSkill:
        with path.open(encoding="utf-8") as f:
            payload = yaml.safe_load(f) or {}

        # 合并 fragment
        imports = payload.pop("imports", [])
        for import_spec in imports:
            fragment = self._load_fragment(import_spec["fragment"])
            payload = self._deep_merge(fragment, payload)  # payload 优先

        payload.setdefault("skill_type", "report")
        return ReportSkill.model_validate(payload)

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        """递归合并：override 的同名字段覆盖 base。"""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = SkillRegistry._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
```

---

## 9. 端到端工作流程

以"帮我生成本月成本分析报告"为例：

```text
1. intent_router_node
   ├─ 第0-2层路由判定为 complex → planner
   └─ planner.plan() → RouteDecision(target="hybrid", complexity="complex")

2. skill_resolver_node（新增节点，位于 intent_router 之后）
   ├─ 第0层: skill_id 未指定 → 跳过
   ├─ 第1层: report_type 未指定 → 跳过
   ├─ 第2层: domain="finance"(命中"成本"), keyword 候选=[cost_analysis(score=6)]
   │         候选数==1 → 治理过滤(active + cost:read 权限校验) → 通过
   ├─ L2 按需加载: load_full_skill("cost_analysis") → 完整 ReportSkill
   ├─ 写入 state: {skill_set, skill_evidence_requirements}
   └─ trace 记录: resolved=cost_analysis, source=keyword, elapsed=3ms

3. plan_executor_node
   ├─ 读取 route_decision.metadata.plan 或调 planner.plan_with_skills(skill_set)
   ├─ SkillPlannerAdapter.build_plan(skill_set) → DAG
   │   ├─ query_cost_summary (database, can_parallel=True)
   │   ├─ query_labor_cost   (database, can_parallel=True)
   │   ├─ query_material_cost(database, can_parallel=True)
   │   ├─ retrieve_cost_kb   (rag, can_parallel=True)
   │   └─ generate_cost_analysis (report, depends_on=以上全部)
   └─ DAGScheduler 并行执行

4. tool_dispatcher 执行各步骤
   ├─ database 步骤: 从 step.extra 取 query_template_id → L3 渲染 SQL → 执行
   ├─ rag 步骤: 从 step.extra 取 rag_target_id → SkillRAGExecutor → 检索
   └─ report 步骤: 从 step.extra 取 skill_set → ReportTool._resolve_skill_set
       ├─ 已有 skill_set → 直接用（无需二次解析）
       ├─ evidence 校验: SkillEvidenceAdapter.check() → publish_mode=full
       └─ 生成报告

5. answerability_check_node
   ├─ state.get("skill_set") → 读取（第2步注入的）
   ├─ SkillEvidenceAdapter.check() → answerable=True
   └─ recommended_action=generate → quality_check

6. quality_check → prompt_assembly → final_answer
```

---

## 10. 数据结构变更汇总

### 10.1 AgentState 新增字段

```python
# agent/langgraph/state.py — AgentState 新增
class AgentState(TypedDict, total=False):
    # ... 原有字段 ...

    # ========== Skill 路由相关 ==========
    skill_set: Optional[dict]                    # ResolvedSkillSet（序列化）
    skill_evidence_requirements: list[dict]       # 预计算的 evidence 需求
    skill_resolve_trace: Optional[dict]           # 解析轨迹（可观测性）
```

### 10.2 SkillMetadata（L1）— 新增模型

```python
# agent/langgraph/skills/models.py — 新增
class SkillMetadata(BaseModel):
    """L1 元数据 — 常驻内存，仅用于路由匹配。"""
    model_config = ConfigDict(extra="forbid")

    skill_id: str
    domain: str
    version: str
    name: str
    description: str
    report_type: str
    intent_keywords: list[str] = Field(default_factory=list)
    status: str = "active"
    required_permissions: list[str] = Field(default_factory=list)
    enabled: bool = True
    config_path: str = ""
```

### 10.3 ReportSkill 新增字段

```python
class ReportSkill(SkillBase):
    # ... 原有字段 ...
    domain: str = "generic"           # 新增：所属业务域
    governance: dict[str, Any] = Field(default_factory=dict)  # 新增：治理元数据
```

### 10.4 域配置

```yaml
# config/report_skills/_domains.yaml — 新增文件
domains:
  finance:
    name: 财务域
    description: 成本、预算、财务风险相关报告
    keywords: ["成本", "费用", "预算", "财务", "资金", "利润"]
  production:
    name: 生产域
    description: 生产、产量、产能相关报告
    keywords: ["生产", "产量", "产能", "工时", "排产"]
  quality:
    name: 质量域
    description: 质量相关报告
    keywords: ["质量", "缺陷", "返工", "报废", "客诉", "合格率"]
  delivery:
    name: 交付域
    description: 交付、物流、库存相关报告
    keywords: ["交付", "发货", "物流", "库存", "周转"]
  operations:
    name: 运营域
    description: 运营、效率、复盘相关报告
    keywords: ["运营", "效率", "OEE", "复盘", "指标", "绩效"]
  generic:
    name: 通用域
    description: 通用分析兜底
    keywords: []
```

---

## 11. 核心模块改造清单

| 模块 | 改动 | 优先级 | 关键变化 |
|------|------|:---:|---------|
| `skills/models.py` | 新增 `SkillMetadata`、`SkillResolveTrace`；`ReportSkill` 加 `domain` + `governance` | P0 | L1/L2 分离的数据基础 |
| `skills/registry.py` | 全量加载 → L1 元数据常驻 + L2 LRU 懒加载；新增 fragment 合并 | P0 | 内存优化 + 配置复用 |
| `skills/resolver.py` | keyword 单选 → 候选集；接入治理 `is_skill_usable`；新增 domain 定位；写 trace | P0 | 消除匹配不确定性 + 治理生效 |
| `skills/governance.py` | 无代码改动，被 Resolver 调用即可 | P0 | 治理不再空转 |
| `skills/semantic_cache.py` | 新增：skill description embedding 预计算 + 语义排序 | P2 | 解决关键词冲突 |
| `skills/llm_selector.py` | 新增：LLM function calling 终选 | P3 | 语义级精准匹配 |
| `state.py` | 新增 `skill_set` / `skill_evidence_requirements` / `skill_resolve_trace` 字段 | P0 | state 注入桥梁 |
| `nodes/skill_resolver_node.py` | 新增节点：调用 Resolver → 写入 state | P0 | 连通解析→注入→消费全链路 |
| `graph.py` | 新增 `skill_resolver_node` 到边：intent_router → skill_resolver → plan_executor | P0 | 接线 |
| `config/report_skills/_domains.yaml` | 新增域配置 | P0 | 路由树定义 |
| `config/report_skills/_fragments/*.yaml` | 新增公共 fragment | P2 | 配置复用 |
| 现有 7 套 skill YAML | 每个加 `domain` 字段 + `governance.status: active` | P0 | 标注域 + 激活治理 |

---

## 12. 演进路线

### 阶段一：P0 — 治理接入 + 候选集 + state 桥梁（2 周）

**目标**：治理不再空转、匹配不确定消除、四个 Adapter 全链路生效

- [ ] `models.py`：新增 `SkillMetadata`、`SkillResolveTrace`
- [ ] `registry.py`：L1 元数据常驻 + L2 懒加载
- [ ] `resolver.py`：keyword 候选集 + 治理接入 + trace 写入
- [ ] `state.py`：新增 skill_set 等字段
- [ ] `nodes/skill_resolver_node.py`：新增解析注入节点
- [ ] `graph.py`：接线 intent_router → skill_resolver → plan_executor
- [ ] 7 套 skill YAML 加 `domain` + `governance.status`
- [ ] `config/report_skills/_domains.yaml` 域配置
- [ ] 单测：候选集、治理过滤、trace、全链路

### 阶段二：P1 — Trace 可观测性（1 周）

**目标**：选择可解释、可回归

- [ ] Trace 写入 Langfuse（复用 observability 链路）
- [ ] 离线分析脚本：skill 选择命中率、冲突率、fallback 率
- [ ] 质量反馈关联：trace ↔ ReportTool.quality_score

### 阶段三：P2 — Embedding 语义排序 + Fragment 复用（2 周）

**目标**：关键词冲突彻底解决、配置重复消除

- [ ] `skills/semantic_cache.py`：description embedding 预计算
- [ ] `resolver.py`：第 2.5 层 embedding 排序
- [ ] `config/report_skills/_fragments/`：公共 fragment
- [ ] `registry.py`：fragment 合并逻辑
- [ ] 现有 skill YAML 抽取公共 fragment

### 阶段四：P3 — LLM 终选（1 周）

**目标**：语义级精准匹配

- [ ] `skills/llm_selector.py`：function calling 终选
- [ ] `resolver.py`：第 3 层触发条件（embedding top-1 < 0.80）
- [ ] 降级策略：LLM 超时 → tenant_default → fallback

### 阶段五：远期 — DB 存储 + Admin API + 多版本灰度

**目标**：配合微服务化，skill 管理平台化

- [ ] skill 配置迁移到 DB（admin-api 管理）
- [ ] 多版本共存（v1/v2 同时 active，按租户灰度）
- [ ] Admin UI CRUD + 审批流
- [ ] 文件热加载（watchdog）→ DB 缓存失效

---

## 13. Java 对照

| Python 概念 | Java 等价 | 说明 |
|------------|----------|------|
| `SkillMetadata`（L1） | `@Component` + `@Lazy` Bean 元数据 | 轻量元数据常驻 |
| `load_full_skill()`（L2 懒加载） | `@Lazy` + `ApplicationContext.getBean()` | 按需初始化 |
| LRU L2 缓存 | Caffeine `Cache.builder().maximumSize(20)` | L2 缓存淘汰 |
| 分层路由树 | Spring Cloud Gateway 路由树 / Nacos 配置分组 | 按域分组路由 |
| domain + keyword 候选集 | `Map<Domain, List<Skill>>` + `Predicate` 过滤 | 先分组再过滤 |
| embedding 语义排序 | Spring AI `EmbeddingStore` + 相似度检索 | 向量召回 |
| LLM function calling 终选 | Spring AI `ChatClient.tools()` | LLM 选择 |
| Fragment 复用 | Maven parent POM + `dependencyManagement` | 配置继承 |
| 治理 lifecycle | Spring StateMachine / Activiti 流程 | 状态机 |
| `is_skill_usable` | Spring Security `@PreAuthorize` + `AccessDecisionManager` | 权限+生命周期 |
| SkillResolveTrace | Spring Actuator + Micrometer Span | 链路追踪 |
| `skill_resolver_node` state 注入 | Spring `@EventListener` + `RequestScope` Bean | 状态传递 |

---

## 14. 不建议做的事

| 反模式 | 原因 |
|--------|------|
| 引入 Agent 自主加载（skill_load 工具） | 当前是确定性路由模式，Agent 自主加载会破坏可审计性 |
| 一开始就上 DB 存储 | YAML + 文件监听已能满足 50 skill 规模，DB 是远期事 |
| 引入 Service Mesh 做 skill 发现 | skill 是配置资源不是微服务，不需要 sidecar |
| 把 domain 定位也交给 LLM | domain 定位用关键词足够快足够准，LLM 增加延迟无收益 |
| L2 缓存设得过大 | LRU 20 已够（单请求只用 1 个 skill），大了浪费内存 |
| 每次都触发 LLM 终选 | LLM 终选是兜底（< 5% 请求），keyword + embedding 覆盖 95% |

---

## 15. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|:---:|:---:|------|
| embedding 服务不可用 | 中 | 第 2.5 层降级 | 降级到 keyword 候选集取 top-1 |
| LLM 终选超时 | 中 | 第 3 层降级 | 降级到 tenant_default → fallback |
| L2 懒加载首次延迟 | 低 | 首请求慢 ~5ms | 启动时预热 top-3 高频 skill 的 L2 |
| fragment 合并冲突 | 低 | 配置加载失败 | validator 校验 fragment 引用完整性 |
| 治理过滤导致无 skill 可用 | 低 | 请求失败 | generic_analysis 兜底（无需治理过滤） |

---

> **总结**：本方案以"分层路由树"解决"找得快"，以"三级渐进式披露"解决"装得下"，以"分层递进匹配"解决"匹配准"，以"治理接入"解决"管得住"，以"trace 审计"解决"看得清"。P0 阶段（2 周）即可让现有 7 套 skill 的治理全链路生效并消除匹配不确定性，为 50+ skill 规模化奠定基础。
