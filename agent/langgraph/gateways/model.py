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
"""模型网关协议与统一调用结果（微服务拆分 §5.2）。

定义 ModelGateway Protocol 与 ChatResult dataclass，作为 8 个 LLM 调用点的统一接口。
替代直接使用 LLMBundle，为后续 model-client 化（Stage B）与独立模型网关（Stage C）预留升级路径。

两阶段策略（§5.2，AI 禁止跳阶段）：
- Stage A（本期 Phase 0-2）：LLMBundleAdapter 委托现有 LLMBundle，零行为变化；
- Stage B（Phase 1-2）：HttpConfigResolver 替换配置来源，provider 受控裁剪拷贝进
  packages/llm-runtime/（D9），agent-service 镜像不再依赖 ragflow 源码树。

错误语义对齐（§5.2，D4 语义冻结）：
- LLMBundle 失败时返回 **ERROR** 前缀字符串而不抛异常；
- adapter 保持该语义——失败时返回 ChatResult(content="**ERROR**...", error_code=...)
  而非抛异常（调用点已有 content.startswith("**ERROR**") 判断，禁止改动）；
- 仅「配置解析失败」（ModelConfigResolveError）允许抛异常。

范围声明（本期不支持即显式失败）：
- 流式输出（chat_streamly）、bind_tools 原生工具调用、图像/语音不在 8 个调用点路径上；
- 调用到未支持能力时抛 NotImplementedError（快速失败，禁止静默降级）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ChatResult:
    """统一 LLM 调用结果（§5.2，v2.1 替代裸 str 返回）。

    现有 planner/llm_router 调用 async_chat 期望 (content, token_count) 元组；
    影子模式 diff 又要求 finish_reason 与 token 计数——裸 str 无法承载。

    Attributes:
        content: LLM 生成的文本内容（失败时为 "**ERROR**..." 前缀字符串）
        input_tokens: 输入 token 数
        output_tokens: 输出 token 数
        total_tokens: 总 token 数（input + output）
        finish_reason: 终止原因（stop / length / tool_calls / error / content_filter）
        model: 实际生效模型名（便于审计与影子 diff）
        provider_request_id: provider 侧 request id（无则空串）
        error_code: **ERROR** 类失败的结构化错误码；成功为 None
    """

    content: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    finish_reason: str
    model: str
    provider_request_id: str
    error_code: str | None

    @property
    def is_error(self) -> bool:
        """是否为 **ERROR** 类失败（与现有调用点 startswith("**ERROR**") 判断对齐）。"""
        return self.error_code is not None or self.content.startswith("**ERROR**")


@runtime_checkable
class ModelGateway(Protocol):
    """模型网关协议（§5.2）。

    8 个 LLM 调用点（§5.2 表）通过 GatewayResolver.model_for(tenant_id) 获取实现：
    1. intent_router LLM 语义路由
    2. planner DAG 规划
    3. rag_tool 跨语言扩展（仅 local 模式路径）
    4. db_runtime.get_chat_model（SQL Agent）
    5. react_subgraph._resolve_llm_callable
    6. reflection.call_reflection_llm
    7. llm_generate
    8. hallucination._verify_faithfulness

    签名对齐现有 LLMBundle.async_chat(system, history, gen_conf)；
    gen_conf 键（temperature / max_tokens 等）原样透传，adapter 不得改写默认值。
    """

    async def async_chat(
        self,
        *,
        tenant_id: str,
        llm_id: str,
        system: str,
        history: list[dict],
        gen_conf: dict,
    ) -> ChatResult:
        """非流式 LLM 对话。

        Args:
            tenant_id: 租户 ID
            llm_id: 模型 ID（缺省返回租户默认 chat 模型）
            system: 系统提示词
            history: 对话历史（[{role, content}]）
            gen_conf: 生成配置（temperature / max_tokens 等，原样透传）

        Returns:
            ChatResult: 统一调用结果（失败时 content 以 "**ERROR**" 开头，error_code 非空）。

        Raises:
            ModelConfigResolveError: 配置解析失败（唯一允许抛异常的场景）。
            NotImplementedError: 调用未支持的能力（流式/工具调用/图像/语音）。
        """
        ...

    async def embed(
        self,
        *,
        tenant_id: str,
        llm_id: str,
        texts: list[str],
    ) -> tuple[list[list[float]], int]:
        """文本向量化。

        Args:
            tenant_id: 租户 ID
            llm_id: Embedding 模型 ID
            texts: 待向量化的文本列表

        Returns:
            tuple: (vectors, used_tokens) —— 向量列表与消耗的 token 数。

        Raises:
            ModelConfigResolveError: 配置解析失败。
        """
        ...

    async def rerank(
        self,
        *,
        tenant_id: str,
        rerank_id: str,
        query: str,
        texts: list[str],
    ) -> tuple[list[float], int]:
        """文本重排序。

        Args:
            tenant_id: 租户 ID
            rerank_id: Rerank 模型 ID
            query: 查询文本
            texts: 待排序的文本列表

        Returns:
            tuple: (scores, used_tokens) —— 相关性分数列表与消耗的 token 数。

        Raises:
            ModelConfigResolveError: 配置解析失败。
        """
        ...
