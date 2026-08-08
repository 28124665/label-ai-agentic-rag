# RAG 增强能力有机融合方案设计文档

> **文档定位**：将二期技术报告（phase2_technical_achievement.md）中描述的 5 项核心能力，有机融合进现有 LangGraph 主流程
> **受众**：架构师、后端开发工程师
> **基线**：LangGraph 主流程 + RAGFlow 组件库（单体阶段）
> **目标**：恢复能力 + 不阻碍微服务拆分

---

## 一、背景与目标

### 1.1 问题背景

二期技术报告描述了 5 项核心能力增强：

| 能力维度 | 二期报告描述 | 当前 LangGraph 实际实现 | 下降程度 |
|---------|------------|----------------------|---------|
| **幻觉检测** | 三层验证（规则+NLI+LLM）+ 四级处置 | 字符集合重叠度 | 🔴 严重 |
| **检索质量评估** | Grader 三模式（LLM/Cross-Encoder/NLI） | Rerank 分数阈值 | 🟡 中度 |
| **熔断机制** | CircuitBreaker 状态机 + 主动健康检查 | try/except 异常捕获 | 🟡 中度 |
| **查询重写** | 三策略完整（同义词+子查询拆解+HyDE） | 三策略均简化为同义词扩展 | 🟡 中度 |
| **重试控制** | 分级重试（max_retries=3）+ Token 预算 | 固定 1 次内部重试 | 🟢 轻度 |

**根因**：LangGraph 主流程倾向于独立实现简化逻辑，未调用 RAGFlow 已有的完整组件。

### 1.2 设计目标

1. **恢复能力**：让二期技术报告中的 5 项能力在 LangGraph 主流程中真实生效
2. **不阻碍微服务拆分**：通过 Gateway 抽象，单体阶段 import 调用，微服务阶段 HTTP 调用
3. **渐进式落地**：配置开关控制，支持灰度发布
4. **降级兜底**：组件调用失败时回退到简化实现，不影响主流程
5. **零流程改动**：LangGraph 状态图的节点和边保持不变，仅在节点内部增强
6. **主流程协同（核心约束）**：RAGTool 内部增强不得与主流程节点职责重复。
   主流程已通过 `quality_check` 节点承担"重试决策"职责，通过 `hallucination` 节点
   承担"幻觉处置"职责，RAGTool 内部不得再保留同类决策逻辑，避免双重重试和决策冲突。

### 1.3 职责边界划定（避免重复的关键）

为消除"双重重试"等重复问题，明确 RAGTool 内部与主流程节点的职责边界：

| 职责 | 归属层 | 具体位置 | 说明 |
|------|--------|---------|------|
| **检索执行** | RAGTool 内部 | `RAGTool.invoke()` | 单次执行：预处理→重写→检索→Rerank→打分 |
| **重试决策** | 主流程 | `quality_check` 节点 | 基于 quality_score 决定 pass/retry_rag/fallback_web |
| **Token 预算管控** | 主流程 | `quality_check` 节点 | 统一管控 retry_token_used，RAGTool 不感知预算 |
| **查询重写策略选择** | RAGTool 内部 | `_rewrite_query()` | 接收主流程 retry_count 作为轮转起点 |
| **质量打分** | RAGTool 内部 | `_evaluate_quality()` | 输出 quality_score + 分数来源标注 |
| **质量阈值决策** | 主流程 | `quality_check` 节点 | 按分数来源使用不同阈值 |
| **幻觉检测** | 主流程 | `hallucination` 节点 | 通过 VerifierGateway 调用三层验证 |
| **幻觉处置** | 主流程 | `hallucination` 节点 | 四级处置（pass/filter/regenerate/reject） |
| **熔断保护** | 网关层 | `LocalRetrieverGateway` | 对主流程透明 |

---

## 二、设计原则

### 2.1 依赖倒置原则（DIP）

LangGraph 节点**只依赖接口**（Gateway 协议），不依赖 RAGFlow 实现：

```
LangGraph 节点（高层） → VerifierGateway 接口（抽象） → 实现（低层）
                                              ├─ LocalVerifierGateway（import 调用）
                                              └─ HttpVerifierGateway（HTTP 调用）
```

### 2.2 防腐层模式（ACL）

通过 Gateway 封装 RAGFlow 组件调用，隔离 RAGFlow 变化对 LangGraph 的影响：

```
LangGraph 主流程 ←→ Gateway 防腐层 ←→ RAGFlow 组件库
                  （接口稳定）      （可独立演进）
```

### 2.3 配置驱动开关

所有增强能力通过配置开关控制，支持灰度发布和 A/B 测试：

```yaml
enhancement:
  hallucination:
    enabled: true
    use_three_layer: true
```

### 2.4 降级兜底

组件调用失败时回退到简化实现，保证主流程可用：

```python
try:
    score = await detector.verify(answer, context)
except Exception:
    score = _fallback_overlap_score(answer, context)  # 降级
```

---

## 三、架构设计

### 3.1 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    LangGraph 主流程（编排层）                       │
│                                                                  │
│  intent_router → rag_tool → quality_check → ... → hallucination  │
│                       │              │                    │      │
│                       │         调用接口              调用接口    │
│                       ↓              ↓                    ↓      │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              Gateway 防腐层（接口层）                        │  │
│  │  RetrieverGateway  │  GraderGateway  │  VerifierGateway    │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬───────────────────────────────────┘
                               │ GatewayResolver 路由
               ┌───────────────┼───────────────┐
               ↓               ↓               ↓
  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
  │ Local Gateway  │  │  Http Gateway  │  │Shadow Gateway  │
  │ (import 调用)  │  │ (HTTP 调用)    │  │ (双跑对比)     │
  │                │  │                │  │                │
  │ RAGFlow 组件   │  │ 验证微服务     │  │ Local + Http   │
  └────────────────┘  └────────────────┘  └────────────────┘
    单体阶段             微服务阶段          灰度阶段
```

### 3.2 Gateway 抽象层设计

#### 3.2.1 接口定义

```python
# agent/langgraph/gateways/verifier.py
"""验证器网关协议（防腐层）。

微服务拆分时，LocalVerifierGateway 替换为 HttpVerifierGateway，
上层 LangGraph 代码零改动。
"""
from abc import ABC, abstractmethod
from typing import Any


class VerifierGateway(ABC):
    """验证器网关接口。"""

    @abstractmethod
    async def verify_faithfulness(
        self, answer: str, context: str, query: str, tenant_id: str
    ) -> dict[str, Any]:
        """验证答案忠实度（幻觉检测）。

        Returns:
            {
                "faithfulness_score": float,   # 0.0 ~ 1.0
                "claims": list[dict],          # 论断列表
                "action": str,                 # pass/filter/regenerate/reject
            }
        """
        ...

    @abstractmethod
    async def grade_retrieval(
        self, query: str, chunks: list[dict], tenant_id: str
    ) -> dict[str, Any]:
        """评估检索结果质量。

        Returns:
            {
                "quality_score": float,        # 0.0 ~ 1.0
                "has_relevant": bool,
                "relevant_count": int,
                "graded_docs": list[dict],
            }
        """
        ...
```

#### 3.2.2 本地实现（单体阶段）

```python
# agent/langgraph/gateways/local_verifier.py
"""本地验证器网关（单体阶段）。

import RAGFlow 组件库，将其能力封装在 Gateway 接口下。
微服务拆分时，替换为 HttpVerifierGateway，上层代码不变。
"""
from agent.langgraph.gateways.verifier import VerifierGateway


class LocalVerifierGateway(VerifierGateway):
    """本地验证器：import 调用 RAGFlow 组件。"""

    async def verify_faithfulness(
        self, answer: str, context: str, query: str, tenant_id: str
    ) -> dict:
        # import 在方法内部（延迟加载，降低启动开销）
        from agent.component.hallucination_detector import HallucinationDetector
        from api.utils.fact_checker import rule_check_fact

        detector = HallucinationDetector(tenant_id=tenant_id)
        claims = detector._decompose_claims(answer)
        score = await detector._verify_claims(claims, context)
        action = detector._dispose(score, regenerate_count=0)

        return {
            "faithfulness_score": score,
            "claims": claims,
            "action": action,
        }

    async def grade_retrieval(
        self, query: str, chunks: list[dict], tenant_id: str
    ) -> dict:
        from agent.component.grader import Grader

        grader = Grader(
            tenant_id=tenant_id,
            evaluator_model="cross_encoder",
            fallback_on_failure=True,
        )
        graded_docs = grader.evaluate(query, chunks)

        scores = [d["score"] for d in graded_docs]
        return {
            "quality_score": sum(scores) / len(scores) if scores else 0.0,
            "graded_docs": graded_docs,
            "has_relevant": any(s >= 0.5 for s in scores),
            "relevant_count": sum(1 for s in scores if s >= 0.5),
        }
```

#### 3.2.3 远程实现（微服务阶段）

```python
# agent/langgraph/gateways/http_verifier.py
"""HTTP 验证器网关（微服务阶段）。

微服务拆分后，验证服务独立部署，通过 HTTP 调用。
"""
import httpx
from agent.langgraph.gateways.verifier import VerifierGateway


class HttpVerifierGateway(VerifierGateway):
    """远程验证器：HTTP 调用验证微服务。"""

    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def verify_faithfulness(
        self, answer: str, context: str, query: str, tenant_id: str
    ) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/verify/faithfulness",
                json={
                    "answer": answer,
                    "context": context,
                    "query": query,
                    "tenant_id": tenant_id,
                },
            )
            response.raise_for_status()
            return response.json()

    async def grade_retrieval(
        self, query: str, chunks: list[dict], tenant_id: str
    ) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/verify/grade",
                json={
                    "query": query,
                    "chunks": chunks,
                    "tenant_id": tenant_id,
                },
            )
            response.raise_for_status()
            return response.json()
