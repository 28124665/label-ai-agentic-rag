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
"""GatewayResolver 测试（微服务拆分 §5.7）。

PR-0.1 范围：仅 local 路由骨架测试。
"""

import pytest

from agent.langgraph.gateways.factory import (
    GatewayResolver,
    get_gateway_resolver,
    reset_gateway_resolver,
)


class FakeRetrieverGateway:
    """用于测试的 RetrieverGateway 假实现。"""

    def __init__(self, name: str = "fake"):
        self.name = name

    async def retrieve(
        self,
        *,
        query,
        kb_ids,
        tenant_id,
        top_k,
        similarity_threshold,
        keywords_similarity_weight,
        rerank_id,
        cross_languages,
    ):
        return {"chunks": [], "doc_aggs": {}, "source": self.name}


class FakeModelGateway:
    """用于测试的 ModelGateway 假实现。"""

    def __init__(self, name: str = "fake"):
        self.name = name

    async def async_chat(self, *, tenant_id, llm_id, system, history, gen_conf):
        from agent.langgraph.gateways.model import ChatResult

        return ChatResult(
            content=self.name,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            finish_reason="stop",
            model=self.name,
            provider_request_id="",
            error_code=None,
        )

    async def embed(self, *, tenant_id, llm_id, texts):
        return ([], 0)

    async def rerank(self, *, tenant_id, rerank_id, query, texts):
        return ([], 0)


class TestGatewayResolverSkeleton:
    """GatewayResolver 骨架测试（PR-0.1：仅 local 路由）。"""

    def test_create_empty_resolver(self):
        """空骨架 GatewayResolver 可创建（未注入任何网关）。"""
        resolver = GatewayResolver()
        assert resolver is not None

    @pytest.mark.asyncio
    async def test_retriever_for_raises_without_local(self):
        """未注入 local_retriever 时 retriever_for 应抛 NotImplementedError。"""
        resolver = GatewayResolver()
        with pytest.raises(NotImplementedError, match="LocalRetrieverGateway"):
            await resolver.retriever_for(tenant_id="t1")

    @pytest.mark.asyncio
    async def test_model_for_raises_without_local(self):
        """未注入 local_model 时 model_for 应抛 NotImplementedError。"""
        resolver = GatewayResolver()
        with pytest.raises(NotImplementedError, match="LLMBundleAdapter"):
            await resolver.model_for(tenant_id="t1")

    @pytest.mark.asyncio
    async def test_retriever_for_returns_local_when_injected(self):
        """注入 local_retriever 后 retriever_for 应返回该实例。"""
        local = FakeRetrieverGateway(name="local")
        resolver = GatewayResolver(local_retriever=local)

        result = await resolver.retriever_for(tenant_id="t1")
        assert result is local

    @pytest.mark.asyncio
    async def test_model_for_returns_local_when_injected(self):
        """注入 local_model 后 model_for 应返回该实例。"""
        local = FakeModelGateway(name="local")
        resolver = GatewayResolver(local_model=local)

        result = await resolver.model_for(tenant_id="t1")
        assert result is local


