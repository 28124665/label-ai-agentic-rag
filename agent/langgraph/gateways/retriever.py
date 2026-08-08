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
"""检索网关协议（微服务拆分 §5.1）。

定义 RetrieverGateway Protocol，作为 local / remote / shadow 三种实现的统一接口。
RAGTool._retrieve 通过 GatewayResolver.retriever_for(tenant_id) 获取实现，
按租户运行时配置（tenant_runtime_config.retrieval_mode）路由到具体实现。

协议约束（§5.1 实现约束）：
- LocalRetrieverGateway：包装现有 settings.retriever.retrieval 调用，逐行保持原逻辑
  （含 embedding 模型加载、KB 校验、本地 cross_languages）；
- HttpRetrieverGateway：
  - 参数映射严格按 §4.2「关键参数映射」表执行；
  - 本地 cross_languages 步骤跳过，将 cross_languages 透传 API（服务端执行）；
  - 本地 KB 校验/embedding 一致性断言删除——服务端已做，错误透传为 RetrievalDataError；
  - rerank：只传 rerank_id，本地不再调 multilingual_rerank；
  - 响应 chunk → kbinfos 映射按 §4.4 表执行，original_text ?? content fallback。

返回值契约：
- 返回与 settings.retriever.retrieval 同构的 kbinfos（含 chunks/doc_aggs）；
- chunks 中每个 chunk 必须含 original_text ?? content fallback 字段（§4.4）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RetrieverGateway(Protocol):
    """检索网关协议（§5.1）。

    三种实现：
    - LocalRetrieverGateway：进程内检索（包装 settings.retriever.retrieval）；
    - HttpRetrieverGateway：远程检索（HTTP 调 ragflow-service POST /api/v1/retrieval）；
    - ShadowRetrieverGateway：影子模式（local 主路径 + remote 影子路径，§7.1）。

    调用方通过 GatewayResolver.retriever_for(tenant_id) 获取实现，
    按租户运行时配置路由，禁止在构造期绑定实现（§5.7）。
    """

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

        Args:
            query: 查询文本（已繁简转换）
            kb_ids: 知识库 ID 列表
            tenant_id: 租户 ID
            top_k: 返回文档数量
            similarity_threshold: 相似度阈值（默认 0.2）
            keywords_similarity_weight: 关键词相似度权重（默认 0.5）
            rerank_id: Rerank 模型 ID（None 表示不 rerank）
            cross_languages: 跨语言扩展目标语言列表（None 或空列表表示不扩展）

        Returns:
            dict: 与 settings.retriever.retrieval 同构的 kbinfos，含 chunks/doc_aggs。

        Raises:
            RetrievalServiceError: 超时/5xx/熔断（HttpRetrieverGateway）
            RetrievalAuthError: 401/403（HttpRetrieverGateway）
            RetrievalDataError: 400/数据错误（HttpRetrieverGateway）
            Exception: local 模式维持现状抛出（如 "No dataset is selected."）
        """
        ...