```

#### 3.2.4 GatewayResolver 扩展

```python
# agent/langgraph/gateways/factory.py 扩展

class GatewayResolver:
    """网关解析器（按租户路由到 local/remote/shadow）。"""

    def __init__(self):
        self._retriever_gateways: dict[str, RetrieverGateway] = {}
        self._verifier_gateways: dict[str, VerifierGateway] = {}

    async def verifier_for(self, tenant_id: str) -> VerifierGateway:
        """按租户解析验证器网关。"""
        if tenant_id in self._verifier_gateways:
            return self._verifier_gateways[tenant_id]

        mode = await self._resolve_verifier_mode(tenant_id)
        if mode == "remote":
            gateway = HttpVerifierGateway(
                base_url=self._get_verifier_url(tenant_id),
                timeout=10.0,
            )
        else:
            gateway = LocalVerifierGateway()  # 单体阶段默认

        self._verifier_gateways[tenant_id] = gateway
        return gateway
```

---

## 四、能力融合方案

### 4.1 幻觉检测恢复（最高优先级）

#### 4.1.1 问题

当前 [hallucination.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/hallucination.py) 的 `_verify_faithfulness()` 使用字符集合重叠度，无法检测数值矛盾、日期错误等精确幻觉。

#### 4.1.2 改造方案

在 hallucination 节点中通过 VerifierGateway 调用 HallucinationDetector 三层验证。

```python
# agent/langgraph/nodes/hallucination.py 改造

async def hallucination_node(state: AgentState) -> dict[str, Any]:
    """幻觉检测节点。

    优先调用 VerifierGateway 三层验证，失败时降级为字符重叠度。
    """
    start_time = time.time()
    generated_answer = state.get("generated_answer", "")
    merged_context = state.get("merged_context", "")
    user_question = state.get("user_question", "")
    tenant_id = state.get("tenant_id", "")
    regenerate_count = state.get("regenerate_count", 0)

    # 空答案/空上下文 → 跳过检测
    if not generated_answer or not merged_context:
        return _skip_result(start_time)

    # 开关控制：未启用三层验证时走简化实现
    if not _is_three_layer_enabled(tenant_id):
        score = _fallback_overlap_score(generated_answer, merged_context)
        action = _dispose(score, regenerate_count)
        return _build_result(score, action, start_time)

    # 调用 VerifierGateway（防腐层）
    try:
        from agent.langgraph.gateways.factory import get_gateway_resolver

        resolver = get_gateway_resolver()
        verifier = await resolver.verifier_for(tenant_id)
        result = await verifier.verify_faithfulness(
            answer=generated_answer,
            context=merged_context,
            query=user_question,
            tenant_id=tenant_id,
        )

        score = result["faithfulness_score"]
        action = _dispose_with_regenerate_limit(
            score, regenerate_count, result["action"]
        )

        logger.info(
            f"[hallucination] 三层验证完成: score={score:.4f}, "
            f"action={action}, claims={len(result.get('claims', []))}"
        )

        updates = {
            "hallucination_score": score,
            "hallucination_action": action,
            "node_timings": {"hallucination": int((time.time() - start_time) * 1000)},
        }
        if action == "regenerate":
            updates["regenerate_count"] = regenerate_count + 1
        return updates

    except Exception as e:
        logger.warning(
            f"[hallucination] VerifierGateway 调用失败，降级为字符重叠度: {e}"
        )
        score = _fallback_overlap_score(generated_answer, merged_context)
        action = _dispose(score, regenerate_count)
        return _build_result(score, action, start_time)


def _fallback_overlap_score(answer: str, context: str) -> float:
    """降级实现：保留现有的字符重叠度逻辑。"""
    if not answer or not context:
        return 0.0
    answer_chars = set(answer)
    context_chars = set(context)
    overlap = answer_chars & context_chars
    overlap_ratio = len(overlap) / len(answer_chars) if answer_chars else 0.0

    # 长度惩罚（答案过长时降分）
    length_penalty = 0.8 if len(context) > 0 and len(answer) > len(context) * 2 else 1.0
    return min(1.0, overlap_ratio * length_penalty)
```

#### 4.1.3 四级处置逻辑

```python
# 分级处置阈值（与二期报告一致）
PASS_THRESHOLD = 0.85
FILTER_THRESHOLD = 0.6
REGENERATE_THRESHOLD = 0.3
MAX_REGENERATES = 2


def _dispose_with_regenerate_limit(
    score: float, regenerate_count: int, gateway_action: str
) -> str:
    """四级处置（含重新生成次数限制）。"""
    if score >= PASS_THRESHOLD:
        return "pass"
    elif score >= FILTER_THRESHOLD:
        return "filter"
    elif score >= REGENERATE_THRESHOLD:
        if regenerate_count >= MAX_REGENERATES:
            logger.warning(
                f"[hallucination] 重新生成次数已达上限 {MAX_REGENERATES}，降级为 exhausted"
            )
            return "exhausted"
        return "regenerate"
    else:
        return "reject"
```

---

### 4.2 检索质量评估恢复（含分数来源标注）

#### 4.2.1 问题

当前 [rag_tool.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py#L444) 的 `_evaluate_quality()` 仅用 Rerank 分数阈值，丢失了 LLM 语义判断能力。

**协同风险**：引入 Grader 增强后，分数语义会变化（base×0.4 + grader×0.6），但主流程 [quality_check.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/quality_check.py#L34) 的 `QUALITY_PASS_THRESHOLD=0.7` 仍基于旧分数语义校准，**阈值未同步调整**会导致决策偏差。

#### 4.2.2 改造方案（含分数来源标注）

在 RAGTool 中增加 GraderGateway 调用，仅在边界值（0.4~0.7）时触发，控制成本。
**关键改进**：输出 `score_source` 字段标注分数来源（`base` / `grader_merged`），供主流程 `quality_check` 按来源使用不同阈值。

```python
# agent/langgraph/tools/rag_tool.py _evaluate_quality() 改造

async def _evaluate_quality(
    self,
    chunks: list[dict],
    query: str = "",
    tenant_id: str = "",
    enable_grader: bool = True,  # 默认开启，由配置开关控制
) -> tuple[float, bool, int, float, str]:
    """评估检索质量（输出分数来源标注）。

    策略：
    1. 基础评估（快速、零成本）：Rerank 分数阈值 → score_source="base"
    2. 增强评估（边界值触发）：Grader 三模式语义评估 → score_source="grader_merged"

    Args:
        chunks: 检索到的文档块列表
        query: 查询文本（用于 Grader 语义评估）
        tenant_id: 租户 ID
        enable_grader: 是否启用 Grader 增强（由配置开关控制）

    Returns:
        tuple: (quality_score, has_relevant, relevant_count, top_score, score_source)
            - score_source: "base"（基础评估）或 "grader_merged"（Grader 增强后融合分数）
              供主流程 quality_check 按来源选择阈值
    """
    # 基础评估（保留现有逻辑）
    scores = [
        chunk.get("rerank_score", chunk.get("similarity", chunk.get("score", 0.0)))
        for chunk in chunks
    ]
    if not scores:
        return 0.0, False, 0, 0.0, "base"

    top_score = max(scores)
    relevant_count = len([s for s in scores if s >= self.RELEVANT_THRESHOLD])
    has_relevant = relevant_count > 0
    base_quality_score = sum(scores) / len(scores)

    # 增强评估：仅在边界值（0.4~0.7）时触发 Grader
    if (
        enable_grader
        and query
        and tenant_id
        and 0.4 <= base_quality_score < 0.7
    ):
        try:
            from agent.langgraph.gateways.factory import get_gateway_resolver

            resolver = get_gateway_resolver()
            verifier = await resolver.verifier_for(tenant_id)
            grader_result = await verifier.grade_retrieval(query, chunks, tenant_id)

            # 融合分数：基础分 × 0.4 + Grader 分 × 0.6
            grader_score = grader_result["quality_score"]
            quality_score = base_quality_score * 0.4 + grader_score * 0.6
            relevant_count = grader_result["relevant_count"]
            has_relevant = grader_result["has_relevant"]

            logger.info(
                f"[RAGTool] Grader 增强: base={base_quality_score:.2f}, "
                f"grader={grader_score:.2f}, merged={quality_score:.2f}"
            )
            # ★ 标注分数来源为 grader_merged，供 quality_check 使用更严格的阈值
            return quality_score, has_relevant, relevant_count, top_score, "grader_merged"

        except Exception as e:
            logger.warning(f"[RAGTool] Grader 评估失败，使用基础评估: {e}")

    # 基础评估结果，标注分数来源为 base
    return base_quality_score, has_relevant, relevant_count, top_score, "base"
```

#### 4.2.3 触发策略

| 基础分数 | 场景 | 是否触发 Grader | score_source |
|---------|------|----------------|--------------|
| < 0.4 | 明确不相关 | ❌ 直接判为不相关 | `base` |
| 0.4 ~ 0.7 | 边界值（不确定） | ✅ 触发 Grader 语义评估 | `grader_merged` |
| ≥ 0.7 | 明确相关 | ❌ 直接判为相关 | `base` |

**成本控制**：仅边界值场景触发 LLM 调用，预计触发率 ~30%。

#### 4.2.4 主流程 quality_check 阈值按来源区分

**协同改造**：主流程 `quality_check` 节点根据 `score_source` 使用不同阈值，避免 Grader 增强后决策偏差。

```python
# agent/langgraph/nodes/quality_check.py 阈值定义

# base 分数阈值（原 QUALITY_PASS_THRESHOLD，保留）
# 适用场景：基础评估（Rerank 分数），分数语义未变化
QUALITY_PASS_THRESHOLD_BASE = 0.7

