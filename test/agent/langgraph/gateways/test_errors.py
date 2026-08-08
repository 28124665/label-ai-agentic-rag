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
"""网关层错误分类测试（微服务拆分 §5.1）。

验证错误继承关系，确保 RAGTool.invoke 的 isinstance 分类捕获（§5.8）正确工作。
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


class TestGatewayErrorHierarchy:
    """错误分类继承关系测试。"""

    def test_gateway_error_is_exception(self):
        """GatewayError 应继承 Exception。"""
        assert issubclass(GatewayError, Exception)

    def test_retrieval_service_error_is_gateway_error(self):
        """RetrievalServiceError 应继承 GatewayError。"""
        assert issubclass(RetrievalServiceError, GatewayError)

    def test_retrieval_timeout_is_service_error(self):
        """RetrievalTimeoutError 应继承 RetrievalServiceError（§5.8 分类捕获依据）。"""
        assert issubclass(RetrievalTimeoutError, RetrievalServiceError)

    def test_retrieval_circuit_open_is_service_error(self):
        """RetrievalCircuitOpenError 应继承 RetrievalServiceError。"""
        assert issubclass(RetrievalCircuitOpenError, RetrievalServiceError)

    def test_retrieval_auth_is_gateway_error(self):
        """RetrievalAuthError 应继承 GatewayError（非 RetrievalServiceError）。"""
        assert issubclass(RetrievalAuthError, GatewayError)
        assert not issubclass(RetrievalAuthError, RetrievalServiceError)

    def test_retrieval_data_is_gateway_error(self):
        """RetrievalDataError 应继承 GatewayError（非 RetrievalServiceError）。"""
        assert issubclass(RetrievalDataError, GatewayError)
        assert not issubclass(RetrievalDataError, RetrievalServiceError)

    def test_model_config_resolve_is_gateway_error(self):
        """ModelConfigResolveError 应继承 GatewayError。"""
        assert issubclass(ModelConfigResolveError, GatewayError)

    def test_model_provider_is_gateway_error(self):
        """ModelProviderError 应继承 GatewayError。"""
        assert issubclass(ModelProviderError, GatewayError)


class TestGatewayErrorInstanceof:
    """错误实例 isinstance 识别测试（§5.8 RAGTool.invoke 分类捕获依据）。"""

    def test_timeout_caught_as_service_error(self):
        """RetrievalTimeoutError 应被 isinstance(x, RetrievalServiceError) 捕获。

        场景：RAGTool.invoke 中 `except RetrievalServiceError` 需能捕获超时。
        """
        err = RetrievalTimeoutError("read timeout 15s")
        assert isinstance(err, RetrievalServiceError)

    def test_circuit_open_caught_as_service_error(self):
        """RetrievalCircuitOpenError 应被 isinstance(x, RetrievalServiceError) 捕获。"""
        err = RetrievalCircuitOpenError("circuit open 30s")
        assert isinstance(err, RetrievalServiceError)

    def test_auth_not_caught_as_service_error(self):
        """RetrievalAuthError 不应被 isinstance(x, RetrievalServiceError) 捕获。

        场景：认证错误需单独捕获，走 error 日志告警路径（§5.8）。
        """
        err = RetrievalAuthError("401 Unauthorized")
        assert not isinstance(err, RetrievalServiceError)
        assert isinstance(err, GatewayError)

    def test_data_not_caught_as_service_error(self):
        """RetrievalDataError 不应被 isinstance(x, RetrievalServiceError) 捕获。

        场景：数据错误需透传抛出（§5.8），不转为空结果。
        """
        err = RetrievalDataError("dataset_ids is required")
        assert not isinstance(err, RetrievalServiceError)
        assert isinstance(err, GatewayError)

    def test_all_errors_caught_as_gateway_error(self):
        """所有网关错误应被 isinstance(x, GatewayError) 捕获。"""
        errors = [
            RetrievalServiceError("5xx"),
            RetrievalTimeoutError("timeout"),
            RetrievalCircuitOpenError("circuit"),
            RetrievalAuthError("401"),
            RetrievalDataError("400"),
            ModelConfigResolveError("not found"),
            ModelProviderError("provider down"),
        ]
        for err in errors:
            assert isinstance(err, GatewayError), f"{type(err)} not GatewayError"


class TestGatewayErrorMessages:
    """错误消息安全性测试（附录 D #4）。"""

    def test_error_message_no_sensitive_data(self):
        """错误消息不应包含 api_key / token 等敏感信息。"""
        # 模拟 HttpRetrieverGateway 抛出的错误
        err = RetrievalAuthError("HTTP 401: API Key invalid or expired")
        assert "sk-" not in str(err)
        assert "Bearer " not in str(err)

    def test_error_message_preserves_context(self):
        """错误消息应保留足够的上下文用于排查。"""
        msg = "HTTP 503: ragflow-service unavailable"
        err = RetrievalServiceError(msg)
        assert msg in str(err)
