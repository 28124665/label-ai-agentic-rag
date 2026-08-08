"""第1层：规则路由器。

基于关键词匹配和规则打分进行意图路由，支持：
- Tier 1 强模式匹配（正则，confidence=0.80，直接返回）
  - 范围词 + 时效性 → web
  - 定义型/原理型/统计型正则模式 → rag/database
- Tier 2 弱关键词匹配（关键词计数，confidence cap=0.55，强制走 LLM）
  - DB 偏向词、RAG 偏向词

设计原则：
- 低成本、低延迟（< 10ms）
- 强模式高置信度直接拦截，弱关键词不短路
- 复杂度闸门已拦截复杂问题，此处只处理简单问题
"""

import logging
import re
from typing import Any, Optional

from agent.langgraph.routers.config_loader import IntentRouterConfig, load_config
from agent.langgraph.routers.models import RouteDecision

logger = logging.getLogger(__name__)


class RuleRouter:
    """第1层规则路由器。"""

    # Tier 1 强模式正则（高置信度直接拦截，confidence=0.80）
    # 定义型/原理型/统计型问题，语义强信号
    STRONG_PATTERNS = [
        (re.compile(r"^什么是.+"), "rag", "什么是X"),
        (re.compile(r"^.+是什么$"), "rag", "X是什么"),
        (re.compile(r"^.+是什么意思$"), "rag", "X是什么意思"),
        (re.compile(r"^什么意思"), "rag", "什么意思"),
        (re.compile(r"^.+的原理"), "rag", "X的原理"),
        (re.compile(r"^统计.+(?:数量|总数|人数)$"), "database", "统计X数量"),
        (re.compile(r"^查询.+(?:数量|总数|人数)$"), "database", "查询X数量"),
    ]

    # Tier 2 弱关键词置信度参数（封顶 0.55，强制走 LLM）
    WEAK_BASE_SCORE = 0.45
    WEAK_BONUS_PER_KEYWORD = 0.05
    WEAK_BONUS_CAP = 0.10
    WEAK_CONFIDENCE_CAP = 0.55

    def __init__(self, config: Optional[IntentRouterConfig] = None):
        """初始化规则路由器。

        Args:
            config: 路由配置，如果为 None 则从配置文件加载
        """
        self.component_name = "RuleRouter"
        self._config = config or load_config()

    def route(self, query: str) -> RouteDecision:
        """执行规则路由。

        路由策略：
        1. 范围词优先判断（external/internal）
        2. 时效性检测
        3. DB 偏向词匹配
        4. RAG 偏向词匹配
        5. 综合打分，选择最高置信度的路由

        Args:
            query: 用户查询

        Returns:
            RouteDecision: 路由决策
        """
        if not query or not query.strip():
            return self._default_decision("查询为空")

        query_lower = query.lower()
        rule_config = self._config.rule_router
        keywords = rule_config.get("keywords", {})

        # 1. 范围词判断
        scope = self._detect_scope(query_lower, keywords)

        # 2. 时效性检测
        freshness_required = self._detect_freshness(query_lower, keywords)

        # 3. 如果范围是 external 且有时效性需求，直接路由到 web
        if scope == "external" and freshness_required:
            logger.info(f"[RuleRouter] 外部范围 + 时效性需求 -> web: '{query}'")
            return RouteDecision(
                target="web",
                confidence=0.85,
                source="rule",
                reason="外部范围 + 时效性需求",
                complexity="simple",
                metadata={
                    "scope": "external",
                    "freshness_required": True,
                },
            )

        # 4. 如果范围是 external，路由到 web
        if scope == "external":
            logger.info(f"[RuleRouter] 外部范围 -> web: '{query}'")
            return RouteDecision(
                target="web",
                confidence=0.8,
                source="rule",
                reason="外部范围词匹配",
                complexity="simple",
                metadata={"scope": "external"},
            )

        # 5. 如果有时效性需求，路由到 web
        if freshness_required:
            logger.info(f"[RuleRouter] 时效性需求 -> web: '{query}'")
            return RouteDecision(
                target="web",
                confidence=0.75,
                source="rule",
                reason="时效性关键词匹配",
                complexity="simple",
                metadata={"freshness_required": True},
            )

        # 6. Tier 1：强模式正则匹配（高置信度，直接返回）
        strong_match = self._match_strong_pattern(query)
        if strong_match:
            target, pattern_name = strong_match
            logger.info(
                f"[RuleRouter] Tier1 强模式匹配: '{query}' -> {target} "
                f"({pattern_name})"
            )
            return RouteDecision(
                target=target,
                confidence=0.80,
                source="rule",
                reason=f"强模式: {pattern_name}",
                complexity="simple",
                metadata={"pattern": pattern_name},
            )

        # 7. Tier 2：弱关键词匹配（低置信度，封顶 0.55，强制走 LLM）
        db_score, db_keywords_matched = self._match_db_bias(query_lower, keywords)
        rag_score, rag_keywords_matched = self._match_rag_bias(query_lower, keywords)

        # 8. 综合打分（弱关键词，封顶 0.55，不满足 0.7 阈值，强制走 LLM）
        if db_score > 0 and rag_score > 0:
            target = "hybrid"
            confidence = min(max(db_score, rag_score), self.WEAK_CONFIDENCE_CAP)
            reason = f"混合意图: DB({', '.join(db_keywords_matched)}) + RAG({', '.join(rag_keywords_matched)})"
        elif db_score > rag_score and db_score > 0:
            target = "database"
            confidence = min(db_score, self.WEAK_CONFIDENCE_CAP)
            reason = f"DB 偏向词: {', '.join(db_keywords_matched)}"
        elif rag_score > db_score and rag_score > 0:
            target = "rag"
            confidence = min(rag_score, self.WEAK_CONFIDENCE_CAP)
            reason = f"RAG 偏向词: {', '.join(rag_keywords_matched)}"
        else:
            # 无明确匹配，默认 chitchat
            target = "chitchat"
            confidence = 0.5
            reason = "无明确关键词匹配"

        logger.info(
            f"[RuleRouter] 路由决策: '{query}' -> {target} "
            f"(confidence={confidence:.2f}, reason={reason})"
        )

        return RouteDecision(
            target=target,
            confidence=confidence,
            source="rule",
            reason=reason,
            complexity="simple",
            metadata={
                "db_score": db_score,
                "rag_score": rag_score,
                "db_keywords": db_keywords_matched,
                "rag_keywords": rag_keywords_matched,
                "scope": scope,
                "freshness_required": freshness_required,
            },
        )

    def _detect_scope(self, query: str, keywords: dict) -> str:
        """检测查询范围（external/internal）。

        Args:
            query: 用户查询（小写）
            keywords: 关键词配置

        Returns:
            str: 范围标识（external/internal）
        """
        external_keywords = keywords.get("external_scope", [])
        if any(kw in query for kw in external_keywords):
            return "external"
        return "internal"

    def _detect_freshness(self, query: str, keywords: dict) -> bool:
        """检测时效性需求。

        命中时效性关键词且未命中历史关键词时，返回 True。

        Args:
            query: 用户查询（小写）
            keywords: 关键词配置

        Returns:
            bool: 是否需要时效性信息
        """
        freshness_keywords = keywords.get("freshness_keywords", [])
        historical_keywords = keywords.get("historical_keywords", [])

        has_freshness = any(kw in query for kw in freshness_keywords)
        has_historical = any(kw in query for kw in historical_keywords)

        return has_freshness and not has_historical

    def _match_strong_pattern(self, query: str) -> Optional[tuple[str, str]]:
        """Tier 1 强模式正则匹配。

        检测定义型/原理型/统计型等语义强信号模式，
        命中时给予高置信度（0.80），直接返回。

        Args:
            query: 用户查询（原始大小写）

        Returns:
            tuple: (路由目标, 模式名称) 或 None
        """
        for pattern, target, name in self.STRONG_PATTERNS:
            if pattern.search(query):
                return target, name
        return None

    def _match_db_bias(self, query: str, keywords: dict) -> tuple[float, list[str]]:
        """匹配 DB 偏向词（Tier 2 弱关键词，置信度封顶 0.55）。

        Args:
            query: 用户查询（小写）
            keywords: 关键词配置

        Returns:
            tuple: (置信度分数, 匹配的关键词列表)
        """
        db_bias_keywords = keywords.get("db_bias", [])
        matched = [kw for kw in db_bias_keywords if kw in query]

        if not matched:
            return 0.0, []

        # Tier 2 弱关键词置信度：base=0.45, bonus=0.05/词, cap=0.55
        # 最高 0.55，永远 < 0.7 阈值，强制走 LLM 确认
        bonus = min((len(matched) - 1) * self.WEAK_BONUS_PER_KEYWORD, self.WEAK_BONUS_CAP)
        score = self.WEAK_BASE_SCORE + bonus

        return score, matched

    def _match_rag_bias(self, query: str, keywords: dict) -> tuple[float, list[str]]:
        """匹配 RAG 偏向词（Tier 2 弱关键词，置信度封顶 0.55）。

        Args:
            query: 用户查询（小写）
            keywords: 关键词配置

        Returns:
            tuple: (置信度分数, 匹配的关键词列表)
        """
        rag_bias_keywords = keywords.get("rag_bias", [])
        matched = [kw for kw in rag_bias_keywords if kw in query]

        if not matched:
            return 0.0, []

        # Tier 2 弱关键词置信度：base=0.45, bonus=0.05/词, cap=0.55
        # 最高 0.55，永远 < 0.7 阈值，强制走 LLM 确认
        bonus = min((len(matched) - 1) * self.WEAK_BONUS_PER_KEYWORD, self.WEAK_BONUS_CAP)
        score = self.WEAK_BASE_SCORE + bonus

        return score, matched

    def _default_decision(self, reason: str) -> RouteDecision:
        """生成默认路由决策。

        Args:
            reason: 决策理由

        Returns:
            RouteDecision: 默认决策（chitchat）
        """
        return RouteDecision(
            target="chitchat",
            confidence=0.5,
            source="rule",
            reason=reason,
            complexity="simple",
            metadata={},
        )


# 全局实例
_rule_router_instance: Optional[RuleRouter] = None


def get_rule_router(config: Optional[IntentRouterConfig] = None) -> RuleRouter:
    """获取 RuleRouter 单例实例。

    Args:
        config: 路由配置

    Returns:
        RuleRouter: 规则路由器实例
    """
    global _rule_router_instance
    if _rule_router_instance is None:
        _rule_router_instance = RuleRouter(config)
    return _rule_router_instance
