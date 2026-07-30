"""第2层：LLM 语义路由器。

当第1层规则路由置信度 < 0.7 时，调用 LLM 进行语义路由：
- 使用 Qwen3.5-9B（或同等能力模型）
- 强制输出结构化 JSON
- 支持置信度阈值判断
- 实现超时降级和熔断机制

设计原则：
- 微成本、中等延迟（< 300ms）
- 覆盖 20%-30% 的请求
- 提供语义理解能力
"""

import asyncio
import json
import logging
import time
from typing import Any, Optional

from agent.langgraph.routers.config_loader import IntentRouterConfig, load_config
from agent.langgraph.routers.models import RouteDecision

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """熔断器。

    当 LLM 连续失败超过阈值时，触发熔断，直接降级到规则路由。
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout_s: int = 60):
        """初始化熔断器。

        Args:
            failure_threshold: 失败阈值
            recovery_timeout_s: 恢复超时时间（秒）
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.is_open = False

    def record_failure(self):
        """记录失败。"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            logger.warning(
                f"[CircuitBreaker] 熔断器打开: 连续失败 {self.failure_count} 次"
            )

    def record_success(self):
        """记录成功。"""
        self.failure_count = 0
        self.is_open = False

    def can_execute(self) -> bool:
        """检查是否可以执行。

        Returns:
            bool: 是否可以执行
        """
        if not self.is_open:
            return True

        # 检查是否超过恢复时间
        if time.time() - self.last_failure_time > self.recovery_timeout_s:
            logger.info("[CircuitBreaker] 熔断器恢复，尝试重新执行")
            self.is_open = False
            self.failure_count = 0
            return True

        return False


class LLMRouter:
    """第2层 LLM 语义路由器。"""

    def __init__(self, config: Optional[IntentRouterConfig] = None):
        """初始化 LLM 路由器。

        Args:
            config: 路由配置，如果为 None 则从配置文件加载
        """
        self.component_name = "LLMRouter"
        self._config = config or load_config()
        llm_config = self._config.llm_router
        circuit_breaker_config = llm_config.get("circuit_breaker", {})
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_config.get("failure_threshold", 3),
            recovery_timeout_s=circuit_breaker_config.get("recovery_timeout_s", 60),
        )
        self._timeout_ms = llm_config.get("timeout_ms", 500)
        self._max_retries = llm_config.get("max_retries", 1)
        self._confidence_thresholds = llm_config.get("confidence_thresholds", {})

    async def route(self, query: str, rule_decision: RouteDecision) -> RouteDecision:
        """执行 LLM 语义路由。

        Args:
            query: 用户查询
            rule_decision: 第1层规则路由的决策结果

        Returns:
            RouteDecision: 路由决策
        """
        # 检查熔断器
        if not self._circuit_breaker.can_execute():
            logger.warning("[LLMRouter] 熔断器打开，降级到规则路由")
            return rule_decision

        # 调用 LLM
        start_time = time.time()
        try:
            llm_result = await asyncio.wait_for(
                self._call_llm(query),
                timeout=self._timeout_ms / 1000.0,
            )
            self._circuit_breaker.record_success()
        except asyncio.TimeoutError:
            logger.warning(f"[LLMRouter] LLM 调用超时: {self._timeout_ms}ms")
            self._circuit_breaker.record_failure()
            return rule_decision
        except Exception as e:
            logger.error(f"[LLMRouter] LLM 调用失败: {e}")
            self._circuit_breaker.record_failure()
            return rule_decision

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[LLMRouter] LLM 调用完成: {elapsed_ms}ms")

        # 解析 LLM 结果
        try:
            llm_decision = self._parse_llm_result(llm_result, query)
        except Exception as e:
            logger.error(f"[LLMRouter] 解析 LLM 结果失败: {e}")
            self._circuit_breaker.record_failure()
            return rule_decision

        # 根据置信度阈值判断
        confidence = llm_decision.confidence
        direct_adopt_threshold = self._confidence_thresholds.get("direct_adopt", 0.75)
        needs_clarification_threshold = self._confidence_thresholds.get(
            "needs_clarification", 0.6
        )

        if confidence >= direct_adopt_threshold:
            # 高置信度，直接采纳
            logger.info(
                f"[LLMRouter] 高置信度 ({confidence:.2f})，直接采纳: "
                f"{llm_decision.target}"
            )
            return llm_decision
        elif confidence >= needs_clarification_threshold:
            # 中等置信度，采纳但附带低置信度标签
            logger.info(
                f"[LLMRouter] 中等置信度 ({confidence:.2f})，采纳但附标签: "
                f"{llm_decision.target}"
            )
            llm_decision.metadata["low_confidence_label"] = True
            return llm_decision
        else:
            # 低置信度，需要用户澄清
            logger.info(
                f"[LLMRouter] 低置信度 ({confidence:.2f})，需要澄清"
            )
            llm_decision.metadata["needs_clarification"] = True
            return llm_decision

    async def _call_llm(self, query: str) -> dict[str, Any]:
        """调用 LLM 进行意图分类。

        通过 TenantLLMService 工厂获取 LLM 实例，使用项目的统一 LLM 调用链路，
        自动享受熔断器、重试、metrics 记录等基础设施能力。

        Args:
            query: 用户查询

        Returns:
            dict: LLM 返回的结构化结果
        """
        from agent.langgraph.routers.prompt_manager import get_prompt_manager
        from api.db.services.tenant_llm_service import TenantLLMService
        from common.constants import LLMType
        import json_repair

        # 加载 Prompt 模板
        prompt_manager = get_prompt_manager()
        llm_config = self._config.llm_router
        tenant_id = llm_config.get("tenant_id", "")
        model_name = llm_config.get("model_id", "qwen3.5-9b")
        prompt_version = llm_config.get("prompt_version", "v1")

        # 渲染 Prompt
        prompt = prompt_manager.render_prompt(
            "intent_router",
            prompt_version,
            query=query,
        )

        # 通过项目 LLM 工厂获取模型实例
        model_config = TenantLLMService.get_model_config(
            tenant_id=tenant_id,
            llm_type=LLMType.CHAT.value,
            llm_name=model_name,
        )
        llm_instance = TenantLLMService.model_instance(model_config)

        if not llm_instance:
            raise ValueError(f"无法创建 LLM 实例: {model_name}")

        # 使用 async_chat（非流式），返回 (content, token_count) 元组
        system_prompt = "你是一个意图识别专家。请严格按照 JSON 格式输出。"
        messages = [{"role": "user", "content": prompt}]
        gen_conf = {"temperature": 0.1, "max_tokens": 500}

        content, _token_count = await llm_instance.async_chat(
            system=system_prompt,
            history=messages,
            gen_conf=gen_conf,
        )

        # 检查是否返回错误（async_chat 失败时返回以 **ERROR** 开头的字符串）
        if content.startswith("**ERROR**"):
            raise RuntimeError(f"LLM 调用失败: {content}")

        # 解析 JSON 响应（json_repair 能容忍格式不严格的 JSON）
        result = json_repair.loads(content)
        return result

    def _parse_llm_result(
        self, llm_result: dict[str, Any], query: str
    ) -> RouteDecision:
        """解析 LLM 返回的结构化结果。

        Args:
            llm_result: LLM 返回的结果
            query: 用户查询

        Returns:
            RouteDecision: 路由决策
        """
        primary_intent = llm_result.get("primary_intent", "chitchat")
        confidence = llm_result.get("confidence", 0.5)
        reason = llm_result.get("reason", "")
        complexity = llm_result.get("complexity", "simple")
        sub_intents = llm_result.get("sub_intents", [])
        entities = llm_result.get("entities", {})
        needs_clarification = llm_result.get("needs_clarification", False)
        clarification_question = llm_result.get("clarification_question", "")

        # 映射 primary_intent 到 target
        target_map = {
            "database": "database",
            "rag": "rag",
            "web": "web",
            "hybrid": "hybrid",
            "chitchat": "chitchat",
        }
        target = target_map.get(primary_intent, "chitchat")

        return RouteDecision(
            target=target,
            confidence=confidence,
            source="llm",
            reason=reason,
            complexity=complexity,
            metadata={
                "sub_intents": sub_intents,
                "entities": entities,
                "needs_clarification": needs_clarification,
                "clarification_question": clarification_question,
            },
        )


# 全局实例
_llm_router_instance: Optional[LLMRouter] = None


def get_llm_router(config: Optional[IntentRouterConfig] = None) -> LLMRouter:
    """获取 LLMRouter 单例实例。

    Args:
        config: 路由配置

    Returns:
        LLMRouter: LLM 路由器实例
    """
    global _llm_router_instance
    if _llm_router_instance is None:
        _llm_router_instance = LLMRouter(config)
    return _llm_router_instance
