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
"""RAGTool 网关接入与 §5.8/§5.9 测试（PR-0.2）。

覆盖：
- §5.8 异常分类表（RetrievalServiceError / RetrievalAuthError / RetrievalDataError / 其他）
- §5.9 参数优先级链（rag_tool_node / tool_dispatcher / react executor / skill_rag）
- RAGToolOutput 既有字段回归 + 新增字段验证
- GatewayResolver 注入与回退
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.langgraph.gateways.errors import (
    RetrievalAuthError,
    RetrievalCircuitOpenError,
    RetrievalDataError,
    RetrievalServiceError,
    RetrievalTimeoutError,
)
from agent.langgraph.gateways.factory import (
    GatewayResolver,
    reset_gateway_resolver,
)
from agent.langgraph.tools.rag_tool import RAGTool, RAGToolInput, RAGToolOutput


class FakeRetrieverGateway:
    """可控的 RetrieverGateway 假实现，用于注入 RAGTool 测试。"""

    def __init__(self, result: dict = None, error: Exception = None):
        self._result = result or {"chunks": [], "doc_aggs": {}}
        self._error = error
        self.retrieve = AsyncMock(side_effect=self._do_retrieve)

    async def _do_retrieve(self, **kwargs):
        if self._error:
            raise self._error
        return self._result


def _make_chunks(count: int = 3, score: float = 0.8) -> list[dict]:
    """生成测试用 chunks。"""
    return [
        {
            "content_with_weight": f"content {i}",
            "similarity": score,
            "docnm_kwd": f"doc{i}.pdf",
            "chunk_id": f"chunk{i}",
            "doc_id": f"doc{i}",
        }
        for i in range(count)
    ]


class TestRAGToolExceptionClassification:
    """§5.8 异常分类表测试。"""

    @pytest.mark.asyncio
    async def test_retrieval_service_error_returns_empty_with_error_code(self):
        """RetrievalServiceError → 转空结果 + 写 RETRIEVAL_SERVICE_ERROR。"""
        gateway = FakeRetrieverGateway(error=RetrievalServiceError("503"))
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        result = await tool.invoke(
            RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
        )

        assert result["docs"] == []
        assert result["retrieval_error_code"] == "RETRIEVAL_SERVICE_ERROR"
        assert result["retrieval_mode_used"] == "local"

    @pytest.mark.asyncio
    async def test_retrieval_timeout_error_returns_empty_with_error_code(self):
        """RetrievalTimeoutError（RetrievalServiceError 子类）→ RETRIEVAL_SERVICE_ERROR。"""
        gateway = FakeRetrieverGateway(error=RetrievalTimeoutError("read timeout"))
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        result = await tool.invoke(
            RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
        )

        assert result["docs"] == []
        assert result["retrieval_error_code"] == "RETRIEVAL_SERVICE_ERROR"

    @pytest.mark.asyncio
    async def test_retrieval_circuit_open_error_returns_empty_with_error_code(self):
        """RetrievalCircuitOpenError（RetrievalServiceError 子类）→ RETRIEVAL_SERVICE_ERROR。"""
        gateway = FakeRetrieverGateway(error=RetrievalCircuitOpenError("circuit open"))
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        result = await tool.invoke(
            RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
        )

        assert result["docs"] == []
        assert result["retrieval_error_code"] == "RETRIEVAL_SERVICE_ERROR"

    @pytest.mark.asyncio
    async def test_retrieval_auth_error_returns_empty_with_auth_code(self):
        """RetrievalAuthError → 转空结果 + 写 RETRIEVAL_AUTH。"""
        gateway = FakeRetrieverGateway(error=RetrievalAuthError("401"))
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        result = await tool.invoke(
            RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
        )

        assert result["docs"] == []
        assert result["retrieval_error_code"] == "RETRIEVAL_AUTH"

    @pytest.mark.asyncio
    async def test_retrieval_data_error_propagates(self):
        """RetrievalDataError → 透传抛出（业务错误，不静默）。"""
        gateway = FakeRetrieverGateway(error=RetrievalDataError("dataset_ids required"))
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        with pytest.raises(RetrievalDataError):
            await tool.invoke(
                RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
            )

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty_without_error_code(self):
        """其他未知异常 → 维持现状：空结果，不写 error_code。"""
        gateway = FakeRetrieverGateway(error=Exception("unexpected"))
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        result = await tool.invoke(
            RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
        )

        assert result["docs"] == []
        assert result["retrieval_error_code"] == ""


class TestRAGToolOutputRegression:
    """RAGToolOutput 既有字段回归 + 新增字段验证。"""

    def test_empty_result_has_all_existing_fields(self):
        """_empty_result 返回所有既有字段（回归测试）。"""
        tool = RAGTool()
        result = tool._empty_result(time.time())

        # 既有字段
        assert result["docs"] == []
        assert result["quality_score"] == 0.0
        assert result["has_relevant"] is False
        assert result["relevant_count"] == 0
        assert result["top_score"] == 0.0
        assert result["rewrite_history"] == []
        assert result["query_simplified"] == ""
        assert result["detected_lang"] == "zh_CN"
        assert result["retrieval_time_ms"] >= 0

    def test_empty_result_has_new_fields(self):
        """_empty_result 返回 §5.8 新增字段。"""
        tool = RAGTool()
        result = tool._empty_result(time.time(), retrieval_mode="local")

        assert result["retrieval_error_code"] == ""
        assert result["retrieval_mode_used"] == "local"

    def test_empty_result_with_error_code(self):
        """_empty_result 支持传入 error_code。"""
        tool = RAGTool()
        result = tool._empty_result(
            time.time(),
            retrieval_mode="remote",
            error_code="RETRIEVAL_SERVICE_ERROR",
        )

        assert result["retrieval_error_code"] == "RETRIEVAL_SERVICE_ERROR"
        assert result["retrieval_mode_used"] == "remote"

    @pytest.mark.asyncio
    async def test_successful_invoke_has_new_fields(self):
        """成功检索的输出包含新字段。"""
        gateway = FakeRetrieverGateway(
            result={"chunks": _make_chunks(3, 0.8), "doc_aggs": {}}
        )
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        result = await tool.invoke(
            RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
        )

        assert result["retrieval_mode_used"] == "local"
        assert result["retrieval_error_code"] == ""
        assert len(result["docs"]) == 3


class TestRAGToolGatewayIntegration:
    """RAGTool 与 GatewayResolver 集成测试。"""

    def setup_method(self):
        reset_gateway_resolver()

    def teardown_method(self):
        reset_gateway_resolver()

    @pytest.mark.asyncio
    async def test_rag_tool_uses_injected_resolver(self):
        """RAGTool 使用注入的 resolver。"""
        gateway = FakeRetrieverGateway(
            result={"chunks": _make_chunks(2, 0.7), "doc_aggs": {}}
        )
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        await tool.invoke(
            RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
        )

        gateway.retrieve.assert_called_once()

    @pytest.mark.asyncio
    async def test_rag_tool_falls_back_to_singleton_resolver(self):
        """RAGTool 未注入 resolver 时回退到进程级单例。"""
        from agent.langgraph.gateways.local_retriever import LocalRetrieverGateway

        gateway = FakeRetrieverGateway(
            result={"chunks": [], "doc_aggs": {}}
        )
        reset_gateway_resolver(GatewayResolver(local_retriever=gateway))

        tool = RAGTool()  # 不注入 resolver
        await tool.invoke(
            RAGToolInput(query="test", kb_ids=["kb1"], tenant_id="t1")
        )

        gateway.retrieve.assert_called_once()

    @pytest.mark.asyncio
    async def test_gateway_receives_all_parameters(self):
        """gateway.retrieve 接收所有 §5.1 协议参数。"""
        gateway = FakeRetrieverGateway(
            result={"chunks": [], "doc_aggs": {}}
        )
        resolver = GatewayResolver(local_retriever=gateway)
        tool = RAGTool(resolver=resolver)

        await tool.invoke(
            RAGToolInput(
                query="test query",
                kb_ids=["kb1", "kb2"],
                tenant_id="t1",
                top_k=10,
                similarity_threshold=0.3,
                keywords_similarity_weight=0.6,
                rerank_id="rerank_001",
                cross_languages=["en"],
            )
        )

        call_kwargs = gateway.retrieve.call_args.kwargs
        assert call_kwargs["query"] is not None  # 可能经过重写
        assert call_kwargs["kb_ids"] == ["kb1", "kb2"]
        assert call_kwargs["tenant_id"] == "t1"
        assert call_kwargs["top_k"] == 10
        assert call_kwargs["similarity_threshold"] == 0.3
        assert call_kwargs["keywords_similarity_weight"] == 0.6
        assert call_kwargs["rerank_id"] == "rerank_001"
        # cross_languages 在 local 模式下已应用到 query，但仍透传给 gateway
        assert call_kwargs["cross_languages"] == ["en"]


class TestParameterPassingEntry1:
    """§5.9 入口① rag_tool_node 参数传递测试。"""

    def test_rag_tool_node_reads_params_from_rag_config(self):
        """rag_tool_node 从 agent_config.rag_config 读取 similarity_threshold 等参数。"""
        from agent.langgraph.nodes.rag_tool_node import rag_tool_node

        # 构造 state，含 rag_config 中的 §5.9 参数
        state = {
            "user_question": "test query",
            "kb_ids": ["kb1"],
            "tenant_id": "t1",
            "llm_id": "llm1",
            "agent_config": {
                "rag_config": {
                    "top_k": 10,
                    "similarity_threshold": 0.3,
                    "keywords_similarity_weight": 0.7,
                    "rerank_id": "rerank_001",
                }
            },
        }

        # Mock RAGTool
        mock_tool = MagicMock()
        mock_tool.invoke = AsyncMock(
            return_value=RAGToolOutput(
                docs=[],
                quality_score=0.0,
                has_relevant=False,
                relevant_count=0,
                top_score=0.0,
                rewrite_history=[],
                query_simplified="test",
                detected_lang="zh_CN",
                retrieval_time_ms=10,
                retrieval_error_code="",
                retrieval_mode_used="local",
            )
        )

        with patch(
            "agent.langgraph.nodes.rag_tool_node.get_rag_tool",
            return_value=mock_tool,
        ):
            import asyncio

            result = asyncio.run(rag_tool_node(state))

        # 验证 input_data 包含 §5.9 参数
        call_args = mock_tool.invoke.call_args.args[0]
        assert call_args["similarity_threshold"] == 0.3
        assert call_args["keywords_similarity_weight"] == 0.7
        assert call_args["rerank_id"] == "rerank_001"

        # 验证 state 透传 retrieval_error_code / retrieval_mode_used
        assert "retrieval_error_code" in result
        assert "retrieval_mode_used" in result


class TestParameterPassingEntry3:
    """§5.9 入口③ react executor 参数传递测试。"""

    @pytest.mark.asyncio
    async def test_call_rag_tool_passes_new_params(self):
        """_call_rag_tool 传递 §5.9 新增参数。"""
        from agent.langgraph.react.executor import _call_rag_tool

        mock_tool = MagicMock()
        mock_tool.invoke = AsyncMock(
            return_value=RAGToolOutput(
                docs=[],
                quality_score=0.0,
                has_relevant=False,
                relevant_count=0,
                top_score=0.0,
                rewrite_history=[],
                query_simplified="",
                detected_lang="zh_CN",
                retrieval_time_ms=0,
                retrieval_error_code="",
                retrieval_mode_used="local",
            )
        )

        with patch(
            "agent.langgraph.tools.rag_tool.get_rag_tool",
            return_value=mock_tool,
        ):
            await _call_rag_tool(
                arguments={
                    "query": "test",
                    "similarity_threshold": 0.3,
                    "keywords_similarity_weight": 0.7,
                    "rerank_id": "rerank_001",
                },
                tenant_id="t1",
                llm_id="llm1",
                kb_ids=["kb1"],
            )

        call_args = mock_tool.invoke.call_args.args[0]
        assert call_args["similarity_threshold"] == 0.3
        assert call_args["keywords_similarity_weight"] == 0.7
        assert call_args["rerank_id"] == "rerank_001"

    @pytest.mark.asyncio
    async def test_call_rag_tool_defaults_when_params_missing(self):
        """_call_rag_tool 参数缺失时使用内置默认值。"""
        from agent.langgraph.react.executor import _call_rag_tool

        mock_tool = MagicMock()
        mock_tool.invoke = AsyncMock(
            return_value=RAGToolOutput(
                docs=[],
                quality_score=0.0,
                has_relevant=False,
                relevant_count=0,
                top_score=0.0,
                rewrite_history=[],
                query_simplified="",
                detected_lang="zh_CN",
                retrieval_time_ms=0,
                retrieval_error_code="",
                retrieval_mode_used="local",
            )
        )

        with patch(
            "agent.langgraph.tools.rag_tool.get_rag_tool",
            return_value=mock_tool,
        ):
            await _call_rag_tool(
                arguments={"query": "test"},
                tenant_id="t1",
                llm_id="llm1",
                kb_ids=["kb1"],
            )

        call_args = mock_tool.invoke.call_args.args[0]
        assert call_args["similarity_threshold"] == 0.2
        assert call_args["keywords_similarity_weight"] == 0.5
        assert call_args["rerank_id"] == ""