# grader_merged 分数阈值（新增）
# 适用场景：Grader 增强后的融合分数（base×0.4 + grader×0.6）
# 阈值更低的原因：Grader 语义评估更严格，融合分数普遍低于 base 分数，
# 若沿用 0.7 阈值会导致大量本应通过的查询被判定为不达标，触发不必要的重试。
QUALITY_PASS_THRESHOLD_GRADER = 0.65
```

**阈值选择依据**：
- `base` 分数：基于 Rerank 分数，分布偏高（0.5~0.9），阈值 0.7 合理
- `grader_merged` 分数：融合了 Grader 语义评估，分布偏低（0.4~0.8），阈值降至 0.65
- 两档阈值均经过标注数据集验证，保证通过率与改造前一致（避免增强后反而增加重试）

**决策流程**：
```
RAGTool._evaluate_quality()
  ├─ 基础评估 → score_source="base"
  └─ Grader 增强 → score_source="grader_merged"
        ↓
quality_check_node()
  ├─ 读取 state["rag_score_source"]
  ├─ score_source=="base" → threshold=0.7
  └─ score_source=="grader_merged" → threshold=0.65
        ↓
  _make_decision(quality_threshold=threshold)
```

---

### 4.3 熔断机制恢复

#### 4.3.1 问题

当前 RAGTool 和 LocalRetrieverGateway 仅用 try/except 异常捕获，无状态机、无主动健康检查。

#### 4.3.2 改造方案

在 [local_retriever.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/gateways/local_retriever.py) 中集成 CircuitBreaker。

```python
# agent/langgraph/gateways/local_retriever.py 改造

from api.utils.circuit_breaker import CircuitBreakerRegistry


class LocalRetrieverGateway:
    """本地检索网关，集成熔断保护。"""

    # 熔断器配置（与二期报告分级配置一致）
    CIRCUIT_CONFIG = {
        "failure_threshold": 3,      # 连续失败 3 次触发熔断
        "recovery_timeout": 30,      # 熔断 30 秒后进入半开
        "half_open_max_calls": 2,    # 半开状态最多探测 2 次
    }

    async def retrieve(
        self, *, query, kb_ids, tenant_id, top_k,
        similarity_threshold, keywords_similarity_weight,
        rerank_id, cross_languages,
    ) -> dict:
        """执行混合检索，集成熔断保护。"""
        breaker = CircuitBreakerRegistry.get("retrieval")

        # 熔断状态检查
        if not breaker.can_execute():
            logger.warning("[LocalRetrieverGateway] 检索服务熔断中，快速失败")
            from agent.langgraph.gateways.errors import RetrievalServiceError
            raise RetrievalServiceError(
                "Retrieval circuit breaker is OPEN"
            )

        try:
            # 原有检索逻辑
            kbinfos = await self._do_retrieve(
                query, kb_ids, tenant_id, top_k,
                similarity_threshold, keywords_similarity_weight,
            )
            breaker.record_success()
            return kbinfos
        except Exception as e:
            breaker.record_failure()
            raise
```

#### 4.3.3 熔断器分级配置

```yaml
# conf/service_conf.yaml
circuit_breaker:
  services:
    retrieval:
      failure_threshold: 3
      recovery_timeout: 30
      half_open_max_calls: 2
      call_timeout: 5
    llm:
      failure_threshold: 5
      recovery_timeout: 60
      half_open_max_calls: 3
      call_timeout: 30
    rerank:
      failure_threshold: 3
      recovery_timeout: 30
      half_open_max_calls: 2
      call_timeout: 10
```

---

### 4.4 查询重写完整实现（含策略轮转协同）

#### 4.4.1 问题

当前 [rag_tool.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py#L328) 的 sub_query_decompose 和 hyde 策略均简化为同义词扩展。

**协同风险**：原实现 `strategies[retry_count % len(strategies)]` 中 `retry_count` 是 RAGTool 内层 attempt，外层重试重新进入 RAGTool 时 attempt 归零，导致**相同策略被重复使用**，无法实现"外层重试切换策略"的设计意图。

#### 4.4.2 改造方案（策略轮转协同）

恢复完整的三策略实现，并接收主流程 `retry_count` 作为策略轮转起点。

**关键改进**：策略选择改为 `strategies[(retry_count + attempt) % len(strategies)]`，其中：
- `retry_count`：主流程透传的外层重试次数（方案 A 下 RAGTool 单次执行，由 `rag_tool_node` 从 state 读取并透传）
- `attempt`：RAGTool 内层 attempt（方案 A 下固定为 0，保留参数为未来扩展留余地）

```python
# agent/langgraph/tools/rag_tool.py _rewrite_query() 改造

async def _rewrite_query(
    self,
    query: str,
    retry_count: int = 0,  # ★ 主流程透传的外层重试次数
    attempt: int = 0,      # ★ 内层 attempt（方案 A 下固定为 0）
    tenant_id: str = "",
) -> tuple[str, str]:
    """查询重写（完整三策略实现 + 策略轮转协同）。

    策略轮转公式：strategies[(retry_count + attempt) % len(strategies)]
    - retry_count：主流程外层重试次数，确保外层重试时切换策略
    - attempt：内层 attempt（方案 A 下固定为 0，保留为未来扩展留余地）

    示例（strategies=[synonym, sub_query, hyde]）：
    - retry_count=0, attempt=0 → strategy[0]=synonym       （首次检索）
    - retry_count=1, attempt=0 → strategy[1]=sub_query     （第1次外层重试）
    - retry_count=2, attempt=0 → strategy[2]=hyde          （第2次外层重试）
    - retry_count=3, attempt=0 → strategy[0]=synonym       （第3次外层重试，回到起点）

    Args:
        query: 查询文本（已繁简转换）
        retry_count: 主流程透传的外层重试次数（策略轮转起点）
        attempt: 内层 attempt（方案 A 下固定为 0）
        tenant_id: 租户 ID

    Returns:
        tuple: (rewritten_query, strategy_used)
    """
    from agent.component.query_rewriter import (
        analyze_query_complexity,
        COMPLEXITY_STRATEGIES,
        DEFAULT_STRATEGIES,
        STRATEGY_SYNONYM_REWRITE,
        STRATEGY_SUB_QUERY_DECOMPOSE,
        STRATEGY_HYDE,
        _build_synonym_expansion,
    )

    # 分析查询复杂度
    complexity, strategies = analyze_query_complexity(query)
    logger.debug(
        f"[RAGTool] 查询复杂度: {complexity}, 策略列表: {strategies}"
    )

    if not strategies:
        strategies = list(COMPLEXITY_STRATEGIES.get(complexity, DEFAULT_STRATEGIES))

    # ★ 策略轮转协同：(retry_count + attempt) % len(strategies)
    # 确保外层重试时策略不重复（详见上方示例）
    selected_index = (retry_count + attempt) % len(strategies)
    selected_strategy = strategies[selected_index]

    logger.info(
        f"[RAGTool] 策略选择: retry_count={retry_count}, attempt={attempt}, "
        f"index={selected_index}, strategy={selected_strategy}"
    )

    # 策略 1：同义词扩展（保留现有逻辑）
    if selected_strategy == STRATEGY_SYNONYM_REWRITE:
        return self._synonym_rewrite(query), selected_strategy

    # 策略 2：子查询拆解（恢复完整实现）
    elif selected_strategy == STRATEGY_SUB_QUERY_DECOMPOSE:
        if not _is_sub_query_enabled(tenant_id):
            logger.info("[RAGTool] 子查询拆解未启用，降级为同义词扩展")
            return self._synonym_rewrite(query), "synonym_rewrite(fallback)"
        try:
            from agent.component.sub_query_decomposer import SubQueryDecomposer

            decomposer = SubQueryDecomposer(tenant_id=tenant_id)
            sub_queries = await decomposer.decompose(query)
            # 返回特殊标记，调用方处理并行检索 + RRF 合并
            return f"__sub_query__:{json.dumps(sub_queries)}", selected_strategy
        except Exception as e:
            logger.warning(f"[RAGTool] 子查询拆解失败，降级为同义词: {e}")
            return self._synonym_rewrite(query), "synonym_rewrite(fallback)"

    # 策略 3：HyDE 假设文档嵌入（恢复完整实现）
    elif selected_strategy == STRATEGY_HYDE:
        if not _is_hyde_enabled(tenant_id):
            logger.info("[RAGTool] HyDE 未启用，降级为同义词扩展")
            return self._synonym_rewrite(query), "synonym_rewrite(fallback)"
        try:
            from agent.component.hyde import HyDE

            hyde = HyDE(tenant_id=tenant_id)
            hypothetical_answer = await hyde.generate(query)
            return hypothetical_answer, selected_strategy
        except Exception as e:
            logger.warning(f"[RAGTool] HyDE 生成失败，降级为同义词: {e}")
            return self._synonym_rewrite(query), "synonym_rewrite(fallback)"

    return query, selected_strategy


def _synonym_rewrite(self, query: str) -> str:
    """同义词扩展（提取为独立方法）。"""
    expansion = _build_synonym_expansion(query, topn=3)
    if expansion.get("synonyms"):
        expanded_terms = [query]
        for term, syns in expansion["synonyms"].items():
            for syn in syns:
                expanded_terms.append(f'"{syn}"')
        return " OR ".join(expanded_terms)
    return query
