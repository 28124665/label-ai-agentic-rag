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
"""Chunk 预处理模块。

对应设计文档「Chunk 双字段存储」章节。

为何需要双字段存储
------------------

在 RAG 系统中，知识库可能同时收录简体、繁体、英文等多种语言的文档。若直接以原文
入库并向量化，会导致同一语义的繁体文档与简体查询之间的字面差异降低 BM25 / 向量
召回率。因此设计如下双字段方案：

- ``search_text``：检索文本。中文统一为简体（繁体 -> 简体），英文保持原文，
  用于写入向量库与全文检索索引，保证不同语言来源的 chunk 在同一检索空间内可比对。
- ``original_text``：原文展示文本。保留 chunk 的原始语言（繁体保持繁体），
  用于在最终 Prompt 中展示给用户 / LLM，避免出现「繁体文档被改写为简体后引用」
  这种破坏原文准确性的情况。

不同语言的 search_text 生成策略
-------------------------------

- 繁体中文（``zh-traditional``）：调用 :func:`rag.nlp.t2s.convert` 进行繁体 -> 简体
  转换（含 OpenCC 字形转换 + 台湾用语词汇替换 + 专有名词白名单保护）。
- 简体中文（``zh-simplified``）：保持原文，无需转换。
- 英文（``en``）：保持原文，无需转换。

异常处理
--------

单个 chunk 预处理失败时记录 warning 日志，并返回兜底结果（``original_lang``
固定为 ``zh-simplified``、``search_text`` 与 ``original_text`` 取原始 content、
``metadata`` 为空字典），以保证批量处理不会因单条数据异常而中断。
"""

import logging

from rag.nlp.lang_detect import LANG_ZH_SIMPLIFIED, LANG_ZH_TRADITIONAL, detect_language
from rag.nlp.t2s import convert

logger = logging.getLogger(__name__)


