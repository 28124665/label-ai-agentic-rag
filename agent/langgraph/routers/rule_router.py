"""第1层：规则路由器。

基于关键词匹配和规则打分进行意图路由，支持：
- 范围词优先判断（scope=external/internal）
- 扩充的 DB/RAG 偏向词库
- 置信度打分机制
- 时效性检测

设计原则：
- 低成本、低延迟（< 10ms）
- 覆盖 60%-70% 的请求
- 输出置信度供后续层决策
"""

import logging
from typing import Any, Optional

from agent.langgraph.routers.config_loader import IntentRouterConfig, load_config
from agent.langgraph.routers.models import RouteDecision

logger = logging.getLogger(__name__)


class RuleRouter:
    """第1层规则路由器。"""

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

        # 6. DB 偏向词匹配
        db_score, db_keywords_matched = self._match_db_bias(query_lower, keywords)

        # 7. RAG 偏向词匹配
        rag_score, rag_keywords_matched = self._match_rag_bias(query_lower, keywords)

        # 7.5 检查是否有明确的 RAG 模式（优先级更高）
        has_strong_rag_pattern = self._has_strong_rag_pattern(query_lower)

        # 8. 综合打分，选择最高置信度的路由
        # 如果有明确的 RAG 模式（如 "是什么"、"什么是"、"如何"），优先路由到 RAG
        if has_strong_rag_pattern and rag_score > 0:
            target = "rag"
            # 强 RAG 模式给予高置信度（至少 0.75）
            confidence = max(rag_score, 0.75)
            confidence = min(confidence, 0.95)
            reason = f"RAG 模式优先: {', '.join(rag_keywords_matched)}"
        elif db_score > rag_score and db_score > 0:
            target = "database"
            confidence = min(db_score, 0.9)
            reason = f"DB 偏向词匹配: {', '.join(db_keywords_matched)}"
        elif rag_score > db_score and rag_score > 0:
            target = "rag"
            confidence = min(rag_score, 0.9)
            reason = f"RAG 偏向词匹配: {', '.join(rag_keywords_matched)}"
        elif db_score > 0 and rag_score > 0:
            # 同时匹配 DB 和 RAG 词，判断为 hybrid
            target = "hybrid"
            confidence = min(max(db_score, rag_score), 0.85)
            reason = f"混合意图: DB({', '.join(db_keywords_matched)}) + RAG({', '.join(rag_keywords_matched)})"
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

    def _match_db_bias(self, query: str, keywords: dict) -> tuple[float, list[str]]:
        """匹配 DB 偏向词。

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

        # 置信度计算：匹配词越多，置信度越高
        # 1 个词: 0.6, 2 个词: 0.75, 3 个词: 0.9, 4+ 个词: 0.9
        # 单个关键词匹配时置信度较低，需要 LLM 确认
        base_score = 0.6
        bonus = min((len(matched) - 1) * 0.15, 0.3)
        score = base_score + bonus

        return score, matched

    def _match_rag_bias(self, query: str, keywords: dict) -> tuple[float, list[str]]:
        """匹配 RAG 偏向词。

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

        # 置信度计算：匹配词越多，置信度越高
        # 1 个词: 0.6, 2 个词: 0.75, 3 个词: 0.9, 4+ 个词: 0.9
        # 单个关键词匹配时置信度较低，需要 LLM 确认
        base_score = 0.6
        bonus = min((len(matched) - 1) * 0.15, 0.3)
        score = base_score + bonus

        return score, matched

    def _has_strong_rag_pattern(self, query: str) -> bool:
        """检查是否有明确的 RAG 模式（优先级更高）。

        检测明确的定义性问题模式，如"是什么"、"什么是"、"如何"、"为什么"等，
        这些模式应该优先路由到 RAG，即使同时匹配了 DB 关键词。

        Args:
            query: 用户查询（小写）

        Returns:
            bool: 是否有明确的 RAG 模式
        """
        # 明确的定义性问题模式
        strong_patterns = [
            "是什么",
            "什么是",
            "如何",
            "为什么",
            "啥意思",
            "什么意思",
            "定义",
            "概念",
            "原理",
        ]
        return any(pattern in query for pattern in strong_patterns)

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