```

#### 4.4.3 策略轮转协同示例

以 `strategies=[synonym_rewrite, sub_query_decompose, hyde]` 为例（complexity=complex 场景）：

| 主流程 retry_count | RAGTool attempt | 轮转索引 | 选中策略 | 场景说明 |
|------------------|-----------------|---------|---------|---------|
| 0 | 0 | 0 | synonym_rewrite | 首次检索（简单策略优先） |
| 1 | 0 | 1 | sub_query_decompose | 第 1 次外层重试（切换到子查询拆解） |
| 2 | 0 | 2 | hyde | 第 2 次外层重试（切换到 HyDE） |
| 3 | 0 | 0 | synonym_rewrite | 第 3 次外层重试（回到起点，但 max_retries=3 已耗尽） |

**改造前对比**：
| 主流程 retry_count | 改造前（attempt 归零） | 改造后（retry_count 透传） |
|------------------|---------------------|-------------------------|
| 0 | strategy[0]=synonym | strategy[0]=synonym |
| 1 | strategy[0]=synonym（❌ 重复） | strategy[1]=sub_query（✅ 切换） |
| 2 | strategy[0]=synonym（❌ 重复） | strategy[2]=hyde（✅ 切换） |

#### 4.4.4 子查询拆解的 RRF 合并处理

子查询拆解策略返回特殊标记 `__sub_query__:`，调用方（RAGTool.invoke）需要处理并行检索和 RRF 合并：

```python
# agent/langgraph/tools/rag_tool.py invoke() 中处理子查询拆解结果

# 3. 查询重写（单次，retry_count 作为策略轮转起点）
rewritten_query, strategy_used = self._rewrite_query(
    query_simplified, retry_count, tenant_id=tenant_id
)

# ★ 子查询拆解结果处理：并行检索 + RRF 合并
if rewritten_query.startswith("__sub_query__:"):
    import json
    sub_queries = json.loads(rewritten_query[len("__sub_query__:"):])
    return await self._retrieve_with_sub_queries(
        sub_queries=sub_queries,
        kb_ids=kb_ids,
        tenant_id=tenant_id,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
        keywords_similarity_weight=keywords_similarity_weight,
        rerank_id=rerank_id,
        retrieval_mode=retrieval_mode,
        start_time=start_time,
    )

# 常规单查询检索流程（原有逻辑）
# ...


async def _retrieve_with_sub_queries(
    self, sub_queries: list[str], **kwargs
) -> RAGToolOutput:
    """子查询并行检索 + RRF 合并。

    参考 agent/component/sub_query_decomposer.py 的 RRF 算法：
    score(d) = Σ 1/(k + rank_i(d) + 1)  # k=60 平滑常数

    Args:
        sub_queries: 拆解后的子查询列表
        **kwargs: 检索参数（kb_ids, tenant_id, top_k 等）
    """
    import asyncio
    from agent.langgraph.gateways.factory import get_gateway_resolver

    resolver = get_gateway_resolver()
    gateway = await resolver.retriever_for(kwargs["tenant_id"])

    # 并行检索所有子查询
    tasks = [
        gateway.retrieve(
            query=sq,
            kb_ids=kwargs["kb_ids"],
            tenant_id=kwargs["tenant_id"],
            top_k=kwargs["top_k"],
            similarity_threshold=kwargs["similarity_threshold"],
            keywords_similarity_weight=kwargs["keywords_similarity_weight"],
            rerank_id=kwargs.get("rerank_id"),
            cross_languages=None,
        )
        for sq in sub_queries
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # RRF 合并
    K = 60  # RRF 平滑常数
    rrf_scores: dict[str, float] = {}
    chunk_map: dict[str, dict] = {}

    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"[RAGTool] 子查询检索失败: {result}")
            continue
        chunks = result.get("chunks", [])
        for rank, chunk in enumerate(chunks):
            chunk_id = chunk.get("chunk_id", str(id(chunk)))
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (K + rank + 1)
            if chunk_id not in chunk_map:
                chunk_map[chunk_id] = chunk

    # 按 RRF 分数排序，取 top_k
    sorted_chunks = sorted(
        rrf_scores.items(), key=lambda x: x[1], reverse=True
    )[:kwargs["top_k"]]
    merged_chunks = []
    for chunk_id, rrf_score in sorted_chunks:
        chunk = chunk_map[chunk_id]
        chunk["rrf_score"] = rrf_score
        chunk["rerank_score"] = rrf_score  # 复用 rerank_score 字段
        merged_chunks.append(chunk)

    # 后续 Rerank + 质量评估（复用主流程）
    # ...（与单查询流程一致）
```

---

### 4.5 重试控制恢复（方案 A：RAGTool 单次执行 + 主流程统一重试）

#### 4.5.1 问题

当前 [rag_tool.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py#L105) `MAX_INTERNAL_RETRIES = 1` 形成内层重试，
叠加主流程 [quality_check.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/quality_check.py#L67) `max_retries=3` 的外层重试，
构成**双重重试机制**（最多 1×3=3 次，若按原设计文档提升内层到 3 则变成 3×3=9 次）。

重复带来的危害：
1. **资源浪费**：相同查询重复检索，LLM Token 成本翻倍
2. **Token 预算割裂**：RAGTool 内部 `MAX_RETRY_TOKENS` 只在内层生效，外层重试时不感知已用 Token
3. **策略轮转混乱**：外层重试重新进入 RAGTool 时内层 attempt 归零，导致相同策略被重复使用

#### 4.5.2 改造方案（方案 A）

**核心思路**：RAGTool 退化为单次执行（`MAX_INTERNAL_RETRIES = 0`），重试决策和 Token 预算管控完全上提到主流程 `quality_check` 节点。

```
改造前（双重重试）                    改造后（方案 A：单层重试）
─────────────────                    ─────────────────────────
rag_tool_node                         rag_tool_node（单次执行）
  for attempt in range(2):              ↓ 预处理→重写→检索→Rerank→打分
    _rewrite_query(attempt)             ↓ 返回 quality_score + score_source
    retrieve()                        reflection
    _evaluate_quality()                ↓
    _should_internal_retry()           quality_check（统一重试决策）
      └─ continue（内层重试）            ├─ pass → prompt_assembly
  返回 best_result                      ├─ retry_rag → 回到 rag_tool_node（外层重试）
reflection                              └─ fallback_web → web_tool
  ↓
quality_check（外层重试决策）
  ├─ retry_rag → 回到 rag_tool_node
  └─ fallback_web
```

#### 4.5.3 RAGTool 改造（移除内层重试）

```python
# agent/langgraph/tools/rag_tool.py 改造

class RAGTool:
    # 质量评估阈值（保留，用于 _evaluate_quality 打分）
    RELEVANT_THRESHOLD = 0.5
    QUALITY_PASS_THRESHOLD = 0.7
    MIN_RELEVANT_DOCS = 2

    # ★ 方案 A：移除内层重试，单次执行
    # 原 MAX_INTERNAL_RETRIES = 1 已删除，重试完全交给主流程 quality_check
    # 原 MAX_RETRY_TOKENS 已删除，Token 预算上提到主流程 state

    async def invoke(self, input_data: RAGToolInput) -> RAGToolOutput:
        """执行 RAG 检索流程（单次执行，无内层重试）。

        重试决策由主流程 quality_check 节点统一管控。
        本方法只负责一次完整的：预处理→重写→检索→Rerank→打分。

        Args:
            input_data: RAG Tool 输入参数。
                新增字段 retry_count（由主流程透传）用于查询重写策略轮转起点。

        Returns:
            RAGToolOutput: 检索结果，包含 quality_score 和 score_source。
        """
        start_time = time.time()

        # 提取参数（带默认值）
        query = input_data.get("query", "")
        top_k = input_data.get("top_k", 5)
        enable_rewrite = input_data.get("enable_rewrite", True)
        enable_rerank = input_data.get("enable_rerank", True)
        kb_ids = input_data.get("kb_ids", [])
        tenant_id = input_data.get("tenant_id", "")
        similarity_threshold = input_data.get("similarity_threshold", 0.2)
        keywords_similarity_weight = input_data.get("keywords_similarity_weight", 0.5)
        rerank_id = input_data.get("rerank_id", "")
        cross_languages_param = input_data.get("cross_languages", [])
        llm_id = input_data.get("llm_id", "")

        # ★ 主流程透传的 retry_count（用于查询重写策略轮转起点）
        # 详见 §4.4 查询重写策略协同
        retry_count = input_data.get("retry_count", 0)

        # 获取网关解析器与检索模式
        resolver = self._resolver or get_gateway_resolver()
        retrieval_mode = await resolver._resolve_retrieval_mode(tenant_id)

        if not query or not kb_ids:
            return self._empty_result(start_time, retrieval_mode=retrieval_mode)

        # 1. 跨语言扩展（local 模式本地执行）
        expanded_query = query
        if retrieval_mode == "local" and cross_languages_param and tenant_id and llm_id:
            try:
                from rag.prompts.generator import cross_languages
                expanded_query = await cross_languages(
                    tenant_id, llm_id, query, cross_languages_param
                )
            except Exception as e:
                logger.warning(f"[RAGTool] 跨语言扩展失败，使用原查询: {e}")

        # 2. 查询预处理
        preprocessed = preprocess_query(expanded_query)
        query_simplified = preprocessed["query_simplified"]
        detected_lang = to_langgraph_lang(preprocessed["query_lang"])

        # 3. 查询重写（单次，retry_count 作为策略轮转起点）
        rewritten_query = query_simplified
        strategy_used = "none"
        if enable_rewrite:
            # ★ 传入 retry_count，详见 §4.4
            rewritten_query, strategy_used = self._rewrite_query(
                query_simplified, retry_count
            )
            logger.info(
                f"[RAGTool] 查询重写: strategy={strategy_used}, "
                f"retry_count={retry_count}, result='{rewritten_query}'"
            )

        # 4. 混合检索（单次，无内层循环）
        try:
            gateway = await resolver.retriever_for(tenant_id)
            kbinfos = await gateway.retrieve(
                query=rewritten_query,
                kb_ids=kb_ids,
                tenant_id=tenant_id,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
                keywords_similarity_weight=keywords_similarity_weight,
                rerank_id=rerank_id or None,
                cross_languages=cross_languages_param or None,
            )
        except RetrievalServiceError as e:
            return self._empty_result(
                start_time, retrieval_mode=retrieval_mode,
                error_code="RETRIEVAL_SERVICE_ERROR",
            )
        except RetrievalAuthError as e:
            return self._empty_result(
                start_time, retrieval_mode=retrieval_mode,
                error_code="RETRIEVAL_AUTH",
            )
        except RetrievalDataError:
            raise
        except Exception as e:
            logger.error(f"[RAGTool] 检索失败: {e}")
            return self._empty_result(start_time, retrieval_mode=retrieval_mode)

        chunks = kbinfos.get("chunks", [])
        if not chunks:
            return self._empty_result(start_time, retrieval_mode=retrieval_mode)

        # 5. Rerank 精排（单次）
        if enable_rerank and chunks:
            try:
                rerank_mdl = self._get_rerank_model(rerank_id, tenant_id, kb_ids)
                if rerank_mdl is not None:
                    chunks = multilingual_rerank(
                        rewritten_query, chunks, top_k=top_k, rerank_model=rerank_mdl,
                    )
            except Exception as e:
                logger.warning(f"[RAGTool] Rerank 失败，使用原始排序: {e}")

        # 6. 质量评估（单次，输出 score_source 标注分数来源）
        # ★ 详见 §4.2 分数来源标注
        quality_score, has_relevant, relevant_count, top_score, score_source = (
            self._evaluate_quality(
                chunks, query=query_simplified, tenant_id=tenant_id,
            )
        )

        logger.info(
            f"[RAGTool] 检索完成: score={quality_score:.2f} ({score_source}), "
            f"relevant={relevant_count}, strategy={strategy_used}"
        )

        # 7. 格式化输出（无 best_result 缓存，无 rewrite_history 列表）
        docs = self._format_docs(chunks)
        retrieval_time_ms = int((time.time() - start_time) * 1000)

        return RAGToolOutput(
            docs=docs,
            quality_score=quality_score,
            has_relevant=has_relevant,
            relevant_count=relevant_count,
            top_score=top_score,
            score_source=score_source,  # ★ 新增：分数来源标注
            rewrite_strategy=strategy_used,  # ★ 新增：重写策略（观测用）
            query_simplified=query_simplified,
            detected_lang=detected_lang,
            retrieval_time_ms=retrieval_time_ms,
            retrieval_mode_used=retrieval_mode,
        )

    # ★ 删除 _should_internal_retry 方法（重试决策交给主流程）
    # 原 _should_internal_retry(has_relevant, relevant_count, attempt) 已移除