def preprocess_chunk(chunk: dict) -> dict:
    """预处理单个 chunk，生成双字段（original_text + search_text）及语言标识。

    流程：
        1. 提取 chunk 中的 ``content`` 字段，若缺失则依次 fallback 到
           ``content_with_weight``、``text``，最终 fallback 为空字符串。
        2. 调用 :func:`rag.nlp.lang_detect.detect_language` 检测语言。
        3. 繁体中文调用 :func:`rag.nlp.t2s.convert` 转简体作为 ``search_text``；
           简体中文 / 英文保持原文。
        4. 保留 chunk 中原有字段（如 doc_id、page_number 等），仅新增
           ``original_text``、``original_lang``、``search_text``、``metadata`` 字段。
        5. ``metadata`` 字段：若 chunk 中已存在且为 dict 则保留，否则置为空字典。

    Args:
        chunk: 原始 chunk 字典，至少包含 ``content`` 字段（或
            ``content_with_weight`` / ``text`` 作为 fallback）。

    Returns:
        新的 chunk 字典（不修改入参），新增 ``original_text``、``original_lang``、
        ``search_text``、``metadata`` 字段。预处理失败时返回兜底结果。

    Note:
        本函数不会修改入参 ``chunk``，而是返回其浅拷贝加新增字段。
    """
    # 类型检查：chunk 必须是 dict，否则返回兜底结果
    # 作用：避免后续代码因类型错误而崩溃，保证批量处理的健壮性
    if not isinstance(chunk, dict):
        logger.warning("preprocess_chunk: chunk is not a dict, got %s, returning fallback result", type(chunk).__name__)
        return {"original_text": "", "original_lang": LANG_ZH_SIMPLIFIED, "search_text": "", "metadata": {}}

    # 提取原始文本：支持多种字段名，兼容不同数据源
    # 优先级：content > content_with_weight > text > 空字符串
    # 为什么支持多种字段名？
    #   1. content：标准字段名，大多数场景使用
    #   2. content_with_weight：RAGFlow 原有字段名，带权重信息的文本
    #   3. text：某些数据源可能使用 text 字段
    #   4. 空字符串：兜底，避免后续代码因 None 而报错
    raw_content = chunk.get("content")
    if raw_content is None:
        raw_content = chunk.get("content_with_weight")
    if raw_content is None:
        raw_content = chunk.get("text")
    if raw_content is None:
        raw_content = ""
    # 防御性编程：非字符串统一转字符串
    # 作用：某些数据源可能传入数字、列表等非字符串类型，统一转换避免后续错误
    if not isinstance(raw_content, str):
        try:
            raw_content = str(raw_content)
        except Exception:
            raw_content = ""

    try:
        # 步骤 1：检测语言
        # 作用：判断文本是繁体中文、简体中文还是英文，决定后续处理策略
        original_lang = detect_language(raw_content)
        # 步骤 2：生成 search_text
        # 策略：繁体中文需要繁转简，简体/英文保持原文
        # 为什么繁体需要转换？
        #   1. 主流中文 Embedding 模型（如 BAAI/bge-large-zh）训练数据偏向简体
        #   2. 繁体查询与简体文档之间字形差异会降低向量召回率
        #   3. 统一转为简体后，所有中文文本在同一语义空间，保证检索效果
        if original_lang == LANG_ZH_TRADITIONAL:
            search_text = convert(raw_content)
        else:
            search_text = raw_content

        # 步骤 3：构建结果字典
        # 关键设计：浅拷贝入参，保留所有原有字段
        # 作用：chunk 中可能包含 doc_id、page_number、metadata 等业务字段，必须保留
        result = dict(chunk)
        # 新增三个字段：
        # - original_text：保留原文，用于 Prompt 展示，避免破坏原文语义
        # - original_lang：语言标识，用于后续路由和输出语言控制
        # - search_text：检索文本，用于向量检索和 Rerank，保证在同一语义空间
        result["original_text"] = raw_content
        result["original_lang"] = original_lang
        result["search_text"] = search_text
        # metadata 字段：若已存在且为 dict 则保留，否则置为空字典
        # 作用：metadata 用于存储 chunk 的元数据（如来源、作者等），保持向后兼容
        existing_metadata = result.get("metadata")
        if not isinstance(existing_metadata, dict):
            result["metadata"] = {}
        return result
    except Exception as e:
        # 异常兜底：单个 chunk 预处理失败时，返回兜底结果，不中断批量处理
        # 兜底策略：
        #   1. original_text：保留原始 content（即使预处理失败，原文仍可展示）
        #   2. original_lang：默认简体中文（最安全的假设）
        #   3. search_text：与 original_text 相同（不做繁转简，避免引入错误）
        #   4. metadata：空字典（避免元数据错误影响后续逻辑）
        logger.warning("preprocess_chunk: failed to preprocess chunk (content=%r): %s", raw_content[:200] if raw_content else "", e)
        fallback = dict(chunk)
        fallback["original_text"] = raw_content
        fallback["original_lang"] = LANG_ZH_SIMPLIFIED
        fallback["search_text"] = raw_content
        fallback["metadata"] = {}
        return fallback


def preprocess_chunks(chunks: list[dict]) -> list[dict]:
    """批量预处理 chunk 列表。

    Args:
        chunks: 原始 chunk 字典列表。

    Returns:
        预处理后的 chunk 字典列表，顺序与输入一致；单条异常不会中断整体处理。
    """
    if not chunks:
        return []
    return [preprocess_chunk(chunk) for chunk in chunks]


def extract_search_text(chunk: dict) -> str:
    """从已处理的 chunk 中提取 ``search_text``。

    若 chunk 中不存在 ``search_text`` 字段，则 fallback 到 ``content`` 字段
    （并依次 fallback 到 ``content_with_weight`` / ``text``），最终 fallback 为空字符串。

    Args:
        chunk: chunk 字典，可以是已预处理过的，也可以是原始的。

    Returns:
        ``search_text`` 字段值；若不存在则返回 ``content`` 字段值；若仍不存在返回空字符串。
    """
    if not isinstance(chunk, dict):
        return ""
    search_text = chunk.get("search_text")
    if search_text is not None:
        return search_text if isinstance(search_text, str) else str(search_text)
    content = chunk.get("content")
    if content is None:
        content = chunk.get("content_with_weight")
    if content is None:
        content = chunk.get("text")
    if content is None:
        return ""
    return content if isinstance(content, str) else str(content)


