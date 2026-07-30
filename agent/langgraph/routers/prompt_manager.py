"""Prompt 版本管理器。

支持多版本 Prompt 模板的加载和切换。
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class PromptManager:
    """Prompt 版本管理器。"""

    def __init__(self, prompt_dir: str = "config/prompts"):
        """初始化 Prompt 管理器。

        Args:
            prompt_dir: Prompt 模板目录
        """
        self._prompt_dir = prompt_dir
        self._cache: dict[str, str] = {}

    def load_prompt(self, prompt_name: str, version: str = "v1") -> str:
        """加载指定版本的 Prompt 模板。

        Args:
            prompt_name: Prompt 名称（如 "intent_router"）
            version: 版本号（如 "v1"）

        Returns:
            str: Prompt 模板内容
        """
        cache_key = f"{prompt_name}_{version}"

        # 检查缓存
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 构建文件路径
        filename = f"{prompt_name}_{version}.txt"
        filepath = os.path.join(self._prompt_dir, filename)

        if not os.path.exists(filepath):
            logger.warning(f"[PromptManager] Prompt 文件不存在: {filepath}，使用默认模板")
            # 返回默认模板
            default_template = self._get_default_template(prompt_name)
            self._cache[cache_key] = default_template
            return default_template

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                template = f.read()

            self._cache[cache_key] = template
            logger.info(f"[PromptManager] Prompt 加载成功: {filename}")
            return template

        except Exception as e:
            logger.error(f"[PromptManager] Prompt 加载失败: {e}")
            default_template = self._get_default_template(prompt_name)
            self._cache[cache_key] = default_template
            return default_template

    def render_prompt(self, prompt_name: str, version: str = "v1", **kwargs) -> str:
        """渲染 Prompt 模板。

        Args:
            prompt_name: Prompt 名称
            version: 版本号
            **kwargs: 模板变量

        Returns:
            str: 渲染后的 Prompt
        """
        template = self.load_prompt(prompt_name, version)

        # 替换模板变量
        try:
            rendered = template.format(**kwargs)
            return rendered
        except KeyError as e:
            logger.error(f"[PromptManager] 模板变量缺失: {e}")
            raise

    def clear_cache(self):
        """清空缓存。"""
        self._cache.clear()
        logger.info("[PromptManager] Prompt 缓存已清空")

    def _get_default_template(self, prompt_name: str) -> str:
        """获取默认模板。

        Args:
            prompt_name: Prompt 名称

        Returns:
            str: 默认模板
        """
        default_templates = {
            "intent_router": """你是一个意图识别专家。请分析用户查询，判断其主要意图。

## 用户查询
{query}

## 分类规则
1. **database**: 查询数据、统计、聚合、表结构、SQL 相关
2. **rag**: 查询知识、概念、定义、解释、文档内容
3. **web**: 查询外部信息、实时数据、新闻、网上搜索
4. **hybrid**: 同时包含数据和知识查询需求
5. **chitchat**: 闲聊、问候、无明确任务

## 输出格式
请严格输出 JSON 格式：
{{
  "primary_intent": "database|rag|web|hybrid|chitchat",
  "confidence": 0.0-1.0,
  "reason": "路由理由",
  "complexity": "simple|moderate|complex",
  "sub_intents": [],
  "entities": {{}},
  "needs_clarification": false,
  "clarification_question": ""
}}
""",
            "planner": """你是一个任务规划专家。请将复杂任务拆解为可执行的步骤。

## 用户查询
{query}

## 上下文信息
{context}

## 输出格式
请严格输出 JSON 格式：
{{
  "plan_id": "plan_xxx",
  "steps": [
    {{
      "step_id": "step1",
      "tool": "database|rag|web",
      "args": {{"query": "子查询"}},
      "depends_on": [],
      "can_parallel": false
    }}
  ],
  "estimated_tokens": 1000,
  "fallback_strategy": "sequential"
}}
""",
        }

        return default_templates.get(prompt_name, "默认模板：{query}")


# 全局实例
_prompt_manager_instance: Optional[PromptManager] = None


def get_prompt_manager(prompt_dir: str = "config/prompts") -> PromptManager:
    """获取 PromptManager 单例实例。

    Args:
        prompt_dir: Prompt 模板目录

    Returns:
        PromptManager: PromptManager 实例
    """
    global _prompt_manager_instance
    if _prompt_manager_instance is None:
        _prompt_manager_instance = PromptManager(prompt_dir)
    return _prompt_manager_instance