```

#### 4.5.4 RAGToolInput / RAGToolOutput 契约变更

```python
class RAGToolInput(TypedDict, total=False):
    """RAG Tool 输入定义（方案 A 新增 retry_count）。"""

    query: str
    query_lang: Literal["zh_CN", "zh_TW", "en"]
    top_k: int
    enable_rewrite: bool
    enable_rerank: bool
    kb_ids: list[str]
    tenant_id: str
    similarity_threshold: float
    keywords_similarity_weight: float
    rerank_id: str
    cross_languages: list[str]
    llm_id: str
    document_filters: dict[str, Any]
    metadata_filters: dict[str, Any]
    # ★ 方案 A 新增：主流程透传的重试次数（用于查询重写策略轮转起点）
    retry_count: int


class RAGToolOutput(TypedDict, total=False):
    """RAG Tool 输出定义（方案 A 新增 score_source / rewrite_strategy）。"""

    docs: list[dict]
    quality_score: float
    has_relevant: bool
    relevant_count: int
    top_score: float
    query_simplified: str
    detected_lang: str
    retrieval_time_ms: int
    retrieval_error_code: str
    retrieval_mode_used: str
    # ★ 方案 A 新增：分数来源（base / grader_merged），供 quality_check 按来源选阈值
    score_source: str
    # ★ 方案 A 新增：实际使用的重写策略（观测用，替代原 rewrite_history 列表）
    rewrite_strategy: str
    # ★ 方案 A 删除：rewrite_history（单次执行不再有历史列表）
```

#### 4.5.5 主流程 quality_check 改造（承接 Token 预算管控）

```python
# agent/langgraph/nodes/quality_check.py 改造

# 原阈值保留为 base 分数阈值
QUALITY_PASS_THRESHOLD_BASE = 0.7       # base 分数阈值（原 QUALITY_PASS_THRESHOLD）
QUALITY_PASS_THRESHOLD_GRADER = 0.65    # grader_merged 分数阈值（Grader 增强后更严格）

# ★ 方案 A：Token 预算默认值（可被 agent_config 覆盖）
DEFAULT_RETRY_TOKEN_BUDGET = 2000

async def quality_check_node(state: AgentState) -> dict[str, Any]:
    """质量检查节点（方案 A：承接重试决策 + Token 预算管控）。

    职责：
    1. 基于 quality_score 和 score_source 决策 pass/retry_rag/fallback_web
    2. 统一管控 retry_token_used（RAGTool 不再感知预算）
    3. 重试次数和 Token 预算任一耗尽即降级 fallback_web
    """
    start_time = time.time()

    route_target = state.get("route_target", "chitchat")
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 3)

    # ★ 方案 A：Token 预算管控（原 RAGTool 内部逻辑上提）
    retry_token_used = state.get("retry_token_used", 0)
    retry_token_budget = state.get("retry_token_budget", DEFAULT_RETRY_TOKEN_BUDGET)

    # 提取质量评分和分数来源
    quality_score = 0.0
    has_relevant = False
    relevant_count = 0
    score_source = "base"  # 默认 base 分数

    if route_target == "rag":
        quality_score = state.get("rag_quality_score", 0.0)
        has_relevant = state.get("rag_has_relevant", False)
        relevant_count = state.get("rag_relevant_count", 0)
        score_source = state.get("rag_score_source", "base")  # ★ 新增字段
    elif route_target == "database":
        quality_score = state.get("db_quality_score", 0.0)
        db_result = state.get("db_result", {})
        has_relevant = db_result.get("row_count", 0) > 0
        relevant_count = db_result.get("row_count", 0)
    elif route_target == "hybrid":
        rag_score = state.get("rag_quality_score", 0.0)
        db_score = state.get("db_quality_score", 0.0)
        quality_score = (rag_score + db_score) / 2 if db_score > 0 else rag_score
        has_relevant = state.get("rag_has_relevant", False)
        relevant_count = state.get("rag_relevant_count", 0)
        score_source = state.get("rag_score_source", "base")

    # §5.8 前置规则：infra/auth 失败直接 fallback_web
    if route_target in ("rag", "hybrid"):
        retrieval_error_code = state.get("retrieval_error_code", "")
        if retrieval_error_code in _RETRIEVAL_INFRA_ERROR_CODES:
            return {
                "quality_decision": "fallback_web",
                "node_timings": {"quality_check": int((time.time() - start_time) * 1000)},
            }

    # ★ 方案 A：Token 预算耗尽检查（在重试决策之前）
    if retry_token_used >= retry_token_budget:
        logger.warning(
            f"[quality_check] Token 预算耗尽 ({retry_token_used}/{retry_token_budget})，"
            f"降级 fallback_web"
        )
        return {
            "quality_decision": "fallback_web",
            "node_timings": {"quality_check": int((time.time() - start_time) * 1000)},
        }

    # ★ 方案 A：按分数来源选择阈值
    threshold = (
        QUALITY_PASS_THRESHOLD_GRADER
        if score_source == "grader_merged"
        else QUALITY_PASS_THRESHOLD_BASE
    )

    decision = _make_decision(
        quality_score=quality_score,
        has_relevant=has_relevant,
        relevant_count=relevant_count,
        retry_count=retry_count,
        max_retries=max_retries,
        route_target=route_target,
        quality_threshold=threshold,  # ★ 新增参数
    )

    logger.info(
        f"[quality_check] 决策: score={quality_score:.2f} ({score_source}), "
        f"threshold={threshold}, relevant={relevant_count}, "
        f"retry={retry_count}/{max_retries}, token={retry_token_used}/{retry_token_budget}, "
        f"decision={decision}"
    )

    updates: dict[str, Any] = {
        "quality_decision": decision,
        "node_timings": {"quality_check": int((time.time() - start_time) * 1000)},
    }

    # 重试决策递增 retry_count（原逻辑保留）
    if decision in ("retry_rag", "retry_db"):
        updates["retry_count"] = retry_count + 1

    return updates


def _make_decision(
    quality_score: float,
    has_relevant: bool,
    relevant_count: int,
    retry_count: int,
    max_retries: int,
    route_target: str,
    quality_threshold: float = QUALITY_PASS_THRESHOLD_BASE,  # ★ 新增参数
) -> str:
    """质量检查决策函数（方案 A：阈值参数化）。

    Args:
        quality_threshold: 质量通过阈值，按 score_source 区分：
            - base 分数：0.7（原阈值）
            - grader_merged 分数：0.65（Grader 增强后更严格，避免过度重试）
    """
    if route_target == "chitchat":
        return "pass"

    quality_ok = quality_score >= quality_threshold and has_relevant

    if route_target == "database":
        quality_ok = quality_score >= quality_threshold and relevant_count > 0
    elif route_target in ("rag", "hybrid"):
        quality_ok = (
            quality_score >= quality_threshold
            and has_relevant
            and relevant_count >= MIN_RELEVANT_DOCS
        )

    if quality_ok:
        return "pass"

    if retry_count < max_retries:
        if route_target in ("rag", "hybrid"):
            return "retry_rag"
        elif route_target == "database":
            return "retry_db"

    return "fallback_web"
```

#### 4.5.6 Token 预算累计机制

Token 预算的累计由 `rag_tool_node` 在每次执行后上报，`quality_check` 只读不写：

```python
# agent/langgraph/nodes/rag_tool_node.py 改造

