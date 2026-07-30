"""第0层：前置过滤器。

在进入规则路由前执行前置过滤，按优先级依次检测：
1. 安全拦截：SQL 注入特征、越权关键词
2. 显式外部搜索指令：同时命中动作词和外部范围词
3. 问候语识别：匹配问候模式
4. 实体格式匹配：匹配预定义实体格式

设计原则：
- 零成本、零延迟（< 1ms）
- 高置信度（1.0）
- 命中即返回，不进入后续路由层
"""

import logging
import re
from typing import Optional

from agent.langgraph.routers.models import RouteDecision

logger = logging.getLogger(__name__)


class PreFilter:
    """第0层前置过滤器。"""

    # SQL 注入特征
    SQL_INJECTION_PATTERNS = [
        r"['\"]\s*;\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|TRUNCATE)",
        r"--\s*$",
        r"UNION\s+(ALL\s+)?SELECT",
        r"OR\s+1\s*=\s*1",
        r"AND\s+1\s*=\s*1",
        r"['\"]\s*OR\s+['\"]",
    ]

    # 越权关键词
    PRIVILEGE_ESCALATION_KEYWORDS = [
        "删库",
        "删除所有",
        "所有用户密码",
        "管理员密码",
        "root密码",
        "系统密码",
        "DROP TABLE",
        "DROP DATABASE",
    ]

    # 动作词（搜索/查一下/上网找）
    ACTION_KEYWORDS = [
        "搜索",
        "查一下",
        "查找",
        "上网找",
        "网上搜",
        "百度一下",
        "谷歌搜索",
        "搜一下",
    ]

    # 外部范围词（网上/百度/谷歌/外部/全网）
    EXTERNAL_SCOPE_KEYWORDS = [
        "网上",
        "百度",
        "谷歌",
        "外部",
        "全网",
        "互联网",
        "外网",
        "搜索引擎",
    ]

    # 问候语模式
    GREETING_PATTERNS = [
        r"^你好[！!。.？?]?$",
        r"^您好[！!。.？?]?$",
        r"^hello[！!。.？?]?$",
        r"^hi[！!。.？?]?$",
        r"^嗨[！!。.？?]?$",
        r"^谢谢[！!。.？?]?$",
        r"^感谢[！!。.？?]?$",
        r"^thank\s*you[！!。.？?]?$",
        r"^thanks[！!。.？?]?$",
        r"^再见[！!。.？?]?$",
        r"^bye[！!。.？?]?$",
    ]

    # 实体格式匹配（SKU-\d+、ORD-\d+、工单号：\d+ 等）
    ENTITY_PATTERNS = [
        (r"SKU-\d+", "database"),
        (r"ORD-\d+", "database"),
        (r"工单号[：:]\s*\d+", "database"),
        (r"订单号[：:]\s*\d+", "database"),
        (r"产品编号[：:]\s*\w+", "database"),
        (r"SKU[：:]\s*\w+", "database"),
    ]

    def __init__(self):
        """初始化前置过滤器。"""
        self.component_name = "PreFilter"

    def filter(self, query: str) -> Optional[RouteDecision]:
        """执行前置过滤。

        Args:
            query: 用户查询

        Returns:
            RouteDecision: 如果命中前置规则，返回路由决策；否则返回 None
        """
        if not query or not query.strip():
            return None

        query_stripped = query.strip()

        # 1. 安全拦截（最高优先级）
        security_decision = self._check_security(query_stripped)
        if security_decision:
            return security_decision

        # 2. 显式外部搜索指令
        external_search_decision = self._check_external_search(query_stripped)
        if external_search_decision:
            return external_search_decision

        # 3. 问候语识别
        greeting_decision = self._check_greeting(query_stripped)
        if greeting_decision:
            return greeting_decision

        # 4. 实体格式匹配
        entity_decision = self._check_entity_format(query_stripped)
        if entity_decision:
            return entity_decision

        # 未命中任何前置规则
        return None

    def _check_security(self, query: str) -> Optional[RouteDecision]:
        """检查安全拦截。

        检测 SQL 注入特征和越权关键词。

        Args:
            query: 用户查询

        Returns:
            RouteDecision: 如果检测到安全风险，返回拒绝决策；否则返回 None
        """
        query_lower = query.lower()

        # 检查 SQL 注入特征
        for pattern in self.SQL_INJECTION_PATTERNS:
            if re.search(pattern, query_lower, re.IGNORECASE):
                logger.warning(f"[PreFilter] 检测到 SQL 注入特征: '{query}'")
                return RouteDecision(
                    target="chitchat",
                    confidence=1.0,
                    source="prefilter",
                    reason="安全拦截：检测到 SQL 注入特征",
                    complexity="simple",
                    metadata={"blocked": True, "reason": "sql_injection"},
                )

        # 检查越权关键词
        for keyword in self.PRIVILEGE_ESCALATION_KEYWORDS:
            if keyword.lower() in query_lower:
                logger.warning(f"[PreFilter] 检测到越权关键词: '{query}'")
                return RouteDecision(
                    target="chitchat",
                    confidence=1.0,
                    source="prefilter",
                    reason=f"安全拦截：检测到越权关键词 '{keyword}'",
                    complexity="simple",
                    metadata={"blocked": True, "reason": "privilege_escalation"},
                )

        return None

    def _check_external_search(self, query: str) -> Optional[RouteDecision]:
        """检查显式外部搜索指令。

        同时命中动作词和外部范围词时，直接路由到 web。

        Args:
            query: 用户查询

        Returns:
            RouteDecision: 如果命中外部搜索指令，返回 web 路由决策；否则返回 None
        """
        query_lower = query.lower()

        # 检查是否同时命中动作词和外部范围词
        has_action = any(kw in query_lower for kw in self.ACTION_KEYWORDS)
        has_external = any(kw in query_lower for kw in self.EXTERNAL_SCOPE_KEYWORDS)

        if has_action and has_external:
            logger.info(f"[PreFilter] 命中显式外部搜索指令: '{query}'")
            return RouteDecision(
                target="web",
                confidence=1.0,
                source="prefilter",
                reason="命中显式外部搜索指令",
                complexity="simple",
                metadata={"explicit_external_search": True},
            )

        return None

    def _check_greeting(self, query: str) -> Optional[RouteDecision]:
        """检查问候语。

        匹配问候模式，直接路由到 chitchat。

        Args:
            query: 用户查询

        Returns:
            RouteDecision: 如果命中问候语，返回 chitchat 路由决策；否则返回 None
        """
        query_lower = query.lower()

        for pattern in self.GREETING_PATTERNS:
            if re.match(pattern, query_lower, re.IGNORECASE):
                logger.info(f"[PreFilter] 命中问候语: '{query}'")
                return RouteDecision(
                    target="chitchat",
                    confidence=1.0,
                    source="prefilter",
                    reason="命中问候语",
                    complexity="simple",
                    metadata={"greeting": True},
                )

        return None

    def _check_entity_format(self, query: str) -> Optional[RouteDecision]:
        """检查实体格式。

        匹配预定义实体格式（如 SKU-\\d+、ORD-\\d+），直接路由到 database。

        Args:
            query: 用户查询

        Returns:
            RouteDecision: 如果命中实体格式，返回 database 路由决策；否则返回 None
        """
        for pattern, target in self.ENTITY_PATTERNS:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                logger.info(f"[PreFilter] 命中实体格式: '{query}' -> {target}")
                return RouteDecision(
                    target=target,
                    confidence=1.0,
                    source="prefilter",
                    reason=f"命中实体格式: {match.group(0)}",
                    complexity="simple",
                    metadata={"entity_match": match.group(0), "entity_type": target},
                )

        return None


# 全局实例
_pre_filter_instance: Optional[PreFilter] = None


def get_pre_filter() -> PreFilter:
    """获取 PreFilter 单例实例。"""
    global _pre_filter_instance
    if _pre_filter_instance is None:
        _pre_filter_instance = PreFilter()
    return _pre_filter_instance
