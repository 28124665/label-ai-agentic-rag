#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
"""反思共享模块。

供 LangGraph reflection_node 和 Canvas ReAct 共用，提供基于 LLM 的工具调用结果反思能力。

主要功能：
- 加载并渲染反思 Prompt 模板（rag/prompts/reflection_v2.md）
- 调用 LLM 执行结构化反思（通过 TenantLLMService 工厂获取模型实例）
- 解析 LLM 输出为结构化结果（key_findings / info_sufficient / gaps / next_action）
- 提供 LLM 不可用时的降级反思
"""

import asyncio
import logging
import re
from typing import Optional

import jinja2
import json_repair

from rag.prompts.template import load_prompt

logger = logging.getLogger(__name__)

# 反思 Prompt 模板加载缓存，避免重复 IO
_REFLECTION_PROMPT_CACHE: Optional[str] = None

# Jinja2 渲染环境，与 rag/prompts/generator.py 保持一致
_REFLECTION_JINJA_ENV = jinja2.Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)

# 合法的下一步动作取值
_VALID_NEXT_ACTIONS = {"proceed_to_generate", "need_more_retrieval", "need_web_search"}


def load_reflection_prompt() -> str:
    """加载反思 Prompt 模板（rag/prompts/reflection_v2.md）。

    使用 rag.prompts.template.load_prompt 加载，与项目现有 Prompt 加载方式一致。
    带缓存，避免重复 IO。

    Returns:
        str: 反思 Prompt 模板内容
    """
    global _REFLECTION_PROMPT_CACHE
    if _REFLECTION_PROMPT_CACHE is None:
        _REFLECTION_PROMPT_CACHE = load_prompt("reflection_v2")
    return _REFLECTION_PROMPT_CACHE


def build_reflection_prompt(question: str, tool_results: list[dict]) -> str:
    """构建反思 Prompt。

    Args:
        question: 用户原始问题
        tool_results: 工具结果列表，每项 {"tool": "rag", "content": "..."}

    Returns:
        str: 渲染后的完整 Prompt
    """
    template = _REFLECTION_JINJA_ENV.from_string(load_reflection_prompt())
    return template.render(question=question, tool_results=tool_results or [])


def parse_reflection_result(llm_output: str) -> dict:
    """解析 LLM 反思输出为结构化结果。

    Args:
        llm_output: LLM 返回的文本（可能包含 JSON）

    Returns:
        dict: {
            "key_findings": list[str],
            "info_sufficient": bool,
            "gaps": list[str],
            "next_action": str,
            "reflection_text": str
        }
    """
    raw_text = llm_output or ""
    # 去除 markdown 代码块标记，提取 JSON 片段
    cleaned = re.sub(r"^.*```json\s*", "", raw_text, flags=re.DOTALL)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"^.*```", "", cleaned, flags=re.DOTALL)

    # 默认返回值（解析失败时使用，info_sufficient=True 让流程继续）
    default_result = {
        "key_findings": [],
        "info_sufficient": True,
        "gaps": [],
        "next_action": "proceed_to_generate",
        "reflection_text": raw_text,
    }

    try:
        obj = json_repair.loads(cleaned)
    except Exception as e:
        logger.warning(f"解析反思结果失败，使用默认值: {e}")
        return default_result

    if not isinstance(obj, dict):
        logger.warning("反思输出非 JSON 对象，使用默认值")
        return default_result

    # 提取并校验各字段
    key_findings = obj.get("key_findings", [])
    if not isinstance(key_findings, list):
        key_findings = [str(key_findings)] if key_findings else []

    info_sufficient = obj.get("info_sufficient", True)
    if not isinstance(info_sufficient, bool):
        info_sufficient = bool(info_sufficient)

    gaps = obj.get("gaps", [])
    if not isinstance(gaps, list):
        gaps = [str(gaps)] if gaps else []

    next_action = obj.get("next_action", "proceed_to_generate")
    if next_action not in _VALID_NEXT_ACTIONS:
        logger.warning(f"无效的 next_action 值: {next_action}，使用默认值 proceed_to_generate")
        next_action = "proceed_to_generate"

    return {
        "key_findings": [str(k) for k in key_findings],
        "info_sufficient": info_sufficient,
        "gaps": [str(g) for g in gaps],
        "next_action": next_action,
        "reflection_text": raw_text,
    }


def fallback_reflection(tool_results: list[dict]) -> dict:
    """降级反思（LLM 不可用时使用）。

    不调用 LLM，直接基于工具结果生成简单反思。
    返回 info_sufficient=True，让流程继续。

    Args:
        tool_results: 工具结果列表

    Returns:
        dict: 结构化反思结果
    """
    findings = []
    for r in (tool_results or []):
        tool = r.get("tool", "unknown")
        content = r.get("content", "")
        if content:
            preview = content[:80] + ("..." if len(content) > 80 else "")
            findings.append(f"[{tool}] {preview}")

    return {
        "key_findings": findings,
        "info_sufficient": True,
        "gaps": [],
        "next_action": "proceed_to_generate",
        "reflection_text": "（降级反思：未调用 LLM，默认信息充分）",
    }


async def call_reflection_llm(
    tenant_id: str,
    llm_id: str,
    question: str,
    tool_results: list[dict],
    timeout_s: float = 5.0,
) -> str:
    """调用 LLM 执行反思。

    通过 ModelGateway（§5.2 调用点 6）获取 LLM 调用结果。
    ModelGateway 替代直接使用 LLMBundle，为后续 model-client 化预留升级路径。

    Args:
        tenant_id: 租户 ID
        llm_id: LLM 模型 ID
        question: 用户问题
        tool_results: 工具结果列表
        timeout_s: 超时时间（秒）

    Returns:
        str: LLM 返回的反思文本

    Raises:
        asyncio.TimeoutError: 超时
        Exception: LLM 调用失败
    """
    from agent.langgraph.gateways.factory import get_gateway_resolver

    # 1. 构建反思 Prompt
    system_prompt = build_reflection_prompt(question, tool_results)
    messages = [{"role": "user", "content": "请输出反思结果的 JSON。"}]
    gen_conf = {"temperature": 0.2, "max_tokens": 500}

    # 2. 通过 ModelGateway 获取 LLM 调用结果（§5.2 调用点 6）
    resolver = get_gateway_resolver()
    gateway = await resolver.model_for(tenant_id)

    # 3. 调用 async_chat，使用 asyncio.wait_for 控制超时
    async def _do_chat() -> str:
        result = await gateway.async_chat(
            tenant_id=tenant_id,
            llm_id=llm_id,
            system=system_prompt,
            history=messages,
            gen_conf=gen_conf,
            prefer_bundle=False,
        )
        return result.content

    ans = await asyncio.wait_for(_do_chat(), timeout=timeout_s)

    # 4. 检查 **ERROR** 前缀（§5.2 错误语义对齐：**ERROR** 判断逻辑禁止改动）
    if ans.startswith("**ERROR**"):
        raise RuntimeError(f"LLM 调用失败: {ans}")

    # 去除可能的 ```json 代码块标记
    ans = re.sub(r"^.*```json\s*", "", ans, flags=re.DOTALL)
    ans = re.sub(r"```\s*$", "", ans, flags=re.DOTALL)
    return ans.strip()
