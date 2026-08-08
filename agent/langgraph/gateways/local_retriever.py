#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""本地检索网关（微服务拆分 §5.1，PR-0.2）。

LocalRetrieverGateway 包装现有 settings.retriever.retrieval 调用，逐行保持原逻辑：
- embedding 模型加载（LLMBundle + get_model_config_by_type_and_name）
- KB 校验（KnowledgebaseService.get_by_ids + embedding 一致性断言）
- settings.retriever.retrieval 调用（BM25 + 向量混合检索）

与 HttpRetrieverGateway 的差异（§5.1 实现约束）：
- cross_languages：local 模式由 RAGTool 步骤 1 本地执行，gateway 内不重复；
- rerank：local 模式由 RAGTool 步骤 2c 调 multilingual_rerank，gateway 内不做；
- KB 校验：local 模式保留，remote 模式删除（服务端已校验）。

PR-0.2 约束（零行为变化）：
- 本文件仅搬运 RAGTool._retrieve 的现有逻辑，不改变任何行为；
- rerank_id / cross_languages 参数接收但 local 模式不使用（保持与现状一致）。
"""

from __future__ import annotations

import logging

from api.utils.circuit_breaker import CircuitBreakerConfig, CircuitBreakerRegistry
from common import settings

logger = logging.getLogger(__name__)


class LocalRetrieverGateway:
    """本地检索网关（包装 settings.retriever.retrieval）。

    实现 RetrieverGateway 协议（§5.1）。
    逻辑原样搬运自 RAGTool._retrieve，零行为变化。

    Phase 1 Task 3 增强：集成 CircuitBreaker 状态机，
    当检索服务连续失败时触发熔断，快速失败返回 RetrievalServiceError，
    避免持续重试加速系统崩溃（详见 docs/rag_enhancement_integration_design.md §4.3）。
    """

    # 熔断器配置（与设计文档 §4.3.3 分级配置一致，本任务先硬编码默认值）
    # - failure_threshold=3：连续失败 3 次触发熔断
    # - recovery_timeout=30：熔断 30 秒后进入半开状态
    # - half_open_max_calls=2：半开状态最多探测 2 次
    _CIRCUIT_BREAKER_CONFIG = CircuitBreakerConfig(
        failure_threshold=3,
        recovery_timeout=30,
        half_open_max_calls=2,
    )

    def __init__(self) -> None:
        # 通过 CircuitBreakerRegistry 获取或创建名为 "retrieval" 的熔断器
        # Registry 为单例模式，多次获取返回同一实例，便于跨节点共享熔断状态
        self._breaker = CircuitBreakerRegistry().get(
            "retrieval",
            config=self._CIRCUIT_BREAKER_CONFIG,
        )

    async def retrieve(
        self,
        *,
        query: str,
        kb_ids: list[str],
        tenant_id: str,
        top_k: int,
        similarity_threshold: float,
        keywords_similarity_weight: float,
        rerank_id: str | None,
        cross_languages: list[str] | None,
    ) -> dict:
        """执行混合检索（BM25 + 向量）。

        原样搬运自 RAGTool._retrieve，零行为变化。
        rerank_id / cross_languages 参数接收但 local 模式不使用：
        - rerank 由 RAGTool 步骤 2c 调 multilingual_rerank 完成；
        - cross_languages 由 RAGTool 步骤 1 本地完成后传入已扩展的 query。

        Args:
            query: 查询文本（已繁简转换 / 已跨语言扩展）
            kb_ids: 知识库 ID 列表
            tenant_id: 租户 ID
            top_k: 返回文档数量
            similarity_threshold: 相似度阈值
            keywords_similarity_weight: 关键词相似度权重
            rerank_id: Rerank 模型 ID（local 模式不使用，由 RAGTool 处理）
            cross_languages: 跨语言扩展目标语言（local 模式不使用，由 RAGTool 处理）

        Returns:
            dict: 与 settings.retriever.retrieval 同构的 kbinfos，含 chunks/doc_aggs。

        Raises:
            Exception: 维持现状抛出（如 "No dataset is selected." /
                       embedding 不一致断言失败）。
        """
        from api.db.services.knowledgebase_service import KnowledgebaseService
        from api.db.services.llm_service import LLMBundle
        from api.db.joint_services.tenant_model_service import (
            get_model_config_by_type_and_name,
        )
        from agent.langgraph.gateways.errors import RetrievalServiceError
        from common.constants import LLMType

        # 获取知识库信息
        kbs = KnowledgebaseService.get_by_ids(kb_ids)
        if not kbs:
            raise Exception("No dataset is selected.")

        # 获取 Embedding 模型
        embd_nms = list(set([kb.embd_id for kb in kbs]))
        assert len(embd_nms) == 1, "Knowledge bases use different embedding models."

        actual_tenant_id = tenant_id or kbs[0].tenant_id
        embd_model_config = get_model_config_by_type_and_name(
            actual_tenant_id, LLMType.EMBEDDING, embd_nms[0]
        )
        embd_mdl = LLMBundle(actual_tenant_id, embd_model_config)

        # ===== 熔断器状态检查（Phase 1 Task 3）=====
        # 熔断器 OPEN 时快速失败，避免持续重试加速系统崩溃
        # allow_request() 内部会处理 OPEN→HALF_OPEN 的恢复转换（recovery_timeout 到期后）
        prev_state = self._breaker.state
        if not self._breaker.allow_request():
            # 熔断器处于 OPEN 状态（或 HALF_OPEN 探测名额已满），记录 WARNING 并快速失败
            logger.warning(
                "[LocalRetrieverGateway] 检索服务熔断中，快速失败: "
                f"failure_count={self._breaker._failure_count}, "
                f"recovery_timeout={self._breaker.config.recovery_timeout}s"
            )
            raise RetrievalServiceError(
                "Retrieval circuit breaker is OPEN"
            )
        # 记录状态变化 INFO 日志（OPEN→HALF_OPEN 由 allow_request 内部触发）
        new_state = self._breaker.state
        if new_state != prev_state:
            logger.info(
                "[LocalRetrieverGateway] 熔断器状态变化: "
                f"{prev_state.value} → {new_state.value}"
            )

        # 执行检索
        # 注意：settings.retriever.retrieval 的 weight 参数是 vector_similarity_weight，
        # 即向量权重；而 RAGToolInput 的 keywords_similarity_weight 是关键词权重。
        # 关系：vector_similarity_weight = 1 - keywords_similarity_weight
        try:
            kbinfos = await settings.retriever.retrieval(
                query,
                embd_mdl,
                [kb.tenant_id for kb in kbs],
                kb_ids,
                1,  # page
                top_k,
                similarity_threshold,
                1 - keywords_similarity_weight,
                aggs=False,
            )
        except Exception:
            # 检索失败，记录熔断器失败（CLOSED→OPEN 或 HALF_OPEN→OPEN 可能在此触发）
            prev_state = self._breaker.state
            self._breaker.record_failure()
            new_state = self._breaker.state
            if new_state != prev_state:
                logger.info(
                    "[LocalRetrieverGateway] 熔断器状态变化（失败触发）: "
                    f"{prev_state.value} → {new_state.value}"
                )
            raise

        # 检索成功，记录熔断器成功（HALF_OPEN→CLOSED 可能在此触发）
        prev_state = self._breaker.state
        self._breaker.record_success()
        new_state = self._breaker.state
        if new_state != prev_state:
            logger.info(
                "[LocalRetrieverGateway] 熔断器状态变化（成功恢复）: "
                f"{prev_state.value} → {new_state.value}"
            )

        return kbinfos
