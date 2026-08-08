"""第0.5层：复杂度闸门。

在规则路由之前做正则复杂度检测，复杂问题直接跳过规则路由，
进入 LLM 语义路由，避免规则路由误拦截复杂请求。

设计原则：
- 只看问题结构特征，不看意图
- 正则匹配，< 1ms
- 任何信号命中 → 复杂 → 跳过规则路由
- 全部未命中 → 简单 → 进入规则路由
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.langgraph.routers.config_loader import IntentRouterConfig, load_config

logger = logging.getLogger(__name__)


@dataclass
class ComplexityResult:
    """复杂度检测结果。

    Attributes:
        is_complex: 是否复杂
        triggered_signals: 触发的信号名称列表
        confidence: 复杂度判断的置信度（触发信号越多越高）
    """

    is_complex: bool
    triggered_signals: list[str] = field(default_factory=list)
    confidence: float = 0.0


class _MultiToolSignal:
    """多工具信号：同时命中 DB + RAG 关键词才算复杂。"""

    def __init__(self, db_keywords: list[str], rag_keywords: list[str]):
        # 取前 15 个高频词，避免正则过长
        db_top = db_keywords[:15]
        rag_top = rag_keywords[:15]
        self._db_pattern = (
            re.compile("|".join(re.escape(w) for w in db_top))
            if db_top
            else None
        )
        self._rag_pattern = (
            re.compile("|".join(re.escape(w) for w in rag_top))
            if rag_top
            else None
        )

    def search(self, query: str) -> bool:
        """检测是否同时命中 DB 和 RAG 关键词。"""
        if not self._db_pattern or not self._rag_pattern:
            return False
        has_db = self._db_pattern.search(query) is not None
        has_rag = self._rag_pattern.search(query) is not None
        return has_db and has_rag


class ComplexityGate:
    """第0.5层复杂度闸门。

    通过正则检测问题的结构特征，判断是否为复杂问题。
    复杂问题直接跳过规则路由，进入 LLM 语义路由。
    """

    def __init__(self, config: Optional[IntentRouterConfig] = None):
        """初始化复杂度闸门。

        Args:
            config: 路由配置，如果为 None 则从配置文件加载
        """
        self.component_name = "ComplexityGate"
        self._config = config or load_config()
        self._signals = self._compile_signals()

    def check(self, query: str) -> ComplexityResult:
        """检测查询复杂度。

        任何信号命中即判定为复杂，触发信号越多置信度越高。

        Args:
            query: 用户查询

        Returns:
            ComplexityResult: 复杂度检测结果
        """
        if not query or not query.strip():
            return ComplexityResult(is_complex=False)

        triggered = []

        for name, signal in self._signals.items():
            if signal is None:
                continue
            matcher = signal if callable(signal) else signal.search
            if matcher(query):
                triggered.append(name)

        is_complex = len(triggered) > 0
        # 触发信号越多，复杂度置信度越高（1个=0.3, 2个=0.6, 3个=0.9，封顶0.9）
        confidence = min(len(triggered) * 0.3, 0.9) if is_complex else 0.0

        if is_complex:
            logger.info(
                f"[ComplexityGate] 复杂信号触发: '{query[:50]}' -> "
                f"{triggered} (confidence={confidence:.2f})"
            )

        return ComplexityResult(
            is_complex=is_complex,
            triggered_signals=triggered,
            confidence=confidence,
        )

    def _compile_signals(self) -> dict[str, Any]:
        """从配置编译复杂度信号。

        Returns:
            dict: 信号名 → 匹配器（正则对象或可调用对象）
        """
        gate_config = self._config.get("complexity_gate", {})
        if not gate_config.get("enabled", True):
            return {}

        signals_config = gate_config.get("signals", {})
        rule_keywords = self._config.rule_router.get("keywords", {})
        signals: dict[str, Any] = {}

        # 信号1：长查询
        long_cfg = signals_config.get("long_query", {})
        if long_cfg.get("enabled", True):
            threshold = long_cfg.get("threshold", 50)
            signals["long_query"] = re.compile(f".{{{threshold},}}")

        # 信号2：多子句（2+ 个分隔符）
        clause_cfg = signals_config.get("multi_clause", {})
        if clause_cfg.get("enabled", True):
            min_count = clause_cfg.get("min_count", 2)
            signals["multi_clause"] = re.compile(
                f"([^，,；;]*[，,；;]){{{min_count},}}"
            )

        # 信号3：多问号（2+ 个问号，非连续也算）
        # 使用 (?:[^？?]*[？?]){min_count,} 匹配 min_count+ 个问号（可分布在任意位置）
        question_cfg = signals_config.get("multi_question", {})
        if question_cfg.get("enabled", True):
            min_count = question_cfg.get("min_count", 2)
            signals["multi_question"] = re.compile(
                f"(?:[^？?]*[？?]){{{min_count},}}"
            )

        # 信号4：推理标记
        reasoning_cfg = signals_config.get("reasoning_markers", {})
        if reasoning_cfg.get("enabled", True):
            markers = reasoning_cfg.get(
                "keywords",
                ["分析", "对比", "比较", "为什么", "推导", "评估", "综合考虑"],
            )
            if markers:
                signals["reasoning_markers"] = re.compile(
                    "|".join(re.escape(m) for m in markers)
                )

        # 信号5：多工具关键词（同时命中 DB + RAG）
        multi_tool_cfg = signals_config.get("multi_tool", {})
        if multi_tool_cfg.get("enabled", True):
            db_words = rule_keywords.get("db_bias", [])
            rag_words = rule_keywords.get("rag_bias", [])
            if db_words and rag_words:
                signals["multi_tool"] = _MultiToolSignal(db_words, rag_words)

        # 信号6：递进结构
        progressive_cfg = signals_config.get("progressive", {})
        if progressive_cfg.get("enabled", True):
            patterns = progressive_cfg.get(
                "patterns",
                ["先.{1,20}再", "首先.{1,20}然后", "第一步", "接着", "最后"],
            )
            if patterns:
                signals["progressive"] = re.compile(
                    "|".join(patterns)
                )

        return signals


# 全局实例
_complexity_gate_instance: Optional[ComplexityGate] = None


def get_complexity_gate(
    config: Optional[IntentRouterConfig] = None,
) -> ComplexityGate:
    """获取 ComplexityGate 单例实例。

    Args:
        config: 路由配置

    Returns:
        ComplexityGate: 复杂度闸门实例
    """
    global _complexity_gate_instance
    if _complexity_gate_instance is None:
        _complexity_gate_instance = ComplexityGate(config)
    return _complexity_gate_instance
