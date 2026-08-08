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
"""网关层包（微服务拆分 §5.1 / §5.2 / §5.7）。

本包定义 agent-service 与 ragflow-service 之间的网关抽象层，包含：
- RetrieverGateway：检索网关协议（local / remote / shadow 三实现）
- ModelGateway：模型网关协议（替代直接使用 LLMBundle）
- ChatResult：统一 LLM 调用结果（替代裸 str 返回）
- GatewayResolver：进程级网关解析器（按租户运行时配置路由）
- 错误分类：RetrievalServiceError / RetrievalAuthError / RetrievalDataError /
  ModelConfigResolveError / ModelProviderError

PR-0.1 范围：仅协议、错误类型与 GatewayResolver 骨架，不接入业务（零行为变化）。
后续 PR 演进：
- PR-0.2：LocalRetrieverGateway（包装 settings.retriever.retrieval）
- PR-0.3：LLMBundleAdapter（委托 LLMBundle）
- PR-1.3：HttpRetrieverGateway（远程检索 + 熔断 + 重试）
- PR-1.4：HttpConfigResolver（契约 2 + 缓存 + metering）
- PR-1.5：tenant_runtime_config 租户级覆盖
- PR-1.6：ShadowRetrieverGateway / ShadowModelGateway（影子模式）
"""

from agent.langgraph.gateways.errors import (
    GatewayError,
    ModelConfigResolveError,
    ModelProviderError,
    RetrievalAuthError,
    RetrievalCircuitOpenError,
    RetrievalDataError,
    RetrievalServiceError,
    RetrievalTimeoutError,
)
from agent.langgraph.gateways.factory import (
    GatewayResolver,
    get_gateway_resolver,
    reset_gateway_resolver,
)
from agent.langgraph.gateways.llm_bundle_adapter import LLMBundleAdapter
from agent.langgraph.gateways.model import ChatResult, ModelGateway
from agent.langgraph.gateways.retriever import RetrieverGateway

__all__ = [
    # 协议
    "RetrieverGateway",
    "ModelGateway",
    # 结果
    "ChatResult",
    # Stage A 实现
    "LLMBundleAdapter",
    # 解析器
    "GatewayResolver",
    "get_gateway_resolver",
    "reset_gateway_resolver",
    # 错误分类
    "GatewayError",
    "RetrievalServiceError",
    "RetrievalTimeoutError",
    "RetrievalCircuitOpenError",
    "RetrievalAuthError",
    "RetrievalDataError",
    "ModelConfigResolveError",
    "ModelProviderError",
]
