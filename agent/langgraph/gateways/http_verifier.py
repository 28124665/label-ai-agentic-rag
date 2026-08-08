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
"""HTTP 验证器网关（微服务阶段，RAG 增强能力融合 §3.4）。

HttpVerifierGateway 通过 HTTP 调用独立部署的验证微服务，替代进程内 import 调用。
微服务拆分后，HallucinationDetector 和 Grader 组件部署为独立服务，通过 REST API
提供幻觉检测和检索质量评估能力。

API 契约（微服务侧需实现）：
  POST /v1/verify/faithfulness
    Request:  {"answer": str, "context": str, "query": str, "tenant_id": str}
    Response: {"faithfulness_score": float, "action": str, "claims": list}

  POST /v1/verify/grade_retrieval
    Request:  {"query": str, "chunks": list[dict], "tenant_id": str}
    Response: {"quality_score": float, "has_relevant": bool,
               "relevant_count": int, "graded_docs": list[dict]}

设计意图：
  - 单体阶段：使用 LocalVerifierGateway（import 调用，零网络开销）
  - 灰度阶段：使用 ShadowVerifierGateway（Local + Http 双跑对比，未来实现）
  - 微服务阶段：使用 HttpVerifierGateway（HTTP 调用，故障隔离 + 独立扩容）

httpx 依赖说明：
  httpx 为微服务阶段的可选依赖（单体阶段不需要）。本模块使用延迟导入，
  模块本身可在未安装 httpx 的环境中正常导入；仅在方法被实际调用时才触发
  httpx 导入，未安装时抛出 ImportError。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.langgraph.gateways.verifier import VerifierGateway

logger = logging.getLogger(__name__)


class HttpVerifierGateway(VerifierGateway):
    """远程验证器网关（微服务阶段）。

    通过 HTTP 调用独立部署的验证微服务，提供幻觉检测和检索质量评估能力。
    替代 LocalVerifierGateway 的进程内调用，实现故障隔离和独立扩容。

    构造时不绑定租户——每次请求按 tenant_id 路由，与 GatewayResolver 的
    解析模式一致（§5.7 约束：禁止在构造期绑定租户级模式或凭证）。
    """

    def __init__(self, base_url: str, timeout: float = 10.0):
        """初始化 HTTP 验证器网关。

        Args:
            base_url: 验证微服务的基础 URL（如 http://verifier-service:8080）
            timeout: HTTP 请求超时时间（秒），默认 10.0
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def verify_faithfulness(
        self, answer: str, context: str, query: str, tenant_id: str
    ) -> dict[str, Any]:
        """验证答案忠实度（幻觉检测）。

        通过 HTTP POST 调用验证微服务的 /v1/verify/faithfulness 端点，
        由微服务侧的 HallucinationDetector 执行三层验证。

        Args:
            answer: 待验证的生成答案文本
            context: 检索到的参考上下文（已拼接的文档内容）
            query: 用户原始查询
            tenant_id: 租户 ID

        Returns:
            微服务返回的标准化结果字典：
            - faithfulness_score: 忠实度分数 (0.0 ~ 1.0)
            - action: 处置建议 (pass/filter/regenerate/refuse)
            - claims: 论断列表

        Raises:
            ImportError: httpx 未安装
            httpx.HTTPStatusError: HTTP 状态码非 2xx
            httpx.RequestError: 网络错误（超时、连接失败等）
        """
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/verify/faithfulness",
                json={
                    "answer": answer,
                    "context": context,
                    "query": query,
                    "tenant_id": tenant_id,
                },
            )
            response.raise_for_status()
            return response.json()

    async def grade_retrieval(
        self, query: str, chunks: list[dict], tenant_id: str
    ) -> dict[str, Any]:
        """评估检索结果质量。

        通过 HTTP POST 调用验证微服务的 /v1/verify/grade_retrieval 端点，
        由微服务侧的 Grader 执行 LLM 语义评估。

        Args:
            query: 用户查询文本
            chunks: 检索到的文档块列表
            tenant_id: 租户 ID

        Returns:
            微服务返回的标准化结果字典：
            - quality_score: 检索质量分数 (0.0 ~ 1.0)
            - has_relevant: 是否存在相关文档
            - relevant_count: 相关文档数量
            - graded_docs: 评估后的文档列表

        Raises:
            ImportError: httpx 未安装
            httpx.HTTPStatusError: HTTP 状态码非 2xx
            httpx.RequestError: 网络错误（超时、连接失败等）
        """
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/v1/verify/grade_retrieval",
                json={
                    "query": query,
                    "chunks": chunks,
                    "tenant_id": tenant_id,
                },
            )
            response.raise_for_status()
            return response.json()