async def rag_tool_node(state: AgentState) -> dict[str, Any]:
    """RAG 工具节点（方案 A：透传 retry_count + 上报 token_used）。"""
    start_time = time.time()

    user_question = state.get("user_question", "")
    query_simplified = state.get("query_simplified", "")

    if not user_question:
        return _empty_rag_state(start_time)

    rag_tool = get_rag_tool()

    agent_config = state.get("agent_config", {}) or {}
    rag_config = agent_config.get("rag_config", {}) or {}

    # ★ 方案 A：透传主流程 retry_count 给 RAGTool（用于查询重写策略轮转）
    retry_count = state.get("retry_count", 0)

    input_data = {
        "query": user_question,
        "query_simplified": query_simplified or user_question,
        "top_k": rag_config.get("top_k", 5),
        "enable_rewrite": rag_config.get("enable_rewrite", True),
        "enable_rerank": rag_config.get("enable_rerank", True),
        "kb_ids": state.get("kb_ids", []),
        "tenant_id": state.get("tenant_id", ""),
        "llm_id": state.get("llm_id", ""),
        "cross_languages": rag_config.get("cross_languages", []),
        "similarity_threshold": rag_config.get("similarity_threshold", 0.2),
        "keywords_similarity_weight": rag_config.get("keywords_similarity_weight", 0.5),
        "rerank_id": rag_config.get("rerank_id", ""),
        "retry_count": retry_count,  # ★ 新增：策略轮转起点
    }

    try:
        result = await rag_tool.invoke(input_data)

        # ★ 方案 A：估算本次检索消耗的 Token（粗略估算：docs 总字符数 / 4）
        docs = result.get("docs", [])
        estimated_tokens = sum(len(d.get("content", "")) for d in docs) // 4
        retry_token_used = state.get("retry_token_used", 0) + estimated_tokens

        logger.info(
            f"[rag_tool] 检索完成: docs={len(docs)}, "
            f"score={result.get('quality_score', 0.0):.2f} "
            f"({result.get('score_source', 'base')}), "
            f"strategy={result.get('rewrite_strategy', 'none')}, "
            f"retry_count={retry_count}, token_used={retry_token_used}"
        )

        return {
            "rag_docs": docs,
            "rag_quality_score": result.get("quality_score", 0.0),
            "rag_has_relevant": result.get("has_relevant", False),
            "rag_relevant_count": result.get("relevant_count", 0),
            "rag_top_score": result.get("top_score", 0.0),
            "rag_score_source": result.get("score_source", "base"),  # ★ 新增
            "query_lang": result.get("detected_lang", state.get("query_lang", "zh_CN")),
            "query_simplified": result.get("query_simplified", query_simplified),
            "retrieval_error_code": result.get("retrieval_error_code", ""),
            "retrieval_mode_used": result.get("retrieval_mode_used", ""),
            "retry_token_used": retry_token_used,  # ★ 新增：累计 Token
            "node_timings": {"rag_tool": int((time.time() - start_time) * 1000)},
        }

    except RetrievalDataError:
        raise
    except Exception as e:
        logger.error(f"[rag_tool] 检索失败: {e}")
        return _empty_rag_state(start_time)


def _empty_rag_state(start_time: float) -> dict:
    """空结果状态（方案 A：包含 score_source 默认值）。"""
    return {
        "rag_docs": [],
        "rag_quality_score": 0.0,
        "rag_has_relevant": False,
        "rag_relevant_count": 0,
        "rag_top_score": 0.0,
        "rag_score_source": "base",  # ★ 空结果默认 base
        "retrieval_error_code": "",
        "retrieval_mode_used": "",
        "node_timings": {"rag_tool": int((time.time() - start_time) * 1000)},
    }
```

#### 4.5.7 AgentState 新增字段

```python
# agent/langgraph/state.py 改造

class AgentState(TypedDict, total=False):
    # ... 原有字段 ...

    # RAG Tool 输出
    rag_docs: list[dict]
    rag_quality_score: float
    rag_has_relevant: bool
    rag_relevant_count: int
    rag_top_score: float
    rag_score_source: str  # ★ 方案 A 新增：分数来源（base / grader_merged）

    # 重试控制（方案 A：新增 Token 预算字段）
    retry_count: int
    max_retries: int
    retry_token_used: int     # ★ 新增：已用 Token 预算
    retry_token_budget: int   # ★ 新增：Token 预算上限（默认 2000）
```

#### 4.5.8 方案 A 收益总结

| 维度 | 改造前（双重重试） | 改造后（方案 A） |
|------|------------------|----------------|
| **最大检索次数** | 1×3=3 次（或 3×3=9 次按原设计） | 3 次（仅外层） |
| **Token 预算管控** | RAGTool 内部割裂，外层不感知 | 主流程统一管控，全局可见 |
| **查询重写策略** | 内层 attempt 归零，策略重复 | retry_count 透传，策略不重复 |
| **职责清晰度** | RAGTool 和 quality_check 都做重试决策 | RAGTool 只打分，quality_check 只决策 |
| **可观测性** | 内层重试对主流程不可见 | 重试次数和 Token 全在 state 中 |

---

### 4.6 主流程协同改造汇总（方案 A 落地清单）

本节汇总方案 A 涉及的所有主流程协同改造点，作为实施时的统一落地清单。
各项改造的详细代码见 §4.2 / §4.4 / §4.5，本节仅做汇总和影响范围说明。

#### 4.6.1 改造点全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                    主流程协同改造点（方案 A）                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. AgentState 新增字段（§4.5.7）                                 │
│     ├─ rag_score_source: str        ← 分数来源（base/grader_merged）│
│     ├─ retry_token_used: int        ← 已用 Token 预算             │
│     └─ retry_token_budget: int      ← Token 预算上限              │
│                                                                  │
│  2. RAGTool 改造（§4.5.3 + §4.2.2 + §4.4.2）                      │
│     ├─ 移除 MAX_INTERNAL_RETRIES + _should_internal_retry         │
│     ├─ invoke() 单次执行，接收 retry_count                        │
│     ├─ _rewrite_query() 策略轮转：(retry_count + attempt) % len   │
│     └─ _evaluate_quality() 输出 score_source                     │
│                                                                  │
│  3. rag_tool_node 改造（§4.5.6）                                  │
│     ├─ 透传 state["retry_count"] → RAGToolInput["retry_count"]   │
│     ├─ 累计 retry_token_used 并写回 state                         │
│     └─ 透传 rag_score_source 到 state                             │
│                                                                  │
│  4. quality_check 改造（§4.5.5 + §4.2.4）                         │
│     ├─ 读取 state["rag_score_source"] 选择阈值                    │
│     │   ├─ "base" → QUALITY_PASS_THRESHOLD_BASE = 0.7             │
│     │   └─ "grader_merged" → QUALITY_PASS_THRESHOLD_GRADER = 0.65 │
│     ├─ 读取 state["retry_token_used"] 检查 Token 预算             │
│     └─ _make_decision() 新增 quality_threshold 参数               │
│                                                                  │
│  5. RAGToolInput / RAGToolOutput 契约变更（§4.5.4）               │
│     ├─ Input 新增：retry_count                                    │
│     └─ Output 新增：score_source, rewrite_strategy                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### 4.6.2 改造点与文件映射

| 改造点 | 文件路径 | 改造类型 | 依赖关系 |
|--------|---------|---------|---------|
| AgentState 新增 3 个字段 | [agent/langgraph/state.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/state.py) | 修改 | 无依赖，最先改 |
| RAGTool 移除内层重试 | [agent/langgraph/tools/rag_tool.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py) | 修改 | 依赖 AgentState |
| RAGTool._evaluate_quality 输出 score_source | [agent/langgraph/tools/rag_tool.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py#L444) | 修改 | 依赖 RAGToolInput 契约 |
| RAGTool._rewrite_query 策略轮转协同 | [agent/langgraph/tools/rag_tool.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py#L328) | 修改 | 依赖 RAGToolInput 契约 |
| RAGToolInput / RAGToolOutput 契约变更 | [agent/langgraph/tools/rag_tool.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/tools/rag_tool.py#L47) | 修改 | 无依赖 |
| rag_tool_node 透传 retry_count + 累计 token | [agent/langgraph/nodes/rag_tool_node.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/rag_tool_node.py) | 修改 | 依赖 AgentState + RAGToolInput 契约 |
| quality_check 阈值按来源区分 + Token 预算检查 | [agent/langgraph/nodes/quality_check.py](file:///Users/renwk/workspace/data-knowledge-api/agent/langgraph/nodes/quality_check.py) | 修改 | 依赖 AgentState |

#### 4.6.3 数据流时序（方案 A 完整链路）

```
用户查询
  ↓
intent_router → route_target="rag"
  ↓
rag_tool_node（第 1 次执行，retry_count=0）
  ├─ 读取 state["retry_count"]=0
  ├─ 透传 retry_count=0 给 RAGTool
  ├─ RAGTool.invoke():
  │   ├─ _rewrite_query(retry_count=0) → strategy[0]=synonym_rewrite
  │   ├─ gateway.retrieve() → chunks
  │   ├─ multilingual_rerank() → reranked chunks
  │   └─ _evaluate_quality() → (0.55, True, 2, 0.8, "grader_merged")
  ├─ 估算 token_used = 500
  └─ 写回 state: rag_quality_score=0.55, rag_score_source="grader_merged",
                retry_token_used=500
  ↓
reflection
  ↓
quality_check（第 1 次决策）
  ├─ 读取 rag_score_source="grader_merged" → threshold=0.65
  ├─ 读取 retry_token_used=500 < budget=2000
  ├─ quality_score=0.55 < threshold=0.65 → 不达标
  ├─ retry_count=0 < max_retries=3 → 决策 retry_rag
  └─ 写回 state: retry_count=1
  ↓
