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
"""Prompt 组装节点。

整合多源检索结果（RAG 文档、数据库查询结果、Web 搜索结果）到统一上下文，
并根据查询语言注入语言输出指令。

参考原有实现：
- rag/prompts/generator.py: kb_prompt, memory_prompt 等 Prompt 生成函数
- agent/tools/retrieval.py: 检索结果的格式化输出
"""

import logging
import time
from typing import Any

from agent.langgraph.state import AgentState
from api.utils.chunk_preprocessor import extract_original_text

logger = logging.getLogger(__name__)

# 语言输出指令映射
LANGUAGE_INSTRUCTIONS = {
    "zh_CN": "请使用简体中文回答以下问题。",
    "zh_TW": "請使用繁體中文回答以下問題。",
    "en": "Please answer the following question in English.",
}


def prompt_assembly_node(state: AgentState) -> dict[str, Any]:
    """Prompt 组装节点。

    整合多源检索结果到 merged_context，并构建最终 Prompt。

    融合策略：
    - RAG 文档：按分数降序排列，截取 top 5
    - 数据库结果：格式化为表格或键值对
    - Web 搜索结果：按搜索分数排列
    - 多源融合：RAG > DB > Web（优先级递减）

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 merged_context 和 final_prompt
    """
    start_time = time.time()

    user_question = state.get("user_question", "")
    query_lang = state.get("query_lang", "zh_CN")
    rag_docs = state.get("rag_docs", [])
    db_result = state.get("db_result", {})
    web_docs = state.get("web_docs", [])
    route_target = state.get("route_target", "chitchat")
    conversation_history = state.get("conversation_history", []) or []

    # 1. 构建上下文片段
    context_parts = []

    # 1a. RAG 文档上下文
    if rag_docs:
        rag_context = _format_rag_context(rag_docs)
        if rag_context:
            context_parts.append(rag_context)
            logger.info(f"[prompt_assembly] RAG 文档: {len(rag_docs)} 条")

    # 1b. 数据库查询结果上下文
    if db_result and db_result.get("rows"):
        db_context = _format_db_context(db_result)
        if db_context:
            context_parts.append(db_context)
            logger.info(f"[prompt_assembly] 数据库结果: {db_result.get('row_count', 0)} 行")

    # 1c. Web 搜索结果上下文
    if web_docs:
        web_context = _format_web_context(web_docs)
        if web_context:
            context_parts.append(web_context)
            logger.info(f"[prompt_assembly] Web 搜索: {len(web_docs)} 条")

    # 2. 合并上下文
    merged_context = "\n\n".join(context_parts) if context_parts else ""

    # 3. 构建最终 Prompt
    final_prompt = _build_prompt(
        user_question=user_question,
        merged_context=merged_context,
        query_lang=query_lang,
        route_target=route_target,
        conversation_history=conversation_history,
    )

    logger.info(f"[prompt_assembly] Prompt 组装完成: context_len={len(merged_context)}, prompt_len={len(final_prompt)}")

    return {
        "merged_context": merged_context,
        "final_prompt": final_prompt,
        "node_timings": {"prompt_assembly": int((time.time() - start_time) * 1000)},
    }


def _format_rag_context(rag_docs: list[dict]) -> str:
    """格式化 RAG 文档为上下文文本。

    参考 rag/prompts/generator.py 的 kb_prompt 函数。

    Args:
        rag_docs: RAG 检索到的文档列表

    Returns:
        str: 格式化后的上下文文本
    """
    if not rag_docs:
        return ""

    parts = ["【参考资料】"]
    for i, doc in enumerate(rag_docs[:5], 1):
        # 使用 original_text 保留原文语言（如繁体原文），与历史 RAGFlow Prompt 构建策略一致
        content = extract_original_text(doc)
        source = doc.get("source", "")
        score = doc.get("score", 0.0)
        if content:
            source_info = f"（来源：{source}，相关度：{score:.2f}）" if source else ""
            parts.append(f"{i}. {content}{source_info}")

    return "\n".join(parts)


