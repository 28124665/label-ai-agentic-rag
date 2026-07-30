"""意图路由三层递进架构。

提供从简单到复杂的多层路由能力：
- 第0层：前置过滤（安全拦截、显式指令、问候语、实体格式）
- 第1层：规则路由（关键词匹配 + 置信度打分）
- 第2层：LLM 语义路由（结构化输出 + 降级机制）
- 第3层：Planner 复杂任务规划（DAG 执行计划）
"""

from agent.langgraph.routers.models import RouteDecision, PlanStep, ExecutionPlan
from agent.langgraph.routers.pre_filter import PreFilter
from agent.langgraph.routers.rule_router import RuleRouter
from agent.langgraph.routers.llm_router import LLMRouter
from agent.langgraph.routers.planner import Planner
from agent.langgraph.routers.config_loader import IntentRouterConfig, load_config
from agent.langgraph.routers.prompt_manager import PromptManager, get_prompt_manager

__all__ = [
    "RouteDecision",
    "PlanStep",
    "ExecutionPlan",
    "PreFilter",
    "RuleRouter",
    "LLMRouter",
    "Planner",
    "IntentRouterConfig",
    "load_config",
    "PromptManager",
    "get_prompt_manager",
]
