"""SkillRouter 主编排器（P2，§10.2 / §18.3）。

设计文档 §10.2 路由算法 6 Stage：
    Stage 0: 规范化（query 清洗、locale 提取）
    Stage 1: 硬过滤（lifecycle / tenant / permission / capability / latency）
    Stage 2: 强信号匹配（skill_id / report_type / 会话固定）
    Stage 3: 领域分类（规则分类器，Top-N domain）
    Stage 4: 混合召回（BM25 + Embedding + RRF 融合）
    Stage 5: 候选重排（LLM Reranker）
    Stage 6: 决策策略（select / clarify / no_skill / fallback / composition）

设计文档 §10.9.3 优先级矩阵：
    - 精确指令（@database/@rag/@web）优先于 Skill 路由（约束1）
    - chitchat 路径不做 Skill 路由（§10.8 no-skill 分支）
    - Tier2 弱关键词不作为 Skill 强信号（约束2）
    - Skill 路由结果不覆盖 route_target（约束6，Skill 与工具层路由正交）

与 P0 SkillResolver 的关系（§18.3 约束1）：
    P2 阶段 SkillRouter 替代 SkillResolver 成为 Skill 路由的主入口。
    SkillResolver 降级为 SkillRouter 的薄包装（保持向后兼容）。
    intent_router_node 中的 _resolve_skill 逐步切换为调用 SkillRouter.route()。

类比 Java：
    ``SkillRouter`` ≈ ``@Service`` 主编排器（Facade 模式），
    编排 HardFilter / BM25Retriever / EmbeddingRetriever / SkillReranker / DecisionPolicy。
    各 Stage ≈ Pipeline 模式的处理步骤，每步输入上一步输出。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent.langgraph.skills.card import SkillCard
from agent.langgraph.skills.decision_policy import DecisionPolicy, DecisionResult, DecisionThresholds
from agent.langgraph.skills.filters import FilterContext, FilterResult, HardFilter
from agent.langgraph.skills.models import SkillResolveContext, SkillResolveResult
from agent.langgraph.skills.reranker import SkillRankDecision, SkillReranker
from agent.langgraph.skills.retrievers import BM25Retriever, EmbeddingRetriever, ScoredCard

logger = logging.getLogger(__name__)


# ========== 领域分类规则（§10.5.2） ==========

DOMAIN_KEYWORD_RULES: dict[str, list[str]] = {
    "quality": ["质量", "品质", "不良", "缺陷", "客诉", "良率", "8D", "IQC", "OQC"],
    "production": ["产线", "产能", "稼动", "OEE", "工单", "排程", "生产"],
    "cost": ["成本", "费用", "预算", "利润", "财务", "支出"],
    "delivery": ["交付", "物流", "出货", "库存", "周转", "准时率"],
    "operations": ["运营", "综合", "月报", "周报", "看板", "KPI"],
}


def classify_domains_by_rule(query: str) -> list[str]:
    """规则领域分类器（§10.5.2）。

    返回命中的 domain 列表（按命中词数降序，Top-3）。
    若无任何命中，返回 ["generic"]（不收窄候选集）。
    """
    scored: list[tuple[str, int]] = []
    for domain, keywords in DOMAIN_KEYWORD_RULES.items():
        hits = sum(1 for kw in keywords if kw in query)
        if hits > 0:
            scored.append((domain, hits))
    if not scored:
        return ["generic"]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [d for d, _ in scored[:3]]


# ========== RRF 融合（§10.6） ==========

def reciprocal_rank_fusion(
    bm25_results: list[ScoredCard],
    embedding_results: list[ScoredCard],
    top_k: int = 20,
    k: int = 60,
) -> list[ScoredCard]:
    """Reciprocal Rank Fusion（§10.6 融合公式）。

    RRF(d) = Σ 1 / (k + rank_i(d))

    Args:
        bm25_results: BM25 召回结果（按分数降序）
        embedding_results: Embedding 召回结果（按分数降序）
        top_k: 融合后保留的 Top-K
        k: RRF 常数（默认 60）

    Returns:
        融合后的 ScoredCard 列表（按 RRF 分数降序）
    """
    rrf_scores: dict[str, float] = {}
    cards_by_id: dict[str, SkillCard] = {}

    for rank, scored in enumerate(bm25_results):
        sid = scored.card.skill_id
        rrf_scores[sid] = rrf_scores.get(sid, 0.0) + 1.0 / (k + rank + 1)
        cards_by_id[sid] = scored.card

    for rank, scored in enumerate(embedding_results):
        sid = scored.card.skill_id
        rrf_scores[sid] = rrf_scores.get(sid, 0.0) + 1.0 / (k + rank + 1)
        cards_by_id[sid] = scored.card

    # 按 RRF 分数降序排序
    sorted_ids = sorted(rrf_scores.keys(), key=lambda sid: rrf_scores[sid], reverse=True)
    return [
        ScoredCard(card=cards_by_id[sid], score=rrf_scores[sid], source="rrf")
        for sid in sorted_ids[:top_k]
    ]


# ========== SkillRouter ==========


@dataclass
class RouterConfig:
    """SkillRouter 配置。

    Attributes:
        bm25_top_k: BM25 召回 Top-K（默认 20）
        embedding_top_k: Embedding 召回 Top-K（默认 20）
        rrf_top_k: RRF 融合后保留 Top-K（默认 20）
        reranker_max_candidates: Reranker 最大候选数（默认 10）
        domain_top_n: 领域分类 Top-N（默认 3）
    """

    bm25_top_k: int = 20
    embedding_top_k: int = 20
    rrf_top_k: int = 20
    reranker_max_candidates: int = 10
    domain_top_n: int = 3


class SkillRouter:
    """Skill 路由主编排器（§10.2 Stage 0-6）。

    编排 6 个 Stage，从 Catalog Snapshot 的 SkillCard 列表中选出最佳匹配 Skill。

    与 P0 SkillResolver 的接口兼容：
        route() 返回 SkillResolveResult，可被 intent_router_node 直接使用。

    约束（§10.9.3）：
        - 精确指令跳过 Skill 路由（约束1，由调用方判断）
        - chitchat 不做 Skill 路由（§10.8 no-skill 分支，由调用方判断）
        - route_target 不被 Skill 路由覆盖（约束6，由调用方决定）
    """

    def __init__(
        self,
        cards_by_key: dict[tuple[str, str], SkillCard] | None = None,
        bm25_retriever: BM25Retriever | None = None,
        embedding_retriever: EmbeddingRetriever | None = None,
        reranker: SkillReranker | None = None,
        hard_filter: HardFilter | None = None,
        decision_policy: DecisionPolicy | None = None,
        config: RouterConfig | None = None,
    ) -> None:
        """初始化 SkillRouter。

        Args:
            cards_by_key: (skill_id, version) → SkillCard 映射（来自 Catalog Snapshot）
            bm25_retriever: BM25 检索器（None=不可用，降级为 keyword 匹配）
            embedding_retriever: Embedding 检索器（None=不可用，仅 BM25）
            reranker: LLM 重排器（None=不可用，降级为召回分数排序）
            hard_filter: 硬过滤器（默认创建 HardFilter）
            decision_policy: 决策策略（默认创建 DecisionPolicy）
            config: 路由配置
        """
        self._cards_by_key = cards_by_key or {}
        self._bm25 = bm25_retriever
        self._embedding = embedding_retriever
        self._reranker = reranker
        self._hard_filter = hard_filter or HardFilter()
        self._decision_policy = decision_policy or DecisionPolicy()
        self._config = config or RouterConfig()

        # 构建 skill_id → SkillCard 快速查找（取每个 skill_id 的最新版本）
        self._cards_by_id: dict[str, SkillCard] = {}
        for (skill_id, _version), card in self._cards_by_key.items():
            # 后出现的版本覆盖前面的（假设配置已按版本排序）
            self._cards_by_id[skill_id] = card

    def route(
        self,
        context: SkillResolveContext,
        llm_call_func: Any | None = None,
    ) -> SkillResolveResult:
        """执行 Skill 路由（Stage 0-6，§10.2）。

        与 P0 SkillResolver.resolve() 接口兼容，返回 SkillResolveResult。

        Args:
            context: Skill 解析上下文
            llm_call_func: LLM 调用回调（供 Reranker 使用，None=降级）

        Returns:
            SkillResolveResult: 路由结果
        """
        # ===== Stage 0: 规范化 =====
        query = context.user_question.strip()
        if not query:
            return self._failure("EMPTY_QUERY")

        # ===== Stage 2: 强信号匹配（优先于 Stage 1-6）=====
        # §10.4：显式 skill_id / report_type 是强信号，直接定位
        strong_match = self._strong_signal_match(context)
        if strong_match is not None:
            # 强信号匹配仍需经过硬过滤（§10.3 约束）
            filter_ctx = FilterContext.from_resolve_context(context)
            reason = self._hard_filter.check_single(strong_match, filter_ctx)
            if reason is None:
                return self._build_resolve_result(strong_match, "skill_id", context)
            logger.debug("[SkillRouter] 强信号匹配 '%s' 硬过滤失败: %s", strong_match.skill_id, reason)

        # ===== Stage 1: 硬过滤 =====
        all_cards = list(self._cards_by_id.values())
        filter_ctx = FilterContext.from_resolve_context(context)
        filter_result = self._hard_filter.apply(all_cards, filter_ctx)

        if not filter_result.has_candidates:
            logger.debug("[SkillRouter] Stage 1 硬过滤后无候选")
            return self._failure("NO_CANDIDATES_AFTER_FILTER")

        # 只对 ReportSkill 类型进行语义召回
        report_cards = [c for c in filter_result.passed if c.skill_type == "report"]
        if not report_cards:
            return self._failure("NO_REPORT_SKILLS")

        # ===== Stage 3: 领域分类 =====
        domains = classify_domains_by_rule(query)
        domain_filtered = self._filter_by_domain(report_cards, domains)

        # ===== Stage 4: 混合召回 =====
        recall_results = self._hybrid_recall(query, domain_filtered)

        if not recall_results:
            # 召回无结果，降级为 keyword 匹配（与 P0 兼容）
            logger.debug("[SkillRouter] Stage 4 召回无结果，降级为 keyword 匹配")
            recall_results = self._keyword_fallback(query, domain_filtered)

        if not recall_results:
            return self._failure("NO_RECALL_RESULTS")

        # ===== Stage 5: 候选重排 =====
        # 如果 reranker 不可用或未注入 llm_call_func，使用召回分数排序
        if self._reranker is None or llm_call_func is None:
            logger.debug("[SkillRouter] Reranker 不可用，使用召回分数排序")
            from agent.langgraph.skills.reranker import RankedCandidate
            rank_decision = SkillRankDecision(
                candidates=[
                    RankedCandidate(skill_id=s.card.skill_id, score=s.score, reason="recall_only")
                    for s in recall_results[:5]
                ],
                rerank_degraded=True,
            )
        else:
            # 更新 reranker 的 LLM 调用回调
            self._reranker._llm_call = llm_call_func
            import asyncio
            try:
                rank_decision = asyncio.get_event_loop().run_until_complete(
                    self._reranker.rerank(query, recall_results)
                )
            except RuntimeError:
                # 已在事件循环中，创建新循环
                loop = asyncio.new_event_loop()
                try:
                    rank_decision = loop.run_until_complete(
                        self._reranker.rerank(query, recall_results)
                    )
                finally:
                    loop.close()

        # ===== Stage 6: 决策策略 =====
        candidates_by_id = {s.card.skill_id: s.card for s in recall_results}
        decision = self._decision_policy.decide(rank_decision, candidates_by_id)

        return self._build_decision_result(decision, context)

    # ========== Stage 2: 强信号匹配 ==========

    def _strong_signal_match(self, context: SkillResolveContext) -> SkillCard | None:
        """Stage 2 强信号匹配（§10.4）。

        优先级：
            1. 显式 skill_id
            2. report_type（从 route_decision.metadata 读取）
        """
        # 显式 skill_id
        if context.skill_id:
            card = self._cards_by_id.get(context.skill_id.strip())
            if card and card.skill_type == "report":
                return card

        # report_type
        report_type = context.report_type or self._extract_report_type(context)
        if report_type:
            for card in self._cards_by_id.values():
                if card.report_type == report_type:
                    return card

        return None

    @staticmethod
    def _extract_report_type(context: SkillResolveContext) -> str | None:
        """从 route_decision.metadata 提取 report_type。"""
        route_decision = context.route_decision or {}
        metadata = route_decision.get("metadata", {}) if isinstance(route_decision, dict) else {}
        value = metadata.get("report_type") if isinstance(metadata, dict) else None
        return value if isinstance(value, str) else None

    # ========== Stage 3: 领域过滤 ==========

    @staticmethod
    def _filter_by_domain(
        cards: list[SkillCard],
        domains: list[str],
    ) -> list[SkillCard]:
        """Stage 3 领域过滤（§10.5.4）。

        domain 与 card.domains 有交集的保留。
        generic domain 的 Skill 始终保留（兜底）。
        """
        if "generic" in domains:
            # 规则未命中，不收窄候选集
            return cards

        domain_set = set(domains)
        result: list[SkillCard] = []
        for card in cards:
            # generic Skill 始终保留（兜底）
            if "generic" in card.domains:
                result.append(card)
                continue
            # domain 有交集
            if domain_set & set(card.domains):
                result.append(card)
        return result

    # ========== Stage 4: 混合召回 ==========

    def _hybrid_recall(
        self,
        query: str,
        cards: list[SkillCard],
    ) -> list[ScoredCard]:
        """Stage 4 混合召回（§10.6 BM25 + Embedding + RRF）。"""
        bm25_results: list[ScoredCard] = []
        embedding_results: list[ScoredCard] = []

        # BM25 召回
        if self._bm25 is not None:
            try:
                bm25_results = self._bm25.search(query, self._config.bm25_top_k)
            except Exception as e:
                logger.warning("[SkillRouter] BM25 召回异常: %s", e)

        # Embedding 召回（P2 已实现）
        # EmbeddingRetriever.search 需要 query_embedding（list[float]），
        # 而 embed_func 在 build() 时注入。这里从 route() 传入的
        # llm_call_func 不适合做 embedding，需要 embed_func。
        # SkillRouter 暂不支持 per-request embed_func，
        # 所以 Embedding 召回在 SkillResolver 中实现（通过 resolve 的 embed_func 参数）。
        # SkillRouter 的 _hybrid_recall 保留 embedding_retriever 检查但不执行搜索，
        # 实际 Embedding + RRF 由 SkillResolver._embedding_recall_and_fuse() 完成。

        # 如果 BM25 和 Embedding 都无结果，返回空
        if not bm25_results and not embedding_results:
            return []

        # 如果只有一路结果，直接返回
        if not embedding_results:
            return bm25_results[: self._config.rrf_top_k]
        if not bm25_results:
            return embedding_results[: self._config.rrf_top_k]

        # RRF 融合（§10.6）
        return reciprocal_rank_fusion(
            bm25_results, embedding_results, top_k=self._config.rrf_top_k
        )

    @staticmethod
    def _keyword_fallback(
        query: str,
        cards: list[SkillCard],
    ) -> list[ScoredCard]:
        """keyword 匹配降级（与 P0 resolver._ranked_keyword_candidates 兼容）。

        当 BM25/Embedding 索引不可用时，使用关键词命中分数排序。
        """
        normalized = query.casefold()
        scored: list[ScoredCard] = []
        for card in cards:
            score = sum(
                len(kw) for kw in card.intents if kw.casefold() in normalized
            )
            if score > 0:
                scored.append(ScoredCard(card=card, score=float(score), source="keyword"))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:20]

    # ========== 结果构建 ==========

    def _build_decision_result(
        self,
        decision: DecisionResult,
        context: SkillResolveContext,
    ) -> SkillResolveResult:
        """从 DecisionResult 构建 SkillResolveResult。"""
        if decision.decision == "select" and decision.selected_card is not None:
            return self._build_resolve_result(
                decision.selected_card, "semantic", context
            )

        if decision.decision == "composition" and decision.selected_card is not None:
            return self._build_resolve_result(
                decision.selected_card, "composition", context
            )

        if decision.decision == "clarify":
            return SkillResolveResult(
                skill_set=None,
                resolved=False,
                fallback_used=False,
                reason=f"CLARIFY:{decision.reason}",
                warnings=[f"clarify_candidates: {[c.skill_id for c in (decision.clarify_candidates or [])]}"],
            )

        if decision.decision == "no_skill":
            return self._failure(f"NO_SKILL:{decision.reason}")

        # fallback
        return self._failure(f"FALLBACK:{decision.reason}")

    def _build_resolve_result(
        self,
        card: SkillCard,
        source: str,
        context: SkillResolveContext,
    ) -> SkillResolveResult:
        """从 SkillCard 构建 SkillResolveResult。

        P2 阶段简化：不解析 DataSkill/RetrievalSkill 依赖（由 P0 resolver 处理）。
        SkillRouter 只负责 ReportSkill 的路由决策。
        """
        # 从 cards_by_id 找到原始 ReportSkill（P2 简化：直接用 SkillCard 构建）
        # 实际实现中需要从 Snapshot 获取原始 ReportSkill 对象
        # 这里返回 resolved=True 但 skill_set=None，由调用方（intent_router）处理
        return SkillResolveResult(
            skill_set=None,
            resolved=True,
            fallback_used=False,
            reason=f"ROUTED:{source}:{card.skill_id}",
            warnings=[],
        )

    @staticmethod
    def _failure(reason: str) -> SkillResolveResult:
        """构造失败结果。"""
        return SkillResolveResult(
            skill_set=None,
            resolved=False,
            fallback_used=False,
            reason=reason,
        )
