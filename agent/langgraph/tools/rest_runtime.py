#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""RestRuntime — RestTool 运行时，MCP 客户端封装。

参考 DbRuntime 的 session 管理、超时控制、熔断机制。
与 DbRuntime 的关键区别：
- 不执行 SQL 验证（RestTool 不需要）
- 支持 REST 方法（GET/POST/PUT/DELETE）
- 支持自定义请求头
- 支持响应格式解析（JSON/文本）
"""

import asyncio
import json
import logging
import time
from typing import Any, Optional

from common.mcp_tool_call_conn import MCPToolCallSession
from api.db.services.mcp_server_service import MCPServerService

logger = logging.getLogger(__name__)

# 默认超时与重试配置
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_MAX_RETRIES = 2
DEFAULT_MCP_SERVER_NAME = "erp_mcp_server"

# 熔断器配置
CIRCUIT_BREAKER_THRESHOLD = 5  # 连续失败次数阈值
CIRCUIT_BREAKER_TIMEOUT_S = 60  # 熔断后恢复等待时间（秒）


class RestRuntime:
    """RestTool 运行时：MCP 客户端封装。

    职责：
    1. 管理 MCP 会话生命周期
    2. 执行 REST 调用（GET/POST/PUT/DELETE）
    3. 超时控制 + 重试
    4. 熔断器保护
    5. 响应格式解析
    """

    def __init__(self):
        self._mcp_session: Optional[MCPToolCallSession] = None
        self._session_tenant_id: str = ""
        self._session_server_name: str = ""

        # 熔断器状态
        self._circuit_open: bool = False
        self._circuit_open_at: float = 0.0
        self._consecutive_failures: int = 0

    # ========== 会话管理 ==========

    def init_session(self, tenant_id: str, mcp_server_name: str = DEFAULT_MCP_SERVER_NAME) -> None:
        """初始化 MCP 会话。

        如果 tenant_id 和 mcp_server_name 与当前会话一致，则复用已有会话。

        Raises:
            Exception: MCP Server 不存在
        """
        if (
            self._mcp_session is not None
            and self._session_tenant_id == tenant_id
            and self._session_server_name == mcp_server_name
        ):
            logger.debug(f"[RestRuntime] 复用已有 MCP 会话: tenant={tenant_id}, server={mcp_server_name}")
            return

        _, mcp_server = MCPServerService.get_by_name_and_tenant(mcp_server_name, tenant_id)
        if not mcp_server:
            raise Exception(f"MCP Server not found: {mcp_server_name} (tenant={tenant_id})")

        self._mcp_session = MCPToolCallSession(mcp_server.url, tenant_id, mcp_server_name)
        self._session_tenant_id = tenant_id
        self._session_server_name = mcp_server_name
        logger.info(f"[RestRuntime] MCP 会话已初始化: tenant={tenant_id}, server={mcp_server_name}")

    def close_session(self) -> None:
        """关闭 MCP 会话。"""
        if self._mcp_session is not None:
            try:
                self._mcp_session.close()
            except Exception as e:
                logger.warning(f"[RestRuntime] 关闭 MCP 会话异常: {e}")
            finally:
                self._mcp_session = None
                self._session_tenant_id = ""
                self._session_server_name = ""

    # ========== 熔断器 ==========

    def _check_circuit_breaker(self) -> bool:
        """检查熔断器状态。

        Returns:
            bool: True 表示熔断器打开（请求应被拒绝）
        """
        if not self._circuit_open:
            return False

        # 检查是否已过恢复等待时间
        elapsed = time.time() - self._circuit_open_at
        if elapsed >= CIRCUIT_BREAKER_TIMEOUT_S:
            logger.info(f"[RestRuntime] 熔断器恢复: 已等待 {elapsed:.1f}s")
            self._circuit_open = False
            self._consecutive_failures = 0
            return False

        logger.warning(f"[RestRuntime] 熔断器打开中: 剩余 {CIRCUIT_BREAKER_TIMEOUT_S - elapsed:.1f}s")
        return True

    def _record_success(self) -> None:
        """记录一次成功调用，重置连续失败计数。"""
        self._consecutive_failures = 0
        if self._circuit_open:
            self._circuit_open = False

    def _record_failure(self) -> None:
        """记录一次失败调用，达到阈值时打开熔断器。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open = True
            self._circuit_open_at = time.time()
            logger.warning(
                f"[RestRuntime] 熔断器打开: 连续失败 {self._consecutive_failures} 次, "
                f"将在 {CIRCUIT_BREAKER_TIMEOUT_S}s 后尝试恢复"
            )

    # ========== REST 执行 ==========

    async def execute(
        self,
        endpoint: str,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict | None = None,
        tenant_id: str = "",
        mcp_server_name: str = DEFAULT_MCP_SERVER_NAME,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> dict:
        """通过 MCP 服务执行 REST 调用。

        Args:
            endpoint: REST 端点路径
            method: HTTP 方法
            payload: 请求体
            headers: 额外请求头
            tenant_id: 租户 ID
            mcp_server_name: MCP 服务名
            timeout_ms: 超时时间
            max_retries: 最大重试次数

        Returns:
            dict: {status_code, data, text, latency_ms, headers}

        Raises:
            Exception: 熔断器打开、MCP 会话失败、超时或所有重试耗尽
        """
        # 1. 熔断器检查
        if self._check_circuit_breaker():
            raise Exception("Circuit breaker is open, request rejected")

        # 2. 初始化会话
        try:
            self.init_session(tenant_id, mcp_server_name)
        except Exception as e:
            self._record_failure()
            raise Exception(f"Failed to init MCP session: {e}") from e

        if self._mcp_session is None:
            self._record_failure()
            raise Exception("MCP session is not initialized")

        # 3. 构造 MCP 工具调用参数
        tool_args = {
            "endpoint": endpoint,
            "method": method.upper(),
        }
        if payload is not None:
            tool_args["payload"] = json.dumps(payload, ensure_ascii=False)
        if headers is not None:
            tool_args["headers"] = json.dumps(headers, ensure_ascii=False)

        # 4. 带重试的执行
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                start_time = time.time()
                result = await self._call_mcp_tool(
                    tool_name="rest_call",
                    tool_args=tool_args,
                    timeout_ms=timeout_ms,
                )
                latency_ms = int((time.time() - start_time) * 1000)

                # 解析响应
                response = self._parse_response(result, endpoint, method, latency_ms)
                self._record_success()
                return response

            except asyncio.TimeoutError:
                last_error = f"Timeout after {timeout_ms}ms (attempt {attempt + 1}/{max_retries + 1})"
                logger.warning(f"[RestRuntime] {last_error}")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"[RestRuntime] MCP 调用失败 (attempt {attempt + 1}/{max_retries + 1}): {e}")

            if attempt < max_retries:
                wait_ms = min(1000 * (2 ** attempt), 10000)  # 指数退避，最大 10s
                logger.info(f"[RestRuntime] 重试等待 {wait_ms}ms")
                await asyncio.sleep(wait_ms / 1000)

        # 5. 所有重试耗尽
        self._record_failure()
        raise Exception(f"All retries exhausted: {last_error}")

    async def _call_mcp_tool(
        self,
        tool_name: str,
        tool_args: dict,
        timeout_ms: int,
    ) -> Any:
        """调用 MCP 工具（带超时）。

        Args:
            tool_name: MCP 工具名
            tool_args: 工具参数
            timeout_ms: 超时时间（毫秒）

        Returns:
            MCP 工具调用结果
        """
        if self._mcp_session is None:
            raise Exception("MCP session is not initialized")

        timeout_s = timeout_ms / 1000.0
        return await asyncio.wait_for(
            self._mcp_session.call_tool(tool_name, tool_args),
            timeout=timeout_s,
        )

    def _parse_response(
        self,
        result: Any,
        endpoint: str,
        method: str,
        latency_ms: int,
    ) -> dict:
        """解析 MCP 工具调用响应。

        Args:
            result: MCP 工具返回的原始结果
            endpoint: 调用的端点
            method: HTTP 方法
            latency_ms: 调用耗时

        Returns:
            dict: {status_code, data, text, latency_ms, headers}
        """
        response: dict = {
            "status_code": 200,
            "data": {},
            "text": "",
            "latency_ms": latency_ms,
            "headers": {},
        }

        if result is None:
            return response

        # 尝试解析为 JSON
        if isinstance(result, dict):
            response["status_code"] = result.get("status_code", 200)
            response["data"] = result.get("data", result.get("body", result))
            response["text"] = result.get("text", json.dumps(response["data"], ensure_ascii=False))
            response["headers"] = result.get("headers", {})
            return response

        if isinstance(result, str):
            try:
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    response["status_code"] = parsed.get("status_code", 200)
                    response["data"] = parsed.get("data", parsed.get("body", parsed))
                    response["text"] = result
                    response["headers"] = parsed.get("headers", {})
                    return response
            except json.JSONDecodeError:
                pass

            response["text"] = result
            response["data"] = {"raw": result}
            return response

        if isinstance(result, list):
            response["data"] = result
            response["text"] = json.dumps(result, ensure_ascii=False)
            return response

        # 其他类型：转为字符串
        response["text"] = str(result)
        response["data"] = {"raw": str(result)}
        return response


# ============================================================================
# 单例工厂
# ============================================================================

_rest_runtime_instance: RestRuntime | None = None


def get_rest_runtime() -> RestRuntime:
    """获取 RestRuntime 单例实例。"""
    global _rest_runtime_instance
    if _rest_runtime_instance is None:
        _rest_runtime_instance = RestRuntime()
    return _rest_runtime_instance