def extract_original_text(chunk: dict) -> str:
    """从已处理的 chunk 中提取 ``original_text``。

    若 chunk 中不存在 ``original_text`` 字段，则 fallback 到 ``content`` 字段
    （并依次 fallback 到 ``content_with_weight`` / ``text``），最终 fallback 为空字符串。

    Args:
        chunk: chunk 字典，可以是已预处理过的，也可以是原始的。

    Returns:
        ``original_text`` 字段值；若不存在则返回 ``content`` 字段值；若仍不存在返回空字符串。
    """
    if not isinstance(chunk, dict):
        return ""
    original_text = chunk.get("original_text")
    if original_text is not None:
        return original_text if isinstance(original_text, str) else str(original_text)
    content = chunk.get("content")
    if content is None:
        content = chunk.get("content_with_weight")
    if content is None:
        content = chunk.get("text")
    if content is None:
        return ""
    return content if isinstance(content, str) else str(content)


def index_chunk(chunk: dict, kb=None, tenant_id=None) -> dict:
    """生成适配新 schema 的 chunk 字典，供向量库写入使用。

    对应设计文档「向量库 Schema 与分区适配」章节。

    本函数在 :func:`preprocess_chunk` 的基础上做一层薄封装，产出包含
    ``original_text`` / ``original_lang`` / ``search_text`` / ``metadata`` 字段的
    chunk 字典，使其与 ``conf/infinity_mapping.json``、``conf/mapping.json``、
    ``conf/os_mapping.json`` 中定义的新 schema 保持一致。

    重要说明
    --------
    实际写入向量库仍由原有存储层（``rag/utils/*_conn.py``、``common/doc_store/*``）完成，
    本函数仅负责生成「适配新 schema」的 chunk 字典。新字段会被现有 insert 逻辑自然透传
    （mapping JSON 决定 schema），因此**不需要修改任何向量库连接代码**。

    Embedding 模型选择
    ------------------
    当传入 ``kb`` 对象时，理论上可读取 ``kb.language`` 字段决定 embedding 模型（例如
    中文场景使用 ``BAAI/bge-large-zh``、英文场景使用 ``text-embedding-3-small``）。
    但当前阶段保持最小实现，仅做日志记录，不实际切换 embedding 模型，以保证向后兼容：
    旧的知识库仍使用其 ``embd_id`` 字段指定的 embedding 模型，不因本函数引入而改变行为。

    Args:
        chunk: 原始 chunk 字典，至少包含 ``content`` 字段（或
            ``content_with_weight`` / ``text`` 作为 fallback）。
        kb: 可选的知识库对象。当传入时，可读取 ``kb.language`` 字段决定 embedding 模型；
            当前阶段仅做日志记录，不实际切换 embedding 模型。
        tenant_id: 可选的租户 ID，仅用于日志记录，便于排查问题。

    Returns:
        预处理后的 chunk 字典，包含 ``original_text`` / ``original_lang`` /
        ``search_text`` / ``metadata`` 字段。预处理失败时返回兜底结果（见
        :func:`preprocess_chunk` 的异常处理说明）。
    """
    result = preprocess_chunk(chunk)
    if kb is not None:
        # 当前阶段保持最小实现：仅记录日志，不实际切换 embedding 模型（保持向后兼容）
        kb_language = getattr(kb, "language", None)
        logger.info(
            "index_chunk: kb provided (kb_language=%s, tenant_id=%s), embedding model selection deferred for backward compatibility",
            kb_language,
            tenant_id,
        )
    return result
