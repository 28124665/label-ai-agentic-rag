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
"""网关层错误分类（微服务拆分 §5.1）。

错误分类是 RAGTool 异常分类捕获（§5.8）与 quality_check 降级决策的依据：
- RetrievalServiceError 及子类 → 转空结果 + 写 retrieval_error_code → fallback_web
- RetrievalAuthError            → 转空结果 + 写 RETRIEVAL_AUTH + 告警
- RetrievalDataError            → 透传抛出（业务错误，不静默）
- ModelConfigResolveError       → 节点降级链
- ModelProviderError            → provider failover

设计约束：
- 错误类型必须可被 isinstance 识别，便于 RAGTool.invoke 分类捕获；
- 错误消息禁止包含 api_key / X-Service-Token / 解密后凭证（附录 D #4）。
"""

from __future__ import annotations


class GatewayError(Exception):
    """所有网关层错误的基类。"""


# ========== 检索网关错误（§5.1） ==========


class RetrievalServiceError(GatewayError):
    """检索服务异常（超时 / 5xx / 熔断）。

    处置：RAGTool 转空结果 + 写 retrieval_error_code，
    quality_check 直接 fallback_web（重试挂掉的服务无意义）。
    """


class RetrievalTimeoutError(RetrievalServiceError):
    """检索请求超时（读取超时 15s 或连接超时 3s）。"""


class RetrievalCircuitOpenError(RetrievalServiceError):
    """熔断器开启，未发起请求。

    连续 5 次失败后熔断 30s（§5.1 韧性要求）。
    """


class RetrievalAuthError(GatewayError):
    """检索认证失败（HTTP 401/403 或 code=109）。

    处置：不重试，error 日志告警（API Key 失效 / 越权）。
    """


class RetrievalDataError(GatewayError):
    """检索数据错误（HTTP 400 或 code=102）。

    如 dataset_ids 非 list、embedding 不一致、KB 无权限。
    处置：透传抛出（业务错误，与现状 raise Exception("No dataset is selected.") 同语义）。
    """


# ========== 模型网关错误（§5.2） ==========


class ModelConfigResolveError(GatewayError):
    """模型配置解析失败（契约 2）。

    触发场景：
    - 配置 API 不可用且超过 stale_until 硬上限；
    - 配置不存在（404）且负缓存未过期；
    - provider 不在裁剪清单内（D9 快速失败）。

    处置：抛异常（唯一允许抛异常的模型网关错误，其余走 **ERROR** 语义）。
    """


class ModelProviderError(GatewayError):
    """provider 调用失败（可 failover）。

    处置：adapter 内部重试 / failover；最终失败时返回
    ChatResult(content="**ERROR**...", error_code=...) 而非抛异常（§5.2 错误语义对齐）。
    仅当配置解析失败（ModelConfigResolveError）才允许抛异常。
    """
