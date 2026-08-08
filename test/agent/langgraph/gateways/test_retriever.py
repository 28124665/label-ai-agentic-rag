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
"""RetrieverGateway 协议测试（微服务拆分 §5.1）。"""

from agent.langgraph.gateways.retriever import RetrieverGateway


class TestRetrieverGatewayProtocol:
    """RetrieverGateway Protocol 测试。"""

    def test_retriever_gateway_is_protocol(self):
        """RetrieverGateway 应是 Protocol。"""
        assert hasattr(RetrieverGateway, "_is_protocol")
        assert RetrieverGateway._is_protocol is True

    def test_retriever_gateway_has_retrieve(self):
        """RetrieverGateway 应定义 retrieve 方法。"""
        assert hasattr(RetrieverGateway, "retrieve")

    def test_retriever_gateway_implementation_satisfies_protocol(self):
        """实现 RetrieverGateway 接口的类应被 isinstance 识别为 RetrieverGateway。"""

        class FakeRetrieverGateway:
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
                return {"chunks": [], "doc_aggs": {}}

        instance = FakeRetrieverGateway()
        assert isinstance(instance, RetrieverGateway)

    def test_retriever_gateway_does_not_match_missing_method(self):
        """缺少 retrieve 方法的类不应被识别为 RetrieverGateway。"""

        class IncompleteGateway:
            async def search(self, query: str):
                return []

        instance = IncompleteGateway()
        assert not isinstance(instance, RetrieverGateway)
