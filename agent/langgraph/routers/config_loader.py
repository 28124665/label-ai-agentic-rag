"""路由配置加载器。

支持从 YAML 文件加载路由配置，并提供热更新机制。
"""

import logging
import os
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


class IntentRouterConfig:
    """意图路由配置。"""

    def __init__(self, config_data: dict[str, Any]):
        """初始化配置。

        Args:
            config_data: 配置数据字典
        """
        self._config = config_data
        self._last_modified = 0

    @property
    def pre_filter(self) -> dict[str, Any]:
        """获取前置过滤配置。"""
        return self._config.get("pre_filter", {})

    @property
    def rule_router(self) -> dict[str, Any]:
        """获取规则路由配置。"""
        return self._config.get("rule_router", {})

    @property
    def llm_router(self) -> dict[str, Any]:
        """获取 LLM 路由配置。"""
        return self._config.get("llm_router", {})

    @property
    def planner(self) -> dict[str, Any]:
        """获取 Planner 配置。"""
        return self._config.get("planner", {})

    @property
    def thresholds(self) -> dict[str, float]:
        """获取阈值配置。"""
        return self._config.get("thresholds", {})

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项。

        Args:
            key: 配置键
            default: 默认值

        Returns:
            配置值
        """
        return self._config.get(key, default)


class ConfigLoader:
    """配置加载器。

    支持从 YAML 文件加载配置，并提供热更新机制。
    """

    def __init__(self, config_path: str = "config/intent_router.yaml"):
        """初始化配置加载器。

        Args:
            config_path: 配置文件路径
        """
        self._config_path = config_path
        self._config: Optional[IntentRouterConfig] = None
        self._last_modified = 0

    def load(self) -> IntentRouterConfig:
        """加载配置。

        Returns:
            IntentRouterConfig: 配置对象
        """
        if not os.path.exists(self._config_path):
            logger.warning(f"[ConfigLoader] 配置文件不存在: {self._config_path}，使用默认配置")
            return self._get_default_config()

        try:
            # 检查文件是否被修改
            current_modified = os.path.getmtime(self._config_path)
            if self._config and current_modified == self._last_modified:
                # 文件未修改，返回缓存的配置
                return self._config

            # 加载配置文件
            with open(self._config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}

            self._config = IntentRouterConfig(config_data)
            self._last_modified = current_modified

            logger.info(f"[ConfigLoader] 配置加载成功: {self._config_path}")
            return self._config

        except Exception as e:
            logger.error(f"[ConfigLoader] 配置加载失败: {e}")
            return self._get_default_config()

    def reload(self) -> IntentRouterConfig:
        """强制重新加载配置（热更新）。

        Returns:
            IntentRouterConfig: 配置对象
        """
        self._last_modified = 0
        return self.load()

    def _get_default_config(self) -> IntentRouterConfig:
        """获取默认配置。

        Returns:
            IntentRouterConfig: 默认配置对象
        """
        default_config = {
            "pre_filter": {
                "enabled": True,
                "security_check": True,
                "external_search_check": True,
                "greeting_check": True,
                "entity_format_check": True,
            },
            "rule_router": {
                "enabled": True,
                "keywords": {
                    "db_bias": [
                        "多少", "总额", "总", "占比", "排名", "top", "环比", "同比",
                        "最大值", "最小值", "统计", "汇总", "平均", "求和",
                        "总计", "计算", "查询数据", "数据量", "查询", "数据",
                        "产线", "oee", "销售额", "业绩", "产量",
                    ],
                    "rag_bias": [
                        "定义", "解释", "含义", "区别", "联系", "影响", "原因",
                        "背景", "什么是", "是什么", "如何", "为什么", "怎么", "请问",
                        "概念", "原理", "方法", "流程", "治理", "改善",
                    ],
                    "external_scope": [
                        "网上", "百度", "谷歌", "外部", "全网", "互联网",
                        "外网", "搜索引擎",
                    ],
                    "freshness_keywords": [
                        "今天", "最新", "实时", "current", "latest", "trending",
                        "现在", "当前", "刚刚",
                    ],
                    "historical_keywords": [
                        "去年", "历史", "往年", "过去", "以前", "之前",
                        "去年", "上年", "上季度",
                    ],
                },
                "confidence": {
                    "high": 0.8,
                    "medium": 0.6,
                    "low": 0.4,
                },
            },
            "llm_router": {
                "enabled": True,
                "tenant_id": "",
                "model_id": "qwen3.5-9b",
                "prompt_version": "v1",
                "timeout_ms": 500,
                "max_retries": 1,
                "circuit_breaker": {
                    "failure_threshold": 3,
                    "recovery_timeout_s": 60,
                },
                "confidence_thresholds": {
                    "direct_adopt": 0.75,
                    "adopt_with_label": 0.6,
                    "needs_clarification": 0.6,
                },
            },
            "planner": {
                "enabled": True,
                "tenant_id": "",
                "model_id": "qwen3.6-27b",
                "prompt_version": "v1",
                "timeout_ms": 5000,
                "max_steps": 10,
            },
            "thresholds": {
                "rule_to_llm": 0.7,
                "llm_to_planner": 0.8,
                "needs_clarification": 0.6,
            },
        }

        return IntentRouterConfig(default_config)


# 全局配置加载器实例
_config_loader_instance: Optional[ConfigLoader] = None


def get_config_loader(config_path: str = "config/intent_router.yaml") -> ConfigLoader:
    """获取配置加载器单例实例。

    Args:
        config_path: 配置文件路径

    Returns:
        ConfigLoader: 配置加载器实例
    """
    global _config_loader_instance
    if _config_loader_instance is None:
        _config_loader_instance = ConfigLoader(config_path)
    return _config_loader_instance


def load_config(config_path: str = "config/intent_router.yaml") -> IntentRouterConfig:
    """加载配置（便捷函数）。

    Args:
        config_path: 配置文件路径

    Returns:
        IntentRouterConfig: 配置对象
    """
    loader = get_config_loader(config_path)
    return loader.load()
