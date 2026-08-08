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
"""LLMBundleAdapter 单元测试（微服务拆分 §5.2，PR-0.3a）。

覆盖：
- async_chat 正常路径：返回 ChatResult，content 来自 LLMBundle.async_chat；
- async_chat **ERROR** 语义：LLMBundle 返回 **ERROR** 前缀 → ChatResult.error_code 非空；
- async_chat 异常路径：LLMBundle 抛异常 → ChatResult.error_code 非空（不抛异常）；
- 配置解析失败：抛 ModelConfigResolveError（唯一允许抛异常的场景）；
- embed / rerank 委托 LLMBundle.encode / similarity；
- GatewayResolver 注入 LLMBundleAdapter（factory 集成）。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.langgraph.gateways.errors import ModelConfigResolveError
from agent.langgraph.gateways.factory import (
    GatewayResolver,
    get_gateway_resolver,
    reset_gateway_resolver,
)
from agent.langgraph.gateways.llm_bundle_adapter import LLMBundleAdapter
from agent.langgraph.gateways.model import ChatResult


class TestLLMBundleAdapterAsyncChat:
    """async_chat 方法测试。"""

    @pytest.mark.asyncio
    async def test_normal_chat_returns_chat_result(self):
        """正常调用返回 ChatResult，content 来自 LLMBundle.async_chat。"""
        fake_bundle = MagicMock()
        fake_bundle.async_chat = AsyncMock(return_value="LLM 生成内容")
        fake_bundle.model_config = {"llm_name": "qwen-plus"}

        with patch.object(
            LLMBundleAdapter, "_resolve_chat_model", return_value=fake_bundle
        ):
            adapter = LLMBundleAdapter()
            result = await adapter.async_chat(
                tenant_id="tenant-1",
                llm_id="qwen-plus",
                system="系统提示",
                history=[{"role": "user", "content": "你好"}],
                gen_conf={"temperature": 0.1},
            )

        assert isinstance(result, ChatResult)
        assert result.content == "LLM 生成内容"
        assert result.error_code is None
        assert result.finish_reason == "stop"
        assert result.model == "qwen-plus"
        assert result.total_tokens == 0  # Stage A: LLMBundle 不暴露 token 计数

    @pytest.mark.asyncio
    async def test_error_prefix_returns_error_code(self):
        """LLMBundle 返回 **ERROR** 前缀 → ChatResult.error_code 非空（不抛异常）。"""
        fake_bundle = MagicMock()
        fake_bundle.async_chat = AsyncMock(
            return_value="**ERROR**:RuntimeError: 模型超时"
        )
        fake_bundle.model_config = {"llm_name": "qwen-plus"}

        with patch.object(
            LLMBundleAdapter, "_resolve_chat_model", return_value=fake_bundle
        ):
            adapter = LLMBundleAdapter()
            result = await adapter.async_chat(
                tenant_id="tenant-1",
                llm_id="qwen-plus",
                system="",
                history=[],
                gen_conf={},
            )

        assert result.content.startswith("**ERROR**")
        assert result.error_code == "LLM_ERROR"
        assert result.finish_reason == "error"
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_exception_returns_error_not_raise(self):
        """LLMBundle 抛异常 → ChatResult.error_code 非空（不抛异常，§5.2 错误语义对齐）。"""
        fake_bundle = MagicMock()
        fake_bundle.async_chat = AsyncMock(
            side_effect=RuntimeError("连接超时")
        )
        fake_bundle.model_config = {"llm_name": "qwen-plus"}

        with patch.object(
            LLMBundleAdapter, "_resolve_chat_model", return_value=fake_bundle
        ):
            adapter = LLMBundleAdapter()
            result = await adapter.async_chat(
                tenant_id="tenant-1",
                llm_id="qwen-plus",
                system="",
                history=[],
                gen_conf={},
            )

        assert result.content.startswith("**ERROR**")
        assert result.error_code == "LLM_ERROR"
        assert result.finish_reason == "error"
        assert "RuntimeError" in result.content

    @pytest.mark.asyncio
    async def test_config_resolve_failure_raises(self):
        """配置解析失败 → 抛 ModelConfigResolveError（唯一允许抛异常的场景）。"""
        with patch.object(
            LLMBundleAdapter,
            "_resolve_chat_model",
            side_effect=ModelConfigResolveError("配置不存在"),
        ):
            adapter = LLMBundleAdapter()
            with pytest.raises(ModelConfigResolveError, match="配置不存在"):
                await adapter.async_chat(
                    tenant_id="tenant-1",
                    llm_id="unknown-model",
                    system="",
                    history=[],
                    gen_conf={},
                )

    @pytest.mark.asyncio
    async def test_gen_conf_passed_through(self):
        """gen_conf 原样透传给 LLMBundle.async_chat（§5.2 参数语义映射）。"""
        fake_bundle = MagicMock()
        fake_bundle.async_chat = AsyncMock(return_value="OK")
        fake_bundle.model_config = {"llm_name": "qwen-plus"}

        with patch.object(
            LLMBundleAdapter, "_resolve_chat_model", return_value=fake_bundle
        ):
            adapter = LLMBundleAdapter()
            await adapter.async_chat(
                tenant_id="tenant-1",
                llm_id="qwen-plus",
                system="sys",
                history=[{"role": "user", "content": "hi"}],
                gen_conf={"temperature": 0.5, "max_tokens": 1000},
            )

        fake_bundle.async_chat.assert_called_once_with(
            system="sys",
            history=[{"role": "user", "content": "hi"}],
            gen_conf={"temperature": 0.5, "max_tokens": 1000},
        )

    @pytest.mark.asyncio
    async def test_empty_content_handled(self):
        """LLMBundle 返回空字符串 → 正常 ChatResult（content 为空串）。"""
        fake_bundle = MagicMock()
        fake_bundle.async_chat = AsyncMock(return_value="")
        fake_bundle.model_config = {"llm_name": "qwen-plus"}

        with patch.object(
            LLMBundleAdapter, "_resolve_chat_model", return_value=fake_bundle
        ):
            adapter = LLMBundleAdapter()
            result = await adapter.async_chat(
                tenant_id="tenant-1",
                llm_id="qwen-plus",
                system="",
                history=[],
                gen_conf={},
            )

        assert result.content == ""
        assert result.error_code is None
        assert result.is_error is False


class TestLLMBundleAdapterEmbed:
    """embed 方法测试。"""

    @pytest.mark.asyncio
    async def test_embed_delegates_to_encode(self):
        """embed 委托 LLMBundle.encode。"""
        fake_bundle = MagicMock()
        fake_bundle.encode = MagicMock(
            return_value=([[0.1, 0.2], [0.3, 0.4]], 42)
        )

        with patch.object(
            LLMBundleAdapter,
            "_resolve_model_by_type",
            return_value=fake_bundle,
        ):
            adapter = LLMBundleAdapter()
            vectors, tokens = await adapter.embed(
                tenant_id="tenant-1",
                llm_id="text-embedding-ada-002",
                texts=["hello", "world"],
            )

        assert vectors == [[0.1, 0.2], [0.3, 0.4]]
        assert tokens == 42
        fake_bundle.encode.assert_called_once_with(["hello", "world"])

    @pytest.mark.asyncio
    async def test_embed_config_failure_raises(self):
        """embed 配置解析失败 → 抛 ModelConfigResolveError。"""
        with patch.object(
            LLMBundleAdapter,
            "_resolve_model_by_type",
            side_effect=ModelConfigResolveError("Embedding 模型不存在"),
        ):
            adapter = LLMBundleAdapter()
            with pytest.raises(ModelConfigResolveError):
                await adapter.embed(
                    tenant_id="tenant-1",
                    llm_id="unknown",
                    texts=["test"],
                )


class TestLLMBundleAdapterRerank:
    """rerank 方法测试。"""

    @pytest.mark.asyncio
    async def test_rerank_delegates_to_similarity(self):
        """rerank 委托 LLMBundle.similarity。"""
        fake_bundle = MagicMock()
        fake_bundle.similarity = MagicMock(
            return_value=([0.9, 0.3], 10)
        )

        with patch.object(
            LLMBundleAdapter,
            "_resolve_model_by_type",
            return_value=fake_bundle,
        ):
            adapter = LLMBundleAdapter()
            scores, tokens = await adapter.rerank(
                tenant_id="tenant-1",
                rerank_id="bge-reranker",
                query="查询",
                texts=["doc1", "doc2"],
            )

        assert scores == [0.9, 0.3]
        assert tokens == 10
        fake_bundle.similarity.assert_called_once_with("查询", ["doc1", "doc2"])


class TestLLMBundleAdapterProtocol:
    """ModelGateway 协议满足性测试。"""

    def test_satisfies_model_gateway_protocol(self):
        """LLMBundleAdapter 满足 ModelGateway 协议（runtime_checkable）。"""
        from agent.langgraph.gateways.model import ModelGateway

        adapter = LLMBundleAdapter()
        assert isinstance(adapter, ModelGateway)


class TestGatewayResolverLLMBundleAdapterInjection:
    """GatewayResolver 注入 LLMBundleAdapter 测试（factory 集成）。"""

    def test_singleton_resolver_has_local_model(self):
        """进程级单例 resolver 注入了 LLMBundleAdapter（local 模式可用）。"""
        reset_gateway_resolver()  # 清除可能的单例
        resolver = get_gateway_resolver()
        assert resolver._local_model is not None
        assert isinstance(resolver._local_model, LLMBundleAdapter)

    @pytest.mark.asyncio
    async def test_model_for_local_returns_llm_bundle_adapter(self):
        """model_for(local) 返回 LLMBundleAdapter 实例。"""
        resolver = GatewayResolver(local_model=LLMBundleAdapter())
        gateway = await resolver.model_for("tenant-1")
        assert isinstance(gateway, LLMBundleAdapter)

    def test_reset_clears_singleton(self):
        """reset_gateway_resolver 清除单例后重建包含 LLMBundleAdapter。"""
        reset_gateway_resolver()
        resolver1 = get_gateway_resolver()
        assert isinstance(resolver1._local_model, LLMBundleAdapter)

        reset_gateway_resolver(resolver=None)
        resolver2 = get_gateway_resolver()
        assert isinstance(resolver2._local_model, LLMBundleAdapter)
        assert resolver1 is not resolver2

    @pytest.mark.asyncio
    async def test_custom_resolver_without_model_raises_not_implemented(self):
        """未注入 local_model 时 model_for 抛 NotImplementedError。"""
        resolver = GatewayResolver(local_model=None)
        with pytest.raises(NotImplementedError, match="LLMBundleAdapter 未注入"):
            await resolver.model_for("tenant-1")


class TestErrorSemanticsAlignment:
    """§5.2 错误语义对齐测试（D4 语义冻结）。"""

    @pytest.mark.asyncio
    async def test_error_result_does_not_raise(self):
        """**ERROR** 类失败不抛异常（调用点已有 startswith 判断，禁止改动）。"""
        fake_bundle = MagicMock()
        fake_bundle.async_chat = AsyncMock(return_value="**ERROR**:some error")
        fake_bundle.model_config = {}

        with patch.object(
            LLMBundleAdapter, "_resolve_chat_model", return_value=fake_bundle
        ):
            adapter = LLMBundleAdapter()
            # 不应抛异常
            result = await adapter.async_chat(
                tenant_id="t", llm_id="m", system="", history=[], gen_conf={}
            )
            assert result.content.startswith("**ERROR**")

    @pytest.mark.asyncio
    async def test_only_config_error_raises(self):
        """仅 ModelConfigResolveError 允许抛异常（§5.2 错误语义对齐）。"""
        with patch.object(
            LLMBundleAdapter,
            "_resolve_chat_model",
            side_effect=ModelConfigResolveError("config not found"),
        ):
            adapter = LLMBundleAdapter()
            with pytest.raises(ModelConfigResolveError):
                await adapter.async_chat(
                    tenant_id="t", llm_id="m", system="", history=[], gen_conf={}
                )