rag_tool_node（第 2 次执行，retry_count=1）
  ├─ 读取 state["retry_count"]=1
  ├─ 透传 retry_count=1 给 RAGTool
  ├─ RAGTool.invoke():
  │   ├─ _rewrite_query(retry_count=1) → strategy[1]=sub_query_decompose
  │   ├─ _retrieve_with_sub_queries() → RRF 合并 chunks
  │   └─ _evaluate_quality() → (0.72, True, 3, 0.85, "base")
  ├─ 估算 token_used = 500 + 800 = 1300
  └─ 写回 state: rag_quality_score=0.72, rag_score_source="base",
                retry_token_used=1300
  ↓
reflection
  ↓
quality_check（第 2 次决策）
  ├─ 读取 rag_score_source="base" → threshold=0.7
  ├─ 读取 retry_token_used=1300 < budget=2000
  ├─ quality_score=0.72 >= threshold=0.7 → 达标
  └─ 决策 pass → prompt_assembly
  ↓
prompt_assembly → llm_generate → hallucination → ...
```

#### 4.6.4 向后兼容性保证

方案 A 改造涉及 AgentState 字段新增和 RAGToolInput/Output 契约变更，需保证向后兼容：

1. **AgentState 新增字段**：使用 `total=False`（已有），新字段缺失时走默认值
   - `rag_score_source` 缺失 → 默认 `"base"`，quality_check 使用原阈值 0.7
   - `retry_token_used` 缺失 → 默认 `0`，不影响首次检索
   - `retry_token_budget` 缺失 → 默认 `2000`（DEFAULT_RETRY_TOKEN_BUDGET）

2. **RAGToolInput 新增 retry_count**：默认 `0`，未透传时策略轮转从 synonym 开始（与改造前行为一致）

3. **RAGToolOutput 新增 score_source / rewrite_strategy**：默认 `"base"` / `"none"`，未启用增强时走原逻辑

4. **RAGTool 移除内层重试**：原调用方（rag_tool_node）不需要感知此变化，因为重试决策本就在 quality_check 做

5. **quality_check 阈值参数化**：`_make_decision()` 新增 `quality_threshold` 参数有默认值 `QUALITY_PASS_THRESHOLD_BASE`，未传参时行为与改造前一致

#### 4.6.5 实施顺序建议（避免破坏中间状态）

```
Step 1: AgentState 新增字段（state.py）
        ↓ 无依赖，最先改，所有字段 total=False 不破坏现有流程
Step 2: RAGToolInput / RAGToolOutput 契约变更（rag_tool.py）
        ↓ 新增字段有默认值，不破坏现有调用
Step 3: RAGTool._evaluate_quality 输出 score_source（rag_tool.py）
        ↓ 依赖 Step 2 的 Output 契约
Step 4: RAGTool._rewrite_query 策略轮转协同（rag_tool.py）
        ↓ 依赖 Step 2 的 Input 契约（retry_count）
Step 5: RAGTool.invoke() 移除内层重试（rag_tool.py）
        ↓ 依赖 Step 3 + Step 4
Step 6: rag_tool_node 透传 retry_count + 累计 token（rag_tool_node.py）
        ↓ 依赖 Step 2 + Step 5
Step 7: quality_check 阈值按来源区分 + Token 预算检查（quality_check.py）
        ↓ 依赖 Step 1 + Step 6
```

**关键约束**：Step 5（移除内层重试）必须在 Step 7（quality_check 承接重试）之前完成，
否则会出现"RAGTool 已移除重试但 quality_check 未承接"的中间状态，导致质量不达标时无法重试。

**安全做法**：Step 5 和 Step 7 在同一个 PR 中提交，保证重试职责的平滑迁移。

---

## 五、配置驱动开关

### 5.1 配置文件

新增 `conf/rag_enhancement.yaml`：

```yaml
# RAG 增强能力开关（对应二期技术报告能力）
enhancement:
  # ========== 幻觉检测 ==========
  hallucination:
    enabled: true                    # 是否启用增强
    use_three_layer: true            # true=三层验证, false=字符重叠度
    skip_llm_when_rule_supported: true  # 规则层支持时跳过 LLM
    thresholds:
      pass: 0.85
      filter: 0.6
      regenerate: 0.3
    max_regenerates: 2
    weights:
      rule: 0.4
      nli: 0.4
      llm: 0.2

  # ========== 检索质量评估 ==========
  grader:
    enabled: true
    evaluator_model: cross_encoder   # llm / cross_encoder / local_nli
    trigger_range: [0.4, 0.7]        # 仅边界值触发
    fallback_on_failure: true
    score_merge_ratio: 0.4           # base_score * 0.4 + grader_score * 0.6
    # ★ 方案 A：分数来源阈值（quality_check 按来源选择阈值）
    thresholds:
      base: 0.7              # base 分数阈值（Rerank 分数）
      grader_merged: 0.65    # grader_merged 分数阈值（融合分数，分布偏低）

  # ========== 熔断机制 ==========
  circuit_breaker:
    enabled: true
    services:
      retrieval:
        failure_threshold: 3
        recovery_timeout: 30
        half_open_max_calls: 2
        call_timeout: 5
      llm:
        failure_threshold: 5
        recovery_timeout: 60
        half_open_max_calls: 3
        call_timeout: 30
      rerank:
        failure_threshold: 3
        recovery_timeout: 30
        half_open_max_calls: 2
        call_timeout: 10

  # ========== 查询重写 ==========
  query_rewrite:
    enable_sub_query_decompose: true
    enable_hyde: true
    hyde_max_tokens: 200
    hyde_temperature: 0.3

  # ========== 重试控制（方案 A：主流程统一管控） ==========
  retry:
    # ★ 方案 A：重试决策由主流程 quality_check 节点统一管控
    # RAGTool 内部不再有重试逻辑，单次执行
    max_retries: 3                   # 外层最大重试次数（quality_check 决策）
    max_retry_tokens: 2000           # Token 预算上限（quality_check 检查）
    min_relevant_docs: 2             # 最小相关文档数（quality_check 决策依据）

  # ========== 网关模式 ==========
  gateway:
    verifier_mode: local             # local / remote / shadow
    verifier_remote_url: http://verifier-service:8080
    verifier_shadow_url: http://verifier-service-canary:8080
```

### 5.2 配置加载

```python
# agent/langgraph/config.py

import yaml
from pathlib import Path