def _format_db_context(db_result: dict) -> str:
    """格式化数据库查询结果为上下文文本。

    支持聚合的多数据库结果（is_aggregated=True 时，每行标注 _source_db 来源）。

    Args:
        db_result: 数据库查询结果

    Returns:
        str: 格式化后的上下文文本
    """
    sql = db_result.get("sql", "")
    rows = db_result.get("rows", [])
    row_count = db_result.get("row_count", 0)
    is_aggregated = db_result.get("is_aggregated", False)
    source_dbs = db_result.get("source_dbs", [])

    if not rows:
        return ""

    parts = ["【数据库查询结果】"]
    if sql:
        parts.append(f"查询语句：{sql}")
    parts.append(f"结果行数：{row_count}")

    # 聚合结果时标注来源数据库
    if is_aggregated and source_dbs:
        parts.append(f"数据来源：{', '.join(source_dbs)}")

    # 格式化数据行（限制最多 20 行）
    display_rows = rows[:20]
    if display_rows:
        # 获取列名（过滤掉 _source_db 内部字段）
        columns = [k for k in display_rows[0].keys() if k != "_source_db"]
        if columns:
            parts.append("列名：" + "、".join(columns))

        # 格式化每行数据
        for i, row in enumerate(display_rows, 1):
            # 提取来源数据库标注（如果存在）
            source_db = row.get("_source_db", "")
            # 格式化行数据，排除内部字段
            row_items = [(k, v) for k, v in row.items() if k != "_source_db"]
            row_str = "，".join(f"{k}={v}" for k, v in row_items)
            if source_db:
                row_str += f"（来源：{source_db}）"
            parts.append(f"  第{i}行：{row_str}")

        if row_count > 20:
            parts.append(f"  ...（省略 {row_count - 20} 行）")

    return "\n".join(parts)


def _format_web_context(web_docs: list[dict]) -> str:
    """格式化 Web 搜索结果为上下文文本。

    Args:
        web_docs: Web 搜索到的文档列表

    Returns:
        str: 格式化后的上下文文本
    """
    if not web_docs:
        return ""

    parts = ["【网络搜索结果】"]
    for i, doc in enumerate(web_docs[:6], 1):
        content = doc.get("content", "")
        title = doc.get("title", "")
        url = doc.get("url", "")
        if content:
            title_info = f"（{title}）" if title else ""
            url_info = f" [{url}]" if url else ""
            parts.append(f"{i}. {content[:500]}{title_info}{url_info}")

    return "\n".join(parts)


def _build_prompt(
    user_question: str,
    merged_context: str,
    query_lang: str,
    route_target: str,
    conversation_history: list[dict] | None = None,
) -> str:
    """构建最终 Prompt。

    参考 rag/prompts/generator.py 中的 Prompt 模板。

    Args:
        user_question: 用户问题
        merged_context: 合并后的上下文
        query_lang: 查询语言
        route_target: 路由目标
        conversation_history: 对话历史，``[{role, content}]``，
            最近 10 条会被注入到 Prompt 中以保持上下文连贯性

    Returns:
        str: 最终 Prompt
    """
    # 语言输出指令
    lang_instruction = LANGUAGE_INSTRUCTIONS.get(query_lang, LANGUAGE_INSTRUCTIONS["zh_CN"])

    # 对话历史段落（最近 10 条）
    history_block = _format_conversation_history(conversation_history or [])

    if route_target == "chitchat" or not merged_context:
        # 闲聊模式或无上下文：直接回答
        prompt_parts = [lang_instruction]
        if history_block:
            prompt_parts.append("")
            prompt_parts.append(history_block)
        prompt_parts.append("")
        prompt_parts.append(f"用户问题：{user_question}")
        return "\n".join(prompt_parts)

    # 构建带上下文的 Prompt
    prompt_parts = [
        "你是一个智能助手，请根据以下参考资料回答用户问题。",
        "如果参考资料中没有相关信息，请诚实告知，不要编造答案。",
        f"{lang_instruction}",
    ]
    if history_block:
        prompt_parts.append("")
        prompt_parts.append(history_block)
    prompt_parts.extend(
        [
            "",
            merged_context,
            "",
            f"用户问题：{user_question}",
            "",
            "请基于以上参考资料给出准确、完整的回答。如果引用了参考资料，请标注来源。",
        ]
    )

    return "\n".join(prompt_parts)


def _format_conversation_history(history: list[dict]) -> str:
    """将对话历史格式化为 Prompt 段落。

    仅保留最近 10 条消息，避免上下文过长。每条消息以
    ``用户: ...`` 或 ``助手: ...`` 的形式呈现。

    Args:
        history: 对话历史列表，元素为 ``{role, content}``

    Returns:
        str: 格式化后的对话历史段落；为空时返回空字符串
    """
    if not history:
        return ""

    role_label = {"user": "用户", "assistant": "助手", "system": "系统"}
    recent = history[-10:]
    lines = ["【对话历史】"]
    for msg in recent:
        role = msg.get("role", "user")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        label = role_label.get(role, role)
        lines.append(f"{label}: {content}")

    # 仅保留至少一条有效历史时才返回段落
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
