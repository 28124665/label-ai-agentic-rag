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
"""HTTP Verifier 协议模块（Claim 级可追溯幻觉检测设计 §4.10，任务二建议 4）。

定义验证微服务拆分后的强类型 HTTP 协议：请求/响应 schema、payload 上限、
schema 版本协商、幂等 attempt ID 与部分失败语义。

设计背景（§4.10.1 问题根因）：
  当前 VerifierGateway 通过本地 Python 调用验证器（LocalVerifierGateway）。
  未来拆分为独立验证微服务时，直接用 dict 透传存在以下隐患：
    1. 缺少 schema version → 协议升级不兼容，无法协商
    2. 缺少 payload 上限 → 大批量 claim × evidence 可能导致 OOM
    3. 缺少幂等 attempt ID → 重试产生重复验证，浪费 LLM 调用预算
    4. 缺少部分失败语义 → 单个 pair 失败影响整批，无法精细降级
  本模块用 Pydantic BaseModel 强类型化协议，根治上述问题。

模块定位：
  - 本模块仅定义「协议契约 + 客户端」，不实现具体验证逻辑；
  - HttpVerifierGateway（http_verifier.py）在微服务阶段将委托本客户端发起调用；
  - 单体阶段不加载本模块（local 模式直接走 LocalVerifierGateway）。

httpx 依赖说明：
  httpx 为微服务阶段可选依赖。本模块对 httpx 采用延迟导入——模块本身可在
  未安装 httpx 的环境中正常 import；仅在 HTTPVerifierClient.verify_batch
  实际调用时才触发导入，未安装时抛出 ImportError（与 http_verifier.py 一致）。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

# 复用网关层错误基类（errors.py 仅定义异常，无循环依赖风险）。
# 让本模块的协议异常归入 GatewayError 体系，便于上层 RAGTool 用
# isinstance 统一分类捕获（§5.1 错误类型必须可被 isinstance 识别）。
from agent.langgraph.gateways.errors import GatewayError

logger = logging.getLogger(__name__)

# 支持的协议版本集合。
# 新增版本时向前兼容追加；移除版本前需保证所有调用方已升级，
# 否则旧客户端会收到 ProtocolError（快速失败，禁止静默降级）。
SUPPORTED_SCHEMA_VERSIONS: set[str] = {"1.0"}


# ========== 自定义异常（§4.10.2） ==========


class PayloadTooLargeError(GatewayError):
    """请求 payload 超过上限（体积 > 1MB 或 pair 数量 > max_pairs）。

    处置：调用方应分批重试或缩减 claim 数量，不应原样重试。
    """


class ProtocolError(GatewayError):
    """协议错误（schema version 不兼容 / 响应结构非法 / HTTP 状态码非 2xx）。

    处置：通常为不可重试错误，需排查客户端与服务端版本匹配。
    """


class VerifierTimeoutError(GatewayError):
    """验证器调用超时（整批超时或网络读超时）。

    处置：可按指数退避重试，但需复用同一 attempt_id 以触发服务端幂等缓存。
    """


# ========== 请求 Payload ==========


class ClaimPayload(BaseModel):
    """Claim 请求 payload（单条待验证论断）。

    对应 §4.1 Claim 强类型 schema 的传输视图：仅携带验证所需字段，
    不包含服务端计算的派生字段（如 server_is_required、invalid_indices），
    避免客户端伪造服务端决策结果。
    """

    claim_id: str
    text: str
    # 该 claim 声明引用的证据编号列表（对应 evidences 列表的下标或 snapshot 编号）。
    # pair 数量 = Σ len(citation_indices)，受 max_pairs 上限约束。
    citation_indices: list[int] = Field(default_factory=list)
    # 论断类型：statement（默认）/ quantity / date / entity / negation 等，
    # 服务端可据此选择不同验证策略（如 quantity 走数值比对）。
    claim_type: str = "statement"


class EvidencePayload(BaseModel):
    """Evidence 请求 payload（单条证据）。

    支持非结构化（content）与结构化（structured_data）两种证据形态：
    - 文本证据：source_type=text，证据放在 content
    - DB 证据：source_type=db，查询结果放在 structured_data（含聚合值）
    - 检索证据：source_type=retrieval，chunk 内容放在 content，metadata 含 doc_id
    """

    evidence_id: str
    source_type: str
    content: str = ""
    # 结构化数据（DB 行 / 聚合结果等）。Pydantic v2 必须用 default_factory
    # 处理可变默认值，禁止直接用 {}（会跨实例共享）。
    structured_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerifierBudgetPayload(BaseModel):
    """验证预算 payload（§4.9 整轮验证预算的传输视图）。

    将 §4.9 VerificationBudget 中「跨网络需要约束」的字段抽出：
    per_pair_timeout_ms / total_timeout_ms / max_llm_calls。
    其余预算字段（max_claims_per_section、batch_concurrency 等）为
    服务端内部调度参数，不通过协议暴露，避免客户端干预服务端调度。
    """

    per_pair_timeout_ms: int = Field(default=3000, ge=0)
    total_timeout_ms: int = Field(default=30000, ge=0)
    max_llm_calls: int = Field(default=50, ge=0)


class VerifierRequest(BaseModel):
    """HTTP verifier 请求 schema（§4.10.2，强类型 + 显式版本号）。

    显式 schema_version 支持协议演进：服务端按版本路由处理逻辑，
    客户端按版本协商能力，避免隐式不兼容。

    幂等语义：同 attempt_id 重复请求由服务端返回缓存结果（不重复验证），
    客户端通过 X-Attempt-Id header 携带，服务端按 attempt_id 做幂等键。
    """

    # 协议版本号（默认 1.0，客户端可指定更高版本协商）。
    schema_version: str = "1.0"
    # 幂等 attempt ID：同 ID 重复请求返回缓存结果，不重复消耗 LLM 预算。
    attempt_id: str
    tenant_id: str

    # 验证内容：claims × evidences 构成 pair 矩阵。
    claims: list[ClaimPayload] = Field(default_factory=list)
    # 不可变证据快照 ID：服务端可凭此 ID 拉取证据，避免每次重传 evidences。
    # 与 evidences 二选一：有 snapshot_id 时 evidences 可为空（服务端按 ID 取）。
    evidence_snapshot_id: str = ""
    evidences: list[EvidencePayload] = Field(default_factory=list)

    # 验证配置：启用的验证器类型（默认三层：规则 + NLI + LLM）。
    verifier_types: list[str] = Field(default_factory=lambda: ["rule", "nli", "llm"])
    # 验证预算（可选，缺省时由客户端 timeout_ms 兜底）。
    budget: VerifierBudgetPayload | None = None

    # 上限约束（服务端强制执行，客户端预先校验快速失败）：
    # max_claims 限制单请求 claim 数，max_pairs 限制 claim × citation 总 pair 数。
    max_claims: int = Field(default=30, ge=1)
    max_pairs: int = Field(default=150, ge=1)


# ========== 响应 Payload ==========


class PairVerdictPayload(BaseModel):
    """单个 pair（claim × evidence）裁决 payload。

    三层验证结果独立回传（rule_result / nli_result / llm_result），
    便于上层做加权投票或按层降级（如 LLM 超时时仅用 rule + nli 结果）。
    """

    claim_id: str
    evidence_id: str
    # pair 终态：supported（证据支持）/ contradicted（证据反驳）/
    # insufficient（证据不足）/ verifier_error（验证器异常，不可信）。
    pair_status: str
    # 验证器执行状态：ok / timeout / error（与 pair_status 解耦，
    # verifier_error 时 verifier_status 指明失败原因类别）。
    verifier_status: str
    # 三层独立结果，默认 unknown（未启用或未执行该层时保留 unknown）。
    rule_result: str = "unknown"
    nli_result: str = "unknown"
    llm_result: str = "unknown"


class FailedPairPayload(BaseModel):
    """失败 pair payload（部分失败语义，§4.10.2）。

    单个 pair 验证失败不影响其他 pair——失败 pair 写入 failed_pairs，
    成功 pair 正常返回 pair_verdicts，partial_failure=true 标记整批部分失败。
    调用方可据此决定：部分失败时接受成功结果 + 标记失败 claim 为待复核。
    """

    claim_id: str
    evidence_id: str
    # 失败原因类别（便于分类告警与重试策略）：
    # timeout / protocol_error / model_unavailable / payload_too_large
    error_type: str
    error_message: str


class VerifierStatsPayload(BaseModel):
    """验证统计 payload（用于预算监控与成本归集）。

    上层可据此校验服务端是否超额调用 LLM/NLI，并计算单次验证成本。
    cache_hits 反映幂等缓存命中率，过低可能预示 attempt_id 生成策略异常。
    """

    total_pairs: int = 0
    successful_pairs: int = 0
    failed_pairs: int = 0
    llm_calls: int = 0
    nli_calls: int = 0
    elapsed_ms: int = 0
    cache_hits: int = 0


class VerifierResponse(BaseModel):
    """HTTP verifier 响应 schema（§4.10.2）。

    回传 attempt_id 用于幂等确认：客户端可核对响应 attempt_id 与请求一致，
    不一致则视为协议异常（防止服务端误返回其他请求的缓存结果）。
    """

    schema_version: str = "1.0"
    # 回传 attempt ID（幂等确认）：客户端应校验与请求一致。
    attempt_id: str
    # 每个 pair 的独立裁决（成功的 pair）。
    pair_verdicts: list[PairVerdictPayload] = Field(default_factory=list)
    # 是否部分失败：true 表示部分 pair 验证失败，需查 failed_pairs。
    partial_failure: bool = False
    failed_pairs: list[FailedPairPayload] = Field(default_factory=list)
    stats: VerifierStatsPayload | None = None


# ========== HTTP 客户端 ==========


class HTTPVerifierClient:
    """HTTP verifier 客户端（§4.10.2）。

    封装验证微服务的 HTTP 调用细节，对上层 HttpVerifierGateway 暴露
    verify_batch 单一入口。客户端侧执行前置校验（payload 上限、pair 数量、
    schema 版本），快速失败以避免无效网络往返。

    设计要点：
    - schema version 协商：请求/响应都带 schema_version，不兼容时抛 ProtocolError
    - payload 上限：请求 payload > 1MB 拒绝，pair 数 > max_pairs 拒绝
    - 幂等 attempt ID：通过 X-Attempt-Id header 携带，服务端据此去重
    - 超时处理：budget.total_timeout_ms 优先，否则用构造参数 timeout_ms 兜底
    - 部分失败透传：服务端返回 partial_failure 时，客户端原样透传，由上层决策降级
    """

    # 请求 payload 体积上限（1MB）。
    # 设定依据：单请求 max_pairs=150，每 pair 的 claim+evidence 文本约 6KB，
    # 150 × 6KB ≈ 900KB，留余量到 1MB。超限分批重试，防服务端 OOM。
    MAX_PAYLOAD_BYTES: int = 1 * 1024 * 1024

    def __init__(self, endpoint: str, timeout_ms: int = 30000):
        """初始化 HTTP verifier 客户端。

        Args:
            endpoint: 验证微服务基础 URL（如 http://verifier-service:8080）。
                      末尾斜杠会被剥离，调用时拼接 /v1/verify 路径。
            timeout_ms: 默认整批超时（毫秒）。当 VerifierRequest.budget
                        提供时，budget.total_timeout_ms 优先于本参数。
        """
        self.endpoint = endpoint.rstrip("/")
        self.timeout_ms = timeout_ms

    async def verify_batch(self, request: VerifierRequest) -> VerifierResponse:
        """批量验证（§4.10.2）。

        执行前置校验后发起 HTTP POST，携带 X-Attempt-Id 实现幂等。
        服务端按 attempt_id 去重——同 ID 重复请求返回缓存结果，不重复验证。

        Args:
            request: 强类型验证请求（含 claims、evidences、budget 等）。

        Returns:
            VerifierResponse: 强类型验证响应（含 pair_verdicts、failed_pairs、stats）。

        Raises:
            PayloadTooLargeError: payload 体积 > 1MB 或 pair 数 > max_pairs 或
                                  claim 数 > max_claims。
            ProtocolError: schema version 不兼容 / HTTP 状态码非 2xx /
                           响应体结构非法 / 网络层错误（非超时）。
            VerifierTimeoutError: 整批超时或网络读超时。
            ImportError: httpx 未安装（微服务阶段可选依赖）。
        """
        # ---- 1. 请求 schema version 兼容性检查 ----
        # 客户端版本不在支持集合内时快速失败，避免服务端按未知版本错误处理。
        if request.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ProtocolError(f"请求 schema version 不兼容: {request.schema_version} 不在支持列表 {sorted(SUPPORTED_SCHEMA_VERSIONS)}")

        # ---- 2. claim 数量上限检查 ----
        # 预先校验避免无效网络往返（服务端也会强制，此处为快速失败）。
        if len(request.claims) > request.max_claims:
            raise PayloadTooLargeError(f"claim 数量超过上限: {len(request.claims)} > {request.max_claims}")

        # ---- 3. pair 数量上限检查 ----
        # pair 数 = Σ 每条 claim 的 citation_indices 数量（claim × citation）。
        # 超限应分批请求或缩减引用，而非原样发送导致服务端拒绝。
        total_pairs = sum(len(c.citation_indices) for c in request.claims)
        if total_pairs > request.max_pairs:
            raise PayloadTooLargeError(f"pair 数量超过上限: {total_pairs} > {request.max_pairs}")

        # ---- 4. payload 体积上限检查 ----
        # 序列化为 JSON 后计算 UTF-8 字节数，与实际传输体积一致。
        # 复用序列化结果作为请求体，避免二次序列化开销。
        payload_json = request.model_dump_json()
        payload_size = len(payload_json.encode("utf-8"))
        if payload_size > self.MAX_PAYLOAD_BYTES:
            raise PayloadTooLargeError(f"Payload 体积超过上限: {payload_size} bytes > {self.MAX_PAYLOAD_BYTES} bytes")

        # ---- 5. 解析整批超时（budget 优先，构造参数兜底）----
        total_timeout_ms = request.budget.total_timeout_ms if request.budget else self.timeout_ms
        # httpx 超时单位为秒，需将毫秒转换为秒。
        timeout_s = total_timeout_ms / 1000.0

        # ---- 6. HTTP 调用（携带 X-Attempt-Id 实现幂等）----
        # 延迟导入 httpx：单体阶段（local 模式）不触发导入，避免无谓依赖。
        import httpx

        # 显式 Content-Type + X-Attempt-Id 头。
        # X-Attempt-Id 是幂等关键：服务端按此 ID 做去重缓存，重复请求不重复验证。
        headers = {
            "Content-Type": "application/json",
            "X-Attempt-Id": request.attempt_id,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(
                    f"{self.endpoint}/v1/verify",
                    content=payload_json,
                    headers=headers,
                )
        except httpx.TimeoutException as e:
            # 超时统一映射为 VerifierTimeoutError，调用方可复用同一 attempt_id
            # 重试（服务端幂等缓存可能已部分完成，重试可拿缓存结果）。
            raise VerifierTimeoutError(f"验证微服务调用超时（attempt_id={request.attempt_id}, timeout_ms={total_timeout_ms}）") from e
        except httpx.RequestError as e:
            # 连接失败 / DNS 解析失败 / 协议错误等非超时网络异常。
            # 错误消息禁止包含凭证（§5.1）；httpx 异常本身不含敏感信息。
            raise ProtocolError(f"验证微服务网络错误（attempt_id={request.attempt_id}）: {e}") from e

        # ---- 7. HTTP 状态码检查 ----
        # 非 2xx 视为协议错误（4xx 为请求非法，5xx 为服务端故障，均不重试原请求）。
        if response.status_code >= 400:
            # 截取响应体前 500 字符用于诊断，避免日志爆炸；不含凭证。
            body_snippet = response.text[:500] if response.text else ""
            raise ProtocolError(f"验证微服务返回非 2xx 状态码: {response.status_code} （attempt_id={request.attempt_id}, body={body_snippet!r}）")

        # ---- 8. 响应体解析 ----
        try:
            body: dict[str, Any] = response.json()
        except ValueError as e:
            raise ProtocolError(f"验证微服务响应体非合法 JSON（attempt_id={request.attempt_id}）") from e

        # ---- 9. 响应 schema version 兼容性检查 ----
        # 服务端版本不在支持集合内时拒绝，防止按未知结构误解析。
        resp_version = body.get("schema_version")
        if resp_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ProtocolError(f"响应 schema version 不兼容: 期望 {sorted(SUPPORTED_SCHEMA_VERSIONS)} 之一, 实际 {resp_version!r}")

        # ---- 10. 构造强类型响应模型 ----
        # model_validate 递归校验嵌套 payload，结构非法时抛 ValidationError，
        # 统一转换为 ProtocolError（调用方无需感知 pydantic 细节）。
        try:
            return VerifierResponse.model_validate(body)
        except Exception as e:
            raise ProtocolError(f"验证微服务响应结构非法（attempt_id={request.attempt_id}）: {e}") from e