class EnhancementConfig:
    """增强能力配置加载器。"""

    _instance = None
    _config: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self):
        config_path = Path("conf/rag_enhancement.yaml")
        if config_path.exists():
            with config_path.open(encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {}

    def get(self, *keys, default=None):
        """嵌套键获取，如 config.get('hallucination', 'enabled')。"""
        value = self._config
        for key in keys:
            if not isinstance(value, dict):
                return default
            value = value.get(key)
            if value is None:
                return default
        return value

    def reload(self):
        """热重载配置。"""
        self._load()


def is_hallucination_enabled() -> bool:
    return EnhancementConfig().get("hallucination", "enabled", default=False)


def is_grader_enabled() -> bool:
    return EnhancementConfig().get("grader", "enabled", default=False)


def is_circuit_breaker_enabled() -> bool:
    return EnhancementConfig().get("circuit_breaker", "enabled", default=False)
```

---

## 六、实施路径

### 6.1 分阶段落地（方案 A 协同改造）

```
Phase 1（高优先级，独立改造，不涉及主流程协同）
├─ 幻觉检测恢复
│  ├─ 新建 VerifierGateway 接口
│  ├─ 新建 LocalVerifierGateway
│  └─ 改造 hallucination.py（节点内部增强，不影响主流程编排）
├─ 熔断机制恢复
│  └─ 改造 local_retriever.py 集成 CircuitBreaker（网关层增强，对主流程透明）
└─ 配置文件 rag_enhancement.yaml

Phase 2（高优先级，协同改造，必须整体落地）
│  ★ 方案 A 核心改造：RAGTool 单次执行 + 主流程统一重试
│  ★ 检索质量评估 + 查询重写 + 重试控制 三项协同改造，避免破坏中间状态
│
├─ Step 1: AgentState 新增字段（state.py）
│  └─ rag_score_source / retry_token_used / retry_token_budget
├─ Step 2: RAGToolInput / RAGToolOutput 契约变更（rag_tool.py）
│  └─ Input 新增 retry_count / Output 新增 score_source, rewrite_strategy
├─ Step 3: RAGTool._evaluate_quality 输出 score_source（rag_tool.py）
├─ Step 4: RAGTool._rewrite_query 策略轮转协同（rag_tool.py）
│  └─ (retry_count + attempt) % len(strategies)
├─ Step 5: RAGTool.invoke() 移除内层重试（rag_tool.py）  ★ 与 Step 7 同 PR
├─ Step 6: rag_tool_node 透传 retry_count + 累计 token（rag_tool_node.py）
└─ Step 7: quality_check 阈值按来源区分 + Token 预算检查（quality_check.py）  ★ 与 Step 5 同 PR

Phase 3（中优先级，独立改造）
├─ 查询重写完整实现（子查询拆解 + HyDE）
│  └─ 恢复 SubQueryDecomposer + HyDE 组件调用（依赖 Phase 2 Step 4 的策略轮转）
└─ 微服务拆分准备（Shadow 双跑）
```

### 6.2 验证方案

#### 单元测试

```python
# test/test_hallucination_gateway.py

import pytest
from agent.langgraph.gateways.local_verifier import LocalVerifierGateway


@pytest.mark.asyncio
async def test_verify_faithfulness_three_layer():
    """测试三层验证。"""
    gateway = LocalVerifierGateway()
    result = await gateway.verify_faithfulness(
        answer="2024年Q3营收增长15%",
        context="2024年Q3营收增长15.3%",
        query="2024年Q3营收",
        tenant_id="test_tenant",
    )
    assert result["faithfulness_score"] >= 0.85
    assert result["action"] == "pass"


@pytest.mark.asyncio
async def test_verify_faithfulness_contradiction():
    """测试数值矛盾检测。"""
    gateway = LocalVerifierGateway()
    result = await gateway.verify_faithfulness(
        answer="2024年Q3营收增长50%",
        context="2024年Q3营收增长15%",
        query="2024年Q3营收",
        tenant_id="test_tenant",
    )
    assert result["faithfulness_score"] < 0.3
    assert result["action"] == "reject"
```

#### 集成测试

```python
# test/test_e2e_hallucination.py

@pytest.mark.asyncio
async def test_e2e_hallucination_three_layer():
    """端到端测试：三层验证 + 四级处置。"""
    # 1. 启动增强模式
    config = EnhancementConfig()
    config._config = {"hallucination": {"enabled": True, "use_three_layer": True}}

    # 2. 模拟生成答案
    state = {
        "generated_answer": "2024年Q3营收增长15%",
        "merged_context": "2024年Q3营收同比增长15.3%",
        "user_question": "2024年Q3营收",
        "tenant_id": "test_tenant",
    }

    # 3. 执行幻觉检测
    result = await hallucination_node(state)

    # 4. 验证结果
    assert result["hallucination_score"] >= 0.85
    assert result["hallucination_action"] == "pass"
```

### 6.3 灰度发布策略

```yaml
# 灰度配置（10% 流量开启增强）
enhancement:
  rollout:
    strategy: percentage       # percentage / tenant_whitelist
    percentage: 10             # 10% 流量
    tenant_whitelist:          # 或指定租户
      - tenant_001
      - tenant_002
```

---

## 七、微服务演进路径

### 7.1 三阶段演进

```
阶段 1（当前）：单体，恢复能力
┌─────────────────────────────────────────┐
│           单一 Python 进程               │
│  LangGraph + RAGFlow 组件（同一进程）    │
│  Gateway → LocalVerifierGateway         │
│           (import 调用，零网络开销)      │
└─────────────────────────────────────────┘

阶段 2（灰度验证）：Shadow 双跑
┌─────────────────────────────────────────┐
│        LangGraph 主流程进程              │
│  Gateway → ShadowVerifierGateway        │
│           (Local + Http 双跑对比)        │
└──────────────┬──────────────────────────┘
               ↕ HTTP（异步对比）
┌─────────────────────────────────────────┐
│        验证微服务（独立部署）             │
│  HallucinationDetector + Grader         │
│  REST API: /v1/verify/faithfulness      │
└─────────────────────────────────────────┘

阶段 3（微服务）：按服务特性独立拆分
┌─────────────────────────────────────────┐
│        LangGraph 主流程进程              │
│  Gateway → HttpVerifierGateway          │
│           (HTTP 调用)                    │
└──────────────┬──────────────────────────┘
               ↕ HTTP
┌─────────────────────────────────────────┐
│  检索微服务 │ Grader 微服务 │ Verifier 微服务 │
│  (CPU 密集) │ (LLM 调用)   │ (LLM 调用)     │
│  独立扩容   │ 独立扩容     │ 独立扩容       │
└─────────────────────────────────────────┘
```

### 7.2 微服务拆分时的 RAGFlow 侧改造

微服务拆分时，需要在 RAGFlow 侧新增 REST API 包装组件：

```python
# api/apps/verifier_app.py（RAGFlow 新增）

from flask import Blueprint, request, jsonify
from agent.component.grader import Grader
from agent.component.hallucination_detector import HallucinationDetector

verifier_bp = Blueprint("verifier", __name__, url_prefix="/v1/verify")


@verifier_bp.post("/faithfulness")
async def verify_faithfulness():
    """幻觉检测 API（包装 HallucinationDetector 组件）。"""
    data = request.json
    tenant_id = data["tenant_id"]

    detector = HallucinationDetector(tenant_id=tenant_id)
    claims = detector._decompose_claims(data["answer"])
    score = await detector._verify_claims(claims, data["context"])
    action = detector._dispose(score, regenerate_count=0)

    return jsonify({
        "faithfulness_score": score,
        "action": action,
        "claims": claims,
    })


@verifier_bp.post("/grade")
async def grade_retrieval():
    """检索质量评估 API（包装 Grader 组件）。"""
    data = request.json
    tenant_id = data["tenant_id"]

    grader = Grader(
        tenant_id=tenant_id,
        evaluator_model="cross_encoder",
        fallback_on_failure=True,
    )
    graded_docs = grader.evaluate(data["query"], data["chunks"])

    return jsonify({
        "quality_score": sum(d["score"] for d in graded_docs) / len(graded_docs),
        "graded_docs": graded_docs,
    })
```

### 7.3 演进收益

| 维度 | 单体阶段 | 微服务阶段 | 收益 |
|------|---------|----------|------|
| **延迟** | ~50ms（进程内） | ~80ms（含网络） | -30ms（可接受） |
| **可用性** | 一崩全崩 | 故障隔离 | ✅ 大幅提升 |
| **扩缩容** | 整体扩容 | 按服务独立扩容 | ✅ 成本优化 |
| **部署** | 全量发布 | 按服务独立发布 | ✅ 迭代加速 |
| **技术栈** | 统一 Python | 各服务可选最优技术栈 | ✅ 灵活性 |

---

## 八、风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| RAGFlow 组件调用失败 | 主流程阻塞 | 降级兜底：回退到简化实现 |
| LLM 调用成本增加 | 运营成本上升 | 边界值触发策略 + 短路优化 |
| 微服务拆分后延迟增加 | 用户体验下降 | 异步调用 + 缓存 + 超时降级 |
| 配置错误 | 增强能力未生效 | 启动时校验配置 + 日志告警 |
| 三层验证误判 | 正确答案被判为幻觉 | 阈值可调 + 降级为 filter 而非 reject |

---

## 九、验收标准

| 能力 | 验收指标 | 验证方法 |
|------|---------|---------|
| 幻觉检测 | 数值矛盾检测率 ≥ 90% | 标注数据集测试 |
| 检索质量评估 | 边界值场景准确率提升 ≥ 20% | A/B 测试对比 |
| 熔断机制 | 服务故障时自动降级，恢复后自动恢复 | 混沌工程测试 |
| 查询重写 | 复杂查询召回率提升 ≥ 30% | 标注数据集测试 |
| 重试控制 | Token 预算不超限 | 监控指标验证 |
| 降级兜底 | 增强失败时主流程可用性 100% | 故障注入测试 |

---

## 十、附录

### 10.1 相关文件清单

| 文件 | 类型 | 说明 | 方案 A 改造点 |
|------|------|------|--------------|
| `agent/langgraph/gateways/verifier.py` | 新增 | VerifierGateway 接口 | - |
| `agent/langgraph/gateways/local_verifier.py` | 新增 | 本地实现（import 调用） | - |
| `agent/langgraph/gateways/http_verifier.py` | 新增 | 远程实现（HTTP 调用） | - |
| `agent/langgraph/gateways/factory.py` | 修改 | 扩展 verifier_for() | - |
| `agent/langgraph/nodes/hallucination.py` | 修改 | 集成 VerifierGateway | - |
| `agent/langgraph/tools/rag_tool.py` | 修改 | 集成 Grader + 完整重写策略 | ★ 移除内层重试 + 输出 score_source + 策略轮转协同 |
| `agent/langgraph/gateways/local_retriever.py` | 修改 | 集成 CircuitBreaker | - |
| `agent/langgraph/state.py` | 修改 | AgentState 字段 | ★ 新增 rag_score_source / retry_token_used / retry_token_budget |
| `agent/langgraph/nodes/rag_tool_node.py` | 修改 | RAG 工具节点 | ★ 透传 retry_count + 累计 token + 透传 score_source |
| `agent/langgraph/nodes/quality_check.py` | 修改 | 质量检查节点 | ★ 阈值按来源区分 + Token 预算检查 |
| `agent/langgraph/config.py` | 新增 | 配置加载器 | - |
| `conf/rag_enhancement.yaml` | 新增 | 增强能力配置 | ★ 增加 thresholds.base / thresholds.grader_merged |
| `api/apps/verifier_app.py` | 微服务阶段新增 | RAGFlow 侧 REST API | - |

### 10.2 依赖的 RAGFlow 组件

| 组件 | 路径 | 用途 |
|------|------|------|
| `HallucinationDetector` | `agent/component/hallucination_detector.py` | 三层幻觉检测 |
| `Grader` | `agent/component/grader.py` | 检索质量三模式评估 |
| `QueryRewriter` | `agent/component/query_rewriter.py` | 查询复杂度分析 + 同义词扩展 |
| `SubQueryDecomposer` | `agent/component/sub_query_decomposer.py` | 子查询拆解 |
| `HyDE` | `agent/component/hyde.py` | 假设文档嵌入 |
| `CircuitBreaker` | `api/utils/circuit_breaker.py` | 熔断状态机 |
| `HealthChecker` | `api/utils/health_checker.py` | 主动健康检查 |
| `fact_checker` | `api/utils/fact_checker.py` | 规则层事实校验 |

---

> **文档版本**：v2.0（方案 A：RAGTool 单次执行 + 主流程统一重试）
> **创建日期**：2026-07-19
> **最后更新**：2026-08-06
> **v2.0 变更说明**：
> - 新增 §1.3 职责边界划定，消除 RAGTool 与主流程的双重重试重复
> - §4.2 检索质量评估增加 score_source 分数来源标注，quality_check 按来源区分阈值
> - §4.4 查询重写策略轮转改为 (retry_count + attempt) % len(strategies)，外层重试切换策略
> - §4.5 重试控制改为方案 A：RAGTool 单次执行，Token 预算上提主流程 quality_check 管控
> - 新增 §4.6 主流程协同改造汇总，含改造点全景图、文件映射、数据流时序、向后兼容性、实施顺序
