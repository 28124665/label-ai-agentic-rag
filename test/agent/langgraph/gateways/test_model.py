#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""ChatResult 与 ModelGateway 协议测试（微服务拆分 §5.2）。"""

import pytest

from agent.langgraph.gateways.model import ChatResult, ModelGateway


class TestChatResult:
    """ChatResult dataclass 测试。"""

    def test_create_success_result(self):
        """创建成功的 ChatResult。"""
        result = ChatResult(
            content="你好",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            finish_reason="stop",
            model="qwen-plus",
            provider_request_id="req-xxx",
            error_code=None,
        )
        assert result.content == "你好"
        assert result.total_tokens == 15
        assert result.error_code is None
        assert result.is_error is False

    def test_create_error_result(self):
        """创建 **ERROR** 类失败的 ChatResult。"""
        result = ChatResult(
            content="**ERROR** provider timeout",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            finish_reason="error",
            model="qwen-plus",
            provider_request_id="",
            error_code="PROVIDER_TIMEOUT",
        )
        assert result.content.startswith("**ERROR**")
        assert result.error_code == "PROVIDER_TIMEOUT"
        assert result.is_error is True

    def test_is_error_with_error_code_only(self):
        """error_code 非空时 is_error 应为 True（即使 content 不以 **ERROR** 开头）。"""
        result = ChatResult(
            content="部分内容",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            finish_reason="content_filter",
            model="qwen-plus",
            provider_request_id="req-xxx",
            error_code="CONTENT_FILTERED",
        )
        assert result.is_error is True

    def test_is_error_with_error_prefix_only(self):
        """content 以 **ERROR** 开头时 is_error 应为 True（即使 error_code 为 None）。"""
        result = ChatResult(
            content="**ERROR** some failure",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            finish_reason="error",
            model="qwen-plus",
            provider_request_id="",
            error_code=None,
        )
        assert result.is_error is True

    def test_frozen_dataclass(self):
        """ChatResult 应为 frozen dataclass（不可变）。"""
        result = ChatResult(
            content="test",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            finish_reason="stop",
            model="test",
            provider_request_id="",
            error_code=None,
        )
        with pytest.raises(AttributeError):
            result.content = "modified"

    def test_finish_reason_values(self):
        """finish_reason 应支持 §5.2 定义的所有值。"""
        for reason in ("stop", "length", "tool_calls", "error", "content_filter"):
            result = ChatResult(
                content="",
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                finish_reason=reason,
                model="test",
                provider_request_id="",
                error_code=None,
            )
            assert result.finish_reason == reason


class TestModelGatewayProtocol:
    """ModelGateway Protocol 测试。"""

    def test_model_gateway_is_protocol(self):
        """ModelGateway 应是 Protocol（runtime_checkable）。"""
        # Protocol 类有 __protocol_attrs__ 或 _is_protocol 属性
        assert hasattr(ModelGateway, "_is_protocol")
        assert ModelGateway._is_protocol is True

    def test_model_gateway_has_async_chat(self):
        """ModelGateway 应定义 async_chat 方法。"""
        assert hasattr(ModelGateway, "async_chat")

    def test_model_gateway_has_embed(self):
        """ModelGateway 应定义 embed 方法。"""
        assert hasattr(ModelGateway, "embed")

    def test_model_gateway_has_rerank(self):
        """ModelGateway 应定义 rerank 方法。"""
        assert hasattr(ModelGateway, "rerank")

    def test_model_gateway_implementation_satisfies_protocol(self):
        """实现 ModelGateway 接口的类应被 isinstance 识别为 ModelGateway。"""

        class FakeModelGateway:
            async def async_chat(
                self, *, tenant_id, llm_id, system, history, gen_conf
            ):
                return ChatResult(
                    content="ok",
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    finish_reason="stop",
                    model="test",
                    provider_request_id="",
                    error_code=None,
                )

            async def embed(self, *, tenant_id, llm_id, texts):
                return ([], 0)

            async def rerank(self, *, tenant_id, rerank_id, query, texts):
                return ([], 0)

        instance = FakeModelGateway()
        assert isinstance(instance, ModelGateway)