class TestGatewayResolverModeResolution:
    """模式解析测试（PR-0.1：仅环境变量，PR-1.5 接入租户级覆盖）。"""

    @pytest.mark.asyncio
    async def test_default_retrieval_mode_is_local(self, monkeypatch):
        """无环境变量时默认 retrieval_mode=local。"""
        monkeypatch.delenv("RETRIEVAL_MODE", raising=False)
        resolver = GatewayResolver()
        mode = await resolver._resolve_retrieval_mode(tenant_id="t1")
        assert mode == "local"

    @pytest.mark.asyncio
    async def test_default_model_mode_is_local(self, monkeypatch):
        """无环境变量时默认 model_mode=local。"""
        monkeypatch.delenv("MODEL_MODE", raising=False)
        resolver = GatewayResolver()
        mode = await resolver._resolve_model_mode(tenant_id="t1")
        assert mode == "local"

    @pytest.mark.asyncio
    async def test_env_retrieval_mode_remote_not_implemented(self, monkeypatch):
        """RETRIEVAL_MODE=remote 时应抛 NotImplementedError（HttpRetrieverGateway 待 PR-1.3）。"""
        monkeypatch.setenv("RETRIEVAL_MODE", "remote")
        resolver = GatewayResolver()

        with pytest.raises(NotImplementedError, match="HttpRetrieverGateway"):
            await resolver.retriever_for(tenant_id="t1")

    @pytest.mark.asyncio
    async def test_env_retrieval_mode_shadow_not_implemented(self, monkeypatch):
        """RETRIEVAL_MODE=shadow 时应抛 NotImplementedError（ShadowRetrieverGateway 待 PR-1.6）。"""
        monkeypatch.setenv("RETRIEVAL_MODE", "shadow")
        resolver = GatewayResolver()

        with pytest.raises(NotImplementedError, match="ShadowRetrieverGateway"):
            await resolver.retriever_for(tenant_id="t1")

    @pytest.mark.asyncio
    async def test_env_model_mode_remote_not_implemented(self, monkeypatch):
        """MODEL_MODE=remote 时应抛 NotImplementedError（HttpConfigResolver 待 PR-1.4）。"""
        monkeypatch.setenv("MODEL_MODE", "remote")
        resolver = GatewayResolver()

        with pytest.raises(NotImplementedError, match="HttpConfigResolver"):
            await resolver.model_for(tenant_id="t1")

    @pytest.mark.asyncio
    async def test_invalid_env_mode_falls_back_to_local(self, monkeypatch):
        """无效的环境变量值应回退到 local。"""
        monkeypatch.setenv("RETRIEVAL_MODE", "invalid_mode")
        resolver = GatewayResolver()

        mode = await resolver._resolve_retrieval_mode(tenant_id="t1")
        assert mode == "local"

    @pytest.mark.asyncio
    async def test_remote_mode_returns_http_when_injected(self, monkeypatch):
        """RETRIEVAL_MODE=remote 且注入 http_retriever 时应返回 http 实例。"""
        monkeypatch.setenv("RETRIEVAL_MODE", "remote")
        http_gw = FakeRetrieverGateway(name="http")
        resolver = GatewayResolver(http_retriever=http_gw)

        result = await resolver.retriever_for(tenant_id="t1")
        assert result is http_gw

    @pytest.mark.asyncio
    async def test_shadow_mode_returns_shadow_when_injected(self, monkeypatch):
        """RETRIEVAL_MODE=shadow 且注入 shadow_retriever 时应返回 shadow 实例。"""
        monkeypatch.setenv("RETRIEVAL_MODE", "shadow")
        shadow_gw = FakeRetrieverGateway(name="shadow")
        resolver = GatewayResolver(shadow_retriever=shadow_gw)

        result = await resolver.retriever_for(tenant_id="t1")
        assert result is shadow_gw


class TestGatewayResolverSingleton:
    """进程级单例测试。"""

    def setup_method(self):
        """每个测试前清除单例。"""
        reset_gateway_resolver()

    def teardown_method(self):
        """每个测试后清除单例。"""
        reset_gateway_resolver()

    def test_get_resolver_returns_singleton(self):
        """get_gateway_resolver 应返回同一实例。"""
        r1 = get_gateway_resolver()
        r2 = get_gateway_resolver()
        assert r1 is r2

    def test_reset_resolver_clears_singleton(self):
        """reset_gateway_resolver 应清除单例。"""
        r1 = get_gateway_resolver()
        reset_gateway_resolver()
        r2 = get_gateway_resolver()
        assert r1 is not r2

    def test_reset_resolver_with_custom_instance(self):
        """reset_gateway_resolver 可注入自定义实例（测试用）。"""
        custom = GatewayResolver()
        reset_gateway_resolver(custom)
        assert get_gateway_resolver() is custom
