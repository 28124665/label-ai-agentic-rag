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
import asyncio
import logging
import os
from abc import ABC
from typing import Any

from agent.component.base import ComponentBase, ComponentParamBase
from agent.component.state_fields import set_state
from common.connection_utils import timeout
from common.constants import LLMType

"""HyDE（Hypothetical Document Embeddings）组件。

对应需求：P2-FR-02（查询重写 - HyDE 假设文档嵌入）

功能说明：
  使用 LLM 生成一个假设性答案，用该答案代替原始查询进行检索，
  利用假设答案与真实文档之间的语义相似性提高检索召回率。

实现方式：
  1. 接收用户原始查询，通过 LLM 生成一段简洁的假设性答案（≤200字）
  2. 使用低温度（默认 0.3）确保生成结果稳定、确定性强
  3. 假设答案仅用于检索（写入 sys.hyde_retrieval_query），
     不参与最终答案生成（不写入 sys.final_answer）
  4. 默认关闭（enable_hyde=False），需要用户主动开启
  5. 如果 LLM 生成失败，回退使用原始查询进行检索
  6. 只取假设答案的第一段（按双换行分割），保持检索聚焦
"""


DEFAULT_HYDE_PROMPT = """你是一个假设性文档生成专家。请根据用户查询，生成一段简洁的假设性答案/文档。
这段内容将作为检索查询，用于在知识库中查找相似的真实文档。

要求：
1. 假设性答案应直接回答用户问题
2. 内容简洁，控制在 200 字以内
3. 可以基于模型常识生成，但应贴近知识库文档风格
4. 不要包含"根据现有资料"等限定语
5. 只输出假设性答案文本，不要解释

用户查询：{query}

假设性答案："""

DEFAULT_HYDE_TEMPERATURE = 0.3
DEFAULT_HYDE_MAX_TOKENS = 256


class HyDEParam(ComponentParamBase):
    """
    Define the HyDE component parameters.
    """

    def __init__(self):
        super().__init__()
        self.query = "sys.query"
        self.llm_id = ""
        self.enable_hyde = False
        self.hyde_prompt = ""
        self.temperature = DEFAULT_HYDE_TEMPERATURE
        self.max_tokens = DEFAULT_HYDE_MAX_TOKENS

    def check(self):
        self.check_defined_type(self.query, "[HyDE] query", ["str"])
        self.check_boolean(self.enable_hyde, "[HyDE] enable_hyde")
        self.check_decimal_float(float(self.temperature), "[HyDE] temperature")
        self.check_nonnegative_number(int(self.max_tokens), "[HyDE] max_tokens")


class HyDE(ComponentBase, ABC):
    """HyDE 假设文档嵌入组件。

    通过 LLM 生成假设性答案，用于改善检索效果。
    核心思想：假设答案与真实文档的语义空间更接近，
    比短查询更能命中相关文档（Gao et al., 2022）。
    """
    component_name = "HyDE"

    def get_input_elements(self) -> dict[str, Any]:
        res = {}
        res.update(self.get_input_elements_from_text(self._param.query))
        return res

    def get_input_form(self) -> dict[str, dict]:
        return {
            "query": {"name": "Query", "type": "line"},
        }

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    def _invoke(self, **kwargs):
        return asyncio.run(self._invoke_async(**kwargs))

    @timeout(int(os.environ.get("COMPONENT_EXEC_TIMEOUT", 10 * 60)))
    async def _invoke_async(self, **kwargs):
        if self.check_if_canceled("HyDE processing"):
            return

        query = self._resolve_query(kwargs)

        if not self._param.enable_hyde:
            self._write_outputs(query, "")
            return

        hypothetical_answer = await self._generate_hypothetical_answer(query)
        retrieval_query = hypothetical_answer if hypothetical_answer else query

        self._write_outputs(query, hypothetical_answer, retrieval_query)

    async def _generate_hypothetical_answer(self, query: str) -> str:
        """使用 LLM 生成假设性答案。

        生成策略：
          - 使用低温度（默认 0.3）确保输出稳定
          - 限制 max_tokens（默认 256）控制生成长度
          - 只取第一段输出，避免生成过多无关内容
          - LLM 调用失败时返回空字符串，触发回退逻辑
        """
        if not query or not self._param.llm_id:
            return ""

        chat_mdl = self._create_llm_bundle(self._param.llm_id)
        prompt = self._param.hyde_prompt or DEFAULT_HYDE_PROMPT
        prompt = self.string_format(prompt, {"query": query})
        history = [{"role": "user", "content": prompt}]

        gen_conf = {
            "temperature": float(self._param.temperature),
            "max_tokens": int(self._param.max_tokens),
        }

        try:
            ans = await chat_mdl.async_chat("", history, gen_conf)
        except Exception as e:
            logging.warning(f"[HyDE] LLM generation failed: {e}")
            return ""

        if not ans or "**ERROR**" in ans:
            logging.warning(f"[HyDE] LLM returned error: {ans}")
            return ""

        ans = ans.strip().strip('"').strip("'")
        # Limit to first paragraph to keep retrieval focused.
        first_para = ans.split("\n\n")[0]
        return first_para.strip()

    def _write_outputs(self, query: str, hypothetical_answer: str, retrieval_query: str = "") -> None:
        if not retrieval_query:
            retrieval_query = hypothetical_answer if hypothetical_answer else query

        self.set_output("query", query)
        self.set_output("hypothetical_answer", hypothetical_answer)
        self.set_output("retrieval_query", retrieval_query)
        self.set_output("enable_hyde", self._param.enable_hyde)

        try:
            set_state(self._canvas, "hypothetical_answer", hypothetical_answer)
            set_state(self._canvas, "hyde_retrieval_query", retrieval_query)
        except Exception as e:
            logging.warning(f"[HyDE] Failed to write shared state: {e}")

    def _resolve_query(self, kwargs: dict) -> str:
        key = self._param.query or "sys.query"
        if key in kwargs:
            return kwargs[key] or ""
        return self._canvas.get_variable_value(key) or ""

    def _create_llm_bundle(self, model_id: str):
        from api.db.joint_services.tenant_model_service import get_model_config_by_type_and_name
        from api.db.services.llm_service import LLMBundle

        config = get_model_config_by_type_and_name(self._canvas.get_tenant_id(), LLMType.CHAT, model_id)
        return LLMBundle(self._canvas.get_tenant_id(), config)

    def thoughts(self) -> str:
        return f"Generating hypothetical answer for retrieval: {self._param.query}"
