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
"""网关解析器（微服务拆分 §5.7，组合根与每租户运行时注入）。

GatewayResolver 是进程级单例，持有全部 Gateway 实现与配置缓存，按请求参数路由。
解决「模式开关（local/remote/shadow）与检索凭证都是每租户维度」的问题——
解析发生在每次调用，而非构造期（§5.7）。

PR-0.1 范围（本文件）：
- 仅定义 GatewayResolver 骨架，不接入业务；
- 仅 local 路由（remote/shadow 实现在 PR-1.3/PR-1.6 落地）；
- 不依赖 LocalRetrieverGateway / LLMBundleAdapter（PR-0.2/PR-0.3 落地），
  通过构造参数注入实现解耦。

后续 PR 演进：
- PR-0.2：注入 LocalRetrieverGateway；
- PR-0.3：注入 LLMBundleAdapter；
- PR-1.5：接入 tenant_runtime_config 租户级覆盖 + 60s 缓存收敛；
- PR-1.3/PR-1.6：注入 HttpRetrieverGateway / ShadowRetrieverGateway。

约束（§5.7）：
- RAGTool.__init__ 接收 resolver: GatewayResolver | None（默认取进程级单例）；
- invoke() 内每次执行 resolver.retriever_for(tenant_id)——模式与凭证按请求解析；
- tenant_runtime_config 变更经 60s 缓存收敛，无需重启；
- HttpRetrieverGateway 不在构造时持凭证；每次请求按 tenant_id 查凭证表（60s 缓存）；
- 禁止在进程启动/单例构造期绑定租户级模式或凭证（附录 D #13）。
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from agent.langgraph.gateways.model import ModelGateway
from agent.langgraph.gateways.retriever import RetrieverGateway
from agent.langgraph.gateways.verifier import VerifierGateway

logger = logging.getLogger(__name__)

# 运行时模式（§5.6 环境变量）
RetrievalMode = Literal["local", "remote", "shadow"]
ModelMode = Literal["local", "remote", "shadow"]
VerifierMode = Literal["local", "remote", "shadow"]


def _env_mode(var_name: str, default: str = "local") -> str:
    """从环境变量读取模式开关（§5.6）。

    PR-0.1 仅 local；PR-1.5 接入 tenant_runtime_config 租户级覆盖。
    """
    value = os.environ.get(var_name, default)
    if value not in ("local", "remote", "shadow"):
        logger.warning(
            "[GatewayResolver] 无效的 %s=%s，回退到 %s", var_name, value, default
        )
        return default
    return value


class GatewayResolver:
    """进程级网关解析器（§5.7）。

    持有全部 Gateway 实现与配置缓存，按请求参数路由到具体实现。
    解析发生在每次调用，而非构造期——模式与凭证按请求解析。

    PR-0.1 骨架约束：
    - 仅 local 路由（remote/shadow 实现待后续 PR）；
    - gateway 实例通过构造参数注入，默认 None（PR-0.2/0.3 填充）；
    - 未注入时 retriever_for/model_for 抛 NotImplementedError（快速失败，禁止静默降级）。
    """

    def __init__(
        self,
        *,
        local_retriever: RetrieverGateway | None = None,
        local_model: ModelGateway | None = None,
        http_retriever: RetrieverGateway | None = None,
        shadow_retriever: RetrieverGateway | None = None,
        http_model: ModelGateway | None = None,
        shadow_model: ModelGateway | None = None,
        local_verifier: VerifierGateway | None = None,
        http_verifier: VerifierGateway | None = None,
        shadow_verifier: VerifierGateway | None = None,
    ) -> None:
        """初始化 GatewayResolver。

        Args:
            local_retriever: 本地检索网关（PR-0.2 注入 LocalRetrieverGateway）
            local_model: 本地模型网关（PR-0.3 注入 LLMBundleAdapter）
            http_retriever: 远程检索网关（PR-1.3 注入 HttpRetrieverGateway）
            shadow_retriever: 影子检索网关（PR-1.6 注入 ShadowRetrieverGateway）
            http_model: 远程模型网关（PR-1.4 注入 HttpConfigResolver 包装）
            shadow_model: 影子模型网关（PR-1.6 注入 ShadowModelGateway）
            local_verifier: 本地验证器网关（注入 LocalVerifierGateway）
            http_verifier: 远程验证器网关（微服务阶段注入 HttpVerifierGateway）
            shadow_verifier: 影子验证器网关（灰度阶段注入 ShadowVerifierGateway）
        """
        self._local_retriever = local_retriever
        self._local_model = local_model
        self._http_retriever = http_retriever
        self._shadow_retriever = shadow_retriever
        self._http_model = http_model
        self._shadow_model = shadow_model
        self._local_verifier = local_verifier
        self._http_verifier = http_verifier
        self._shadow_verifier = shadow_verifier
        # PR-1.5 接入：self._runtime_config_cache = TTLCache(ttl=60)

    async def retriever_for(self, tenant_id: str) -> RetrieverGateway:
        """按租户运行时配置解析检索网关（§5.7）。

        模式优先级：tenant_runtime_config.retrieval_mode > 环境变量 RETRIEVAL_MODE。
        PR-0.1：仅 local 模式，tenant_runtime_config 待 PR-1.5 接入。

        Args:
            tenant_id: 租户 ID

        Returns:
            RetrieverGateway: 对应模式的检索网关实现。

        Raises:
            NotImplementedError: 对应模式的网关未注入（PR-0.1 仅 local 可用）。
        """
        mode = await self._resolve_retrieval_mode(tenant_id)

        if mode == "local":
            if self._local_retriever is None:
                raise NotImplementedError(
                    "LocalRetrieverGateway 未注入（待 PR-0.2 落地）"
                )
            return self._local_retriever

        if mode == "remote":
            if self._http_retriever is None:
                raise NotImplementedError(
                    "HttpRetrieverGateway 未注入（待 PR-1.3 落地）"
                )
            return self._http_retriever

        if mode == "shadow":
            if self._shadow_retriever is None:
                raise NotImplementedError(
                    "ShadowRetrieverGateway 未注入（待 PR-1.6 落地）"
                )
            return self._shadow_retriever

        # 理论不可达（_resolve_retrieval_mode 已校验）
        raise ValueError(f"未知的 retrieval_mode: {mode}")

    async def model_for(self, tenant_id: str) -> ModelGateway:
        """按租户运行时配置解析模型网关（§5.7）。

        模式优先级：tenant_runtime_config.model_mode > 环境变量 MODEL_MODE。
        PR-0.1：仅 local 模式，tenant_runtime_config 待 PR-1.5 接入。

        Args:
            tenant_id: 租户 ID

        Returns:
            ModelGateway: 对应模式的模型网关实现。

        Raises:
            NotImplementedError: 对应模式的网关未注入（PR-0.1 仅 local 可用）。
        """
        mode = await self._resolve_model_mode(tenant_id)

        if mode == "local":
            if self._local_model is None:
                raise NotImplementedError(
                    "LLMBundleAdapter 未注入（待 PR-0.3 落地）"
                )
            return self._local_model

        if mode == "remote":
            if self._http_model is None:
                raise NotImplementedError(
                    "HttpConfigResolver 包装未注入（待 PR-1.4 落地）"
                )
            return self._http_model

        if mode == "shadow":
            if self._shadow_model is None:
                raise NotImplementedError(
                    "ShadowModelGateway 未注入（待 PR-1.6 落地）"
                )
            return self._shadow_model

        raise ValueError(f"未知的 model_mode: {mode}")

    async def _resolve_retrieval_mode(self, tenant_id: str) -> RetrievalMode:
        """解析检索模式（PR-0.1 仅环境变量，PR-1.5 接入租户级覆盖）。

        优先级：tenant_runtime_config.retrieval_mode > 环境变量 RETRIEVAL_MODE。
        """
        # PR-1.5 TODO:
        #   cfg = await self._runtime_config(tenant_id)
        #   return cfg.retrieval_mode or _env_mode("RETRIEVAL_MODE")
        return _env_mode("RETRIEVAL_MODE", "local")

    async def _resolve_model_mode(self, tenant_id: str) -> ModelMode:
        """解析模型模式（PR-0.1 仅环境变量，PR-1.5 接入租户级覆盖）。

        优先级：tenant_runtime_config.model_mode > 环境变量 MODEL_MODE。
        """
        # PR-1.5 TODO:
        #   cfg = await self._runtime_config(tenant_id)
        #   return cfg.model_mode or _env_mode("MODEL_MODE")
        return _env_mode("MODEL_MODE", "local")

    async def verifier_for(self, tenant_id: str) -> VerifierGateway:
        """按租户运行时配置解析验证器网关（RAG 增强能力融合 §3.2.4）。

        模式优先级：tenant_runtime_config.verifier_mode > 环境变量 VERIFIER_MODE。
        当前仅环境变量，tenant_runtime_config 待 PR-1.5 接入。

        Args:
            tenant_id: 租户 ID

        Returns:
            VerifierGateway: 对应模式的验证器网关实现。

        Raises:
            NotImplementedError: 对应模式的网关未注入。
        """
        mode = await self._resolve_verifier_mode(tenant_id)

        if mode == "local":
            if self._local_verifier is None:
                raise NotImplementedError(
                    "LocalVerifierGateway 未注入"
                )
            return self._local_verifier

        if mode == "remote":
            if self._http_verifier is None:
                raise NotImplementedError(
                    "HttpVerifierGateway 未注入（微服务阶段配置 VERIFIER_REMOTE_URL）"
                )
            return self._http_verifier

        if mode == "shadow":
            if self._shadow_verifier is None:
                raise NotImplementedError(
                    "ShadowVerifierGateway 未注入（灰度阶段实现）"
                )
            return self._shadow_verifier

        # 理论不可达（_resolve_verifier_mode 已校验）
        raise ValueError(f"未知的 verifier_mode: {mode}")

    async def _resolve_verifier_mode(self, tenant_id: str) -> VerifierMode:
        """解析验证器模式（当前仅环境变量，PR-1.5 接入租户级覆盖）。

        优先级：tenant_runtime_config.verifier_mode > 环境变量 VERIFIER_MODE。
        """
        # PR-1.5 TODO:
        #   cfg = await self._runtime_config(tenant_id)
        #   return cfg.verifier_mode or _env_mode("VERIFIER_MODE")
        return _env_mode("VERIFIER_MODE", "local")


# ========== 进程级单例 ==========

_resolver_instance: GatewayResolver | None = None


def get_gateway_resolver() -> GatewayResolver:
    """获取进程级 GatewayResolver 单例。

    PR-0.2：注入 LocalRetrieverGateway（local 模式检索实现）；
    PR-0.3：注入 LLMBundleAdapter（local 模式模型实现）；
    RAG 增强：注入 LocalVerifierGateway（local 模式验证器实现）；
    PR-1.3/1.4/1.6：注入 Http/Shadow 实现。

    约束（§5.7）：单例构造期禁止绑定租户级模式或凭证。
    """
    global _resolver_instance
    if _resolver_instance is None:
        # 延迟导入避免循环依赖
        from agent.langgraph.gateways.llm_bundle_adapter import LLMBundleAdapter
        from agent.langgraph.gateways.local_retriever import LocalRetrieverGateway
        from agent.langgraph.gateways.local_verifier import LocalVerifierGateway

        _resolver_instance = GatewayResolver(
            local_retriever=LocalRetrieverGateway(),
            local_model=LLMBundleAdapter(),
            local_verifier=LocalVerifierGateway(),
        )
    return _resolver_instance


def reset_gateway_resolver(resolver: GatewayResolver | None = None) -> None:
    """重置进程级单例（测试用）。

    Args:
        resolver: 指定新的 resolver；None 表示清除单例（下次 get_gateway_resolver 重建）。
    """
    global _resolver_instance
    _resolver_instance = resolver
