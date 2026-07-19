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
"""Prompt 构建与输出语言控制模块。

对应设计文档「Prompt 构建与输出语言控制」章节。

为何 Prompt 中使用 ``original_text`` 而非 ``search_text``
---------------------------------------------------------

文档入库阶段对每条 chunk 存储了两个字段：

- ``original_text``：保留原文语言与语义（例如台湾繁体原文）；
- ``search_text``：繁转简后的简体文本，主要用于向量检索与 Rerank。

检索阶段（Embedding / Rerank）必须使用 ``search_text`` 与查询在同一简体语义空间内匹配
（见 :mod:`api.utils.query_preprocessor` 与 :mod:`api.utils.multilingual_reranker`）。
但 Prompt 阶段是把参考资料交给 LLM 阅读、理解、作答，是「语义理解」环节而非「字形匹配」
环节，应保留原文的完整语言语义信息：

- 台湾繁体原文中的语气、用词、专有名词（如「鴻海精密」「伺服器」）只有保留繁体才能体现
  完整语义上下文，让 LLM 基于原文做出更准确的回答；
- 若改用 ``search_text``（简体），繁简转换过程中可能丢失某些台湾本地用语语境；
- LLM 本身具备多语言理解能力，可以无障碍地阅读繁体原文，并通过 Prompt 指令控制输出语言。

因此本模块在拼接 context 时严格使用 :func:`api.utils.chunk_preprocessor.extract_original_text`
提取每个 chunk 的 ``original_text`` 字段。

输出语言控制策略
----------------

输出语言通过 Prompt 指令控制 LLM，**而非后处理转换**。原因：

- 后处理转换（如机器翻译或繁简转换）会损失语义、引入翻译错误，且无法处理专有名词、
  代码、数字等内容；
- LLM 在生成阶段直接按指定语言输出，可以结合上下文语义做出更自然的语言切换；
- 通过 ``OUTPUT_LANG_INSTRUCTIONS`` 字典提供三种语言的输出指令，根据 ``query_lang`` 选取
  对应指令拼接到 Prompt 末尾，未知语言默认按简体中文兜底。
"""

from rag.nlp.lang_detect import LANG_EN, LANG_ZH_SIMPLIFIED, LANG_ZH_TRADITIONAL

from api.utils.chunk_preprocessor import extract_original_text

# 输出语言指令：根据 query_lang 选择对应指令拼接到 Prompt 末尾
OUTPUT_LANG_INSTRUCTIONS: dict[str, str] = {
    LANG_ZH_TRADITIONAL: "请用繁体中文回答。",
    LANG_ZH_SIMPLIFIED: "请用简体中文回答。",
    LANG_EN: "Please answer in English.",
}

# Prompt 模板：使用 {context} / {query} / {output_lang_instruction} 占位符
_PROMPT_TEMPLATE = """你是一个知识库问答助手。请基于以下参考资料回答用户问题。

参考资料：
{context}

用户问题：{query}

{output_lang_instruction}"""


def build_context(context_chunks: list[dict], max_chunks: int = 10) -> str:
    """根据上下文 chunk 列表拼接 context 字符串。

    每条 chunk 用 ``【序号】 original_text`` 格式拼接，多条之间用空行分隔，
    序号从 1 开始递增。最多截取前 ``max_chunks`` 条。

    Args:
        context_chunks: 上下文 chunk 字典列表。
        max_chunks: 最大拼接条数，默认 10。超出部分会被截断。

    Returns:
        拼接后的 context 字符串。形如::

            【1】 chunk1_original_text

            【2】 chunk2_original_text

        若 ``context_chunks`` 为空，返回空字符串。
    """
    # 边界检查 1：空列表
    # 作用：避免后续代码因空列表而报错，快速返回空字符串
    if not context_chunks:
        return ""

    # 边界检查 2：max_chunks <= 0
    # 作用：避免返回负数或零条结果，保证语义正确
    if max_chunks <= 0:
        return ""

    # 步骤 1：截取前 max_chunks 条
    # 作用：控制 Prompt 长度，避免超出 LLM 的上下文窗口限制
    # 为什么默认 10 条？
    #   1. 经验值：10 条 chunk 通常能提供足够的上下文信息
    #   2. 平衡：太少信息不足，太多会超出 token 限制或引入噪声
    truncated = context_chunks[:max_chunks]
    # 步骤 2：拼接 context 字符串
    # 关键设计：使用 original_text 而非 search_text
    # 为什么必须用 original_text？
    #   1. original_text 保留原文语言语义（如台湾繁体原文的语气、用词、专有名词）
    #   2. search_text 是繁转简后的文本，可能丢失某些台湾本地用语语境
    #   3. LLM 具备多语言理解能力，可以无障碍阅读繁体原文
    #   4. 保留原文让 LLM 基于完整语义上下文做出更准确的回答
    # 格式设计：【序号】 original_text
    #   1. 序号便于 LLM 引用特定 chunk（如"根据参考资料【1】..."）
    #   2. 空行分隔提高可读性，便于 LLM 理解 chunk 边界
    parts = [f"【{idx + 1}】 {extract_original_text(chunk)}" for idx, chunk in enumerate(truncated)]
    return "\n\n".join(parts)


def build_prompt(query: str, context_chunks: list[dict], query_lang: str) -> str:
    """构建完整的知识库问答 Prompt。

    处理流程：

    1. 调用 :func:`build_context` 拼接 context（使用每个 chunk 的 ``original_text``）；
    2. 根据 ``query_lang`` 从 :data:`OUTPUT_LANG_INSTRUCTIONS` 选取输出语言指令，
       未知语言默认按 :data:`LANG_ZH_SIMPLIFIED` 兜底；
    3. 套用 :data:`_PROMPT_TEMPLATE` 拼接完整 Prompt。

    Args:
        query: 用户查询字符串。
        context_chunks: 上下文 chunk 字典列表。
        query_lang: 查询语言标识（``zh-simplified`` / ``zh-traditional`` / ``en``）。

    Returns:
        完整的 Prompt 字符串。
    """
    # 步骤 1：拼接 context（参考资料）
    # 作用：将检索到的 chunk 组织成 LLM 可理解的格式
    context = build_context(context_chunks)
    # 步骤 2：选择输出语言指令
    # 关键设计：通过 Prompt 指令控制 LLM 输出语言，而非后处理转换
    # 为什么不用后处理转换？
    #   1. 后处理转换（如机器翻译或繁简转换）会损失语义、引入翻译错误
    #   2. 后处理无法正确处理专有名词、代码、数字等内容
    #   3. LLM 在生成阶段直接按指定语言输出，可以结合上下文语义做出更自然的语言切换
    # 兜底策略：未知语言默认按简体中文处理
    # 作用：避免因 query_lang 异常导致 Prompt 缺少输出语言指令
    output_lang_instruction = OUTPUT_LANG_INSTRUCTIONS.get(query_lang, OUTPUT_LANG_INSTRUCTIONS[LANG_ZH_SIMPLIFIED])
    # 步骤 3：套用模板拼接完整 Prompt
    # 模板结构：
    #   1. 角色定义：知识库问答助手
    #   2. 参考资料：检索到的 chunk（使用 original_text）
    #   3. 用户问题：原始查询
    #   4. 输出语言指令：控制 LLM 输出语言
    return _PROMPT_TEMPLATE.format(context=context, query=query, output_lang_instruction=output_lang_instruction)
