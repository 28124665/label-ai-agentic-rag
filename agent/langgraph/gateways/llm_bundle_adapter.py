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
"""LLMBundleAdapter — Stage A 模型网关实现（微服务拆分 §5.2，PR-0.3a）。

按调用点现状对齐委托路径，零行为变化：
- intent_router / planner / reflection / react_subgraph（§5.2 调用点 1/2/5/6）
  现状为 TenantLLMService.model_instance（原始 ChatModel，返回 (content, token)
  元组，无 LLMBundle 的 reasoning/tool_call 后处理与 usage 副作用）；
- db_runtime / sql_agent（§5.2 调用点 4）
  现状为 LLMBundle + get_model_config_by_type_and_name。
为后续 model-client 化（Stage B）与独立模型网关（Stage C）预留升级路径。

两阶段策略（§5.2，AI 禁止跳阶段）：
- Stage A（本期）：LLMBundleAdapter 委托 LLMBundle，不迁移 provider 代码；
- Stage B（Phase 1-2）：HttpConfigResolver 替换配置来源，provider 受控裁剪拷贝。

错误语义对齐（§5.2，D4 语义冻结）：
- LLMBundle.async_chat 失败时返回 **ERROR** 前缀字符串而不抛异常；
- adapter 保持该语义——失败时返回 ChatResult(content="**ERROR**...", error_code=...)
  而非抛异常（调用点已有 startswith("**ERROR**") 判断，禁止改动）；
- 仅「配置解析失败」（ModelConfigResolveError）允许抛异常。

LLMBundle.async_chat 返回值说明：
- 现有 LLMBundle.async_chat 返回裸 str（content），不返回 token 计数；
- adapter 将 token 计数置 0（Stage A 零行为变化，调用点现状均丢弃 token_count）；
- Stage B 引入 HttpConfigResolver 后，provider 直调可获取真实 token 计数。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.langgraph.gateways.errors import ModelConfigResolveError
from agent.langgraph.gateways.model import ChatResult

logger = logging.getLogger(__name__)

# **ERROR** 类失败的结构化错误码（§5.2 错误语义对齐）
_ERROR_CODE_LLM = "LLM_ERROR"
_ERROR_CODE_CONFIG = "MODEL_CONFIG_RESOLVE_FAILED"


class LLMBundleAdapter:
    """Stage A 模型网关实现——委托 LLMBundle（§5.2）。

    实现 ModelGateway 协议（§5.2），内部委托现有 LLMBundle + 配置解析函数。
    PR-0.3a：零行为变化，仅替换调用方式（DI 接口替换，D4 允许）。

    配置解析优先级（与 db_runtime.get_chat_model 语义一致）：
    1. tenant_id + llm_id → get_model_config_by_type_and_name 精确解析；
    2. tenant_id only → get_tenant_default_model_by_type 取租户默认；
    3. 解析失败 → 抛 ModelConfigResolveError（唯一允许抛异常的场景）。
    """

    def __init__(self) -> None:
        """初始化 LLMBundleAdapter。

        无构造期状态——配置按请求解析（§5.7 约束：
        禁止在构造期绑定租户级模式或凭证）。
        """
        pass

    # ========== ModelGateway 协议实现 ==========

    async def async_chat(
        self,
        *,
        tenant_id: str,
        llm_id: str,
        system: str,
        history: list[dict],
        gen_conf: dict,
        prefer_bundle: bool = True,
    ) -> ChatResult:
        """非流式 LLM 对话。

        委托路径按 prefer_bundle 与各调用点现状对齐（D4 零行为变化）：
        - prefer_bundle=True（默认，§5.2 调用点 4 现状，与改造前 adapter 行为一致）：
          get_model_config_by_type_and_name → LLMBundle，
          async_chat 返回裸 str（content），含 reasoning/tool_call 后处理与 usage 副作用；
        - prefer_bundle=False（§5.2 调用点 1/2/5/6 现状，需显式传入）：
          TenantLLMService.model_instance → 原始 ChatModel，
          async_chat 返回 (content, token_count) 元组，无 LLMBundle 后处理。

        默认 True 与改造前 adapter（仅委托 LLMBundle）及既有测试语义保持一致，
        确保未显式声明的调用点零行为变化；仅 intent_router/planner/reflection
        （§5.2 调用点 1/2/6，现状为 model_instance）显式传 prefer_bundle=False。

        Args:
            tenant_id: 租户 ID
            llm_id: 模型 ID（缺省返回租户默认 chat 模型）
            system: 系统提示词
            history: 对话历史（[{role, content}]）
            gen_conf: 生成配置（temperature / max_tokens 等，原样透传）
            prefer_bundle: True 时走 LLMBundle 路径（db_runtime/sql_agent 现状）；
                           False 时走原始 ChatModel 路径（intent_router/planner/
                           reflection/react_subgraph 现状）。

        Returns:
            ChatResult: 统一调用结果。失败时 content 以 "**ERROR**" 开头，
                       error_code 非空（§5.2 错误语义对齐，不抛异常）。

        Raises:
            ModelConfigResolveError: 配置解析失败（唯一允许抛异常的场景）。
        """
        # 1. 按调用点现状解析模型实例 + 记录实际生效模型名
        if prefer_bundle:
            chat_mdl = self._resolve_chat_model(tenant_id, llm_id)
            model_name = self._get_model_name(chat_mdl)
        else:
            chat_mdl = self._resolve_chat_model_instance(tenant_id, llm_id)
            model_name = llm_id

        # 2. 委托底层 async_chat
        #    - ChatModel.async_chat 返回 (content, token_count) 元组
        #    - LLMBundle.async_chat 返回裸 str（content）
        try:
            raw = await chat_mdl.async_chat(
                system=system,
                history=history,
                gen_conf=gen_conf,
            )
        except Exception as e:
            # provider 调用异常 → **ERROR** 语义（不抛异常，§5.2 错误语义对齐）
            # 注意：异常消息不得包含 api_key 等敏感信息（附录 D #4）
            logger.warning(
                "[LLMBundleAdapter] async_chat 调用异常: %s (model=%s)",
                type(e).__name__,
                model_name,
            )
            return ChatResult(
                content=f"**ERROR**:{type(e).__name__}: LLM 调用失败",
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                finish_reason="error",
                model=model_name,
                provider_request_id="",
                error_code=_ERROR_CODE_LLM,
            )

        # 3. 归一化返回值：(content, token_count) 元组 或 裸 str
        if isinstance(raw, tuple):
            content = str(raw[0]) if raw else ""
            total_tokens = int(raw[1]) if len(raw) > 1 and raw[1] else 0
        else:
            content = str(raw) if raw else ""
            total_tokens = 0

        # 4. 处理 **ERROR** 前缀（失败语义：返回 **ERROR** 字符串而非抛异常）
        #    调用点已有 content.startswith("**ERROR**") 判断，此处仅包装为 ChatResult
        if content.startswith("**ERROR**"):
            return ChatResult(
                content=content,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                finish_reason="error",
                model=model_name,
                provider_request_id="",
                error_code=_ERROR_CODE_LLM,
            )

        return ChatResult(
            content=content,
            input_tokens=0,
            output_tokens=total_tokens,
            total_tokens=total_tokens,
            finish_reason="stop",
            model=model_name,
            provider_request_id="",
            error_code=None,
        )

    async def embed(
        self,
        *,
        tenant_id: str,
        llm_id: str,
        texts: list[str],
    ) -> tuple[list[list[float]], int]:
        """文本向量化（委托 LLMBundle.encode）。

        Args:
            tenant_id: 租户 ID
            llm_id: Embedding 模型 ID
            texts: 待向量化的文本列表

        Returns:
            tuple: (vectors, used_tokens)

        Raises:
            ModelConfigResolveError: 配置解析失败。
        """
        embd_mdl = self._resolve_model_by_type(
            tenant_id, llm_id, "EMBEDDING"
        )
        # LLMBundle.encode 是同步方法，返回 (embeddings, used_tokens)
        return embd_mdl.encode(texts)

    async def rerank(
        self,
        *,
        tenant_id: str,
        rerank_id: str,
        query: str,
        texts: list[str],
    ) -> tuple[list[float], int]:
        """文本重排序（委托 LLMBundle.similarity）。

        Args:
            tenant_id: 租户 ID
            rerank_id: Rerank 模型 ID
            query: 查询文本
            texts: 待排序的文本列表

        Returns:
            tuple: (scores, used_tokens)

        Raises:
            ModelConfigResolveError: 配置解析失败。
        """
        rerank_mdl = self._resolve_model_by_type(
            tenant_id, rerank_id, "RERANK"
        )
        # LLMBundle.similarity 是同步方法，返回 (sim_scores, used_tokens)
        return rerank_mdl.similarity(query, texts)

    # ========== 配置解析（私有方法） ==========

    @staticmethod
    def _resolve_chat_model(tenant_id: str, llm_id: str) -> Any:
        """解析 Chat 模型 LLMBundle（与 db_runtime.get_chat_model 语义一致）。

        优先级：
        1. tenant_id + llm_id → get_model_config_by_type_and_name 精确解析；
        2. tenant_id only → get_tenant_default_model_by_type 取租户默认；
        3. 解析失败 → 抛 ModelConfigResolveError。

        Args:
            tenant_id: 租户 ID
            llm_id: 模型 ID（空串时取租户默认）

        Returns:
            LLMBundle 实例。

        Raises:
            ModelConfigResolveError: 配置解析失败。
        """
        from api.db.services.llm_service import LLMBundle
        from api.db.joint_services.tenant_model_service import (
            get_model_config_by_type_and_name,
            get_tenant_default_model_by_type,
        )
        from common.constants import LLMType

        try:
            # 优先精确解析
            if tenant_id and llm_id:
                chat_model_config = get_model_config_by_type_and_name(
                    tenant_id, LLMType.CHAT, llm_id
                )
                if chat_model_config:
                    return LLMBundle(tenant_id, chat_model_config)

            # 回退到租户默认
            if tenant_id:
                chat_model_config = get_tenant_default_model_by_type(
                    tenant_id, LLMType.CHAT
                )
                if chat_model_config:
                    return LLMBundle(tenant_id, chat_model_config)

        except LookupError as e:
            # 配置不存在（404 等价语义）
            raise ModelConfigResolveError(
                f"Chat 模型配置不存在: tenant_id={tenant_id}, llm_id={llm_id} ({e})"
            )
        except Exception as e:
            # 其他配置解析异常
            raise ModelConfigResolveError(
                f"Chat 模型配置解析失败: tenant_id={tenant_id}, "
                f"llm_id={llm_id} ({type(e).__name__})"
            )

        # 无可用配置
        raise ModelConfigResolveError(
            f"Chat 模型配置不存在: tenant_id={tenant_id}, llm_id={llm_id}"
        )

    @staticmethod
    def _resolve_chat_model_instance(tenant_id: str, llm_id: str) -> Any:
        """解析原始 ChatModel（TenantLLMService.model_instance 路径）。

        与 §5.2 调用点 1/2/5/6（intent_router/planner/react_subgraph/reflection）
        现状严格对齐：
        - 配置解析：TenantLLMService.get_model_config（含 split_model_name_and_factory
          与 fid 失配回退），而非 get_model_config_by_type_and_name；
        - 实例创建：TenantLLMService.model_instance → 原始 ChatModel，
          async_chat 返回 (content, token_count) 元组，无 LLMBundle 后处理与
          usage 副作用。

        Args:
            tenant_id: 租户 ID
            llm_id: 模型 ID（空串时取租户默认 chat 模型）

        Returns:
            原始 ChatModel 实例（async_chat 返回 (content, token_count) 元组）。

        Raises:
            ModelConfigResolveError: 配置解析失败或无法创建实例。
        """
        from api.db.services.tenant_llm_service import TenantLLMService
        from common.constants import LLMType

        try:
            model_config = TenantLLMService.get_model_config(
                tenant_id=tenant_id,
                llm_type=LLMType.CHAT.value,
                llm_name=llm_id or None,
            )
        except LookupError as e:
            raise ModelConfigResolveError(
                f"Chat 模型配置不存在: tenant_id={tenant_id}, llm_id={llm_id} ({e})"
            )
        except Exception as e:
            raise ModelConfigResolveError(
                f"Chat 模型配置解析失败: tenant_id={tenant_id}, "
                f"llm_id={llm_id} ({type(e).__name__})"
            )

        try:
            llm_instance = TenantLLMService.model_instance(model_config)
        except Exception as e:
            raise ModelConfigResolveError(
                f"Chat 模型实例创建失败: tenant_id={tenant_id}, "
                f"llm_id={llm_id} ({type(e).__name__})"
            )

        if not llm_instance:
            # 与历史 ValueError("无法创建 LLM 实例") 同语义
            raise ModelConfigResolveError(
                f"无法创建 LLM 实例: tenant_id={tenant_id}, llm_id={llm_id}"
            )

        return llm_instance

    @staticmethod
    def _resolve_model_by_type(
        tenant_id: str, llm_id: str, model_type: str
    ) -> Any:
        """按类型解析 Embedding/Rerank 模型 LLMBundle。

        Args:
            tenant_id: 租户 ID
            llm_id: 模型 ID
            model_type: "EMBEDDING" | "RERANK"

        Returns:
            LLMBundle 实例。

        Raises:
            ModelConfigResolveError: 配置解析失败。
        """
        from api.db.services.llm_service import LLMBundle
        from api.db.joint_services.tenant_model_service import (
            get_model_config_by_type_and_name,
        )
        from common.constants import LLMType

        llm_type = getattr(LLMType, model_type, None)
        if llm_type is None:
            raise ModelConfigResolveError(
                f"未知的模型类型: {model_type}"
            )

        try:
            if tenant_id and llm_id:
                model_config = get_model_config_by_type_and_name(
                    tenant_id, llm_type, llm_id
                )
                if model_config:
                    return LLMBundle(tenant_id, model_config)
        except LookupError as e:
            raise ModelConfigResolveError(
                f"{model_type} 模型配置不存在: "
                f"tenant_id={tenant_id}, llm_id={llm_id} ({e})"
            )
        except Exception as e:
            raise ModelConfigResolveError(
                f"{model_type} 模型配置解析失败: "
                f"tenant_id={tenant_id}, llm_id={llm_id} "
                f"({type(e).__name__})"
            )

        raise ModelConfigResolveError(
            f"{model_type} 模型配置不存在: "
            f"tenant_id={tenant_id}, llm_id={llm_id}"
        )

    @staticmethod
    def _get_model_name(chat_mdl: Any) -> str:
        """从 LLMBundle 提取实际生效模型名（便于审计与影子 diff）。"""
        try:
            config = getattr(chat_mdl, "model_config", None)
            if isinstance(config, dict):
                return str(config.get("llm_name", ""))
        except Exception:
            pass
        return ""
