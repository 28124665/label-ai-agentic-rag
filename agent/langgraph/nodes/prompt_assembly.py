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

    v2.1 §3.2 改造点 5：
    - 优先读取统一 ``evidence`` 列表（经 Evidence Fusion 标准化后的多源证据）
    - evidence 为空时回退到 rag_docs/db_result/web_docs 分散字段（兼容旧流程）
    - 根据 enforcement_mode 选择 Prompt 模板：
      ENFORCED → 要求 LLM 输出结构化 Answer AST（JSON）
      DISABLED → 普通文本 Prompt（向后兼容）

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，包含 merged_context、final_prompt，
            以及 evidence_snapshot_id（如有，供下游 verifier/references 复用同一快照）
    """
    start_time = time.time()

    user_question = state.get("user_question", "")
    query_lang = state.get("query_lang", "zh_CN")
    route_target = state.get("route_target", "chitchat")
    conversation_history = state.get("conversation_history", []) or []

    # ★ v2.1 §3.2 改造点 5：优先读取统一 evidence 列表（替代分散字段）
    evidences = state.get("evidence", []) or []
    evidence_snapshot_id = state.get("evidence_snapshot_id", "") or ""
    enforcement_mode = _get_enforcement_mode(state)

    merged_context = ""
    use_evidence_path = False

    # 1a. 新路径：统一 Evidence 列表 → 分组格式化（带全局编号 [n] 供引用）
    if evidences:
        evidence_context = _format_evidence_context(evidences)
        if evidence_context:
            merged_context = evidence_context
            use_evidence_path = True
            logger.info(f"[prompt_assembly] Evidence 路径: {len(evidences)} 条, enforcement_mode={enforcement_mode}, snapshot_id={evidence_snapshot_id or 'N/A'}")

    # 1b. 回退路径：兼容旧流程，读取 rag_docs/db_result/web_docs 分散字段
    if not use_evidence_path:
        rag_docs = state.get("rag_docs", [])
        db_result = state.get("db_result", {})
        web_docs = state.get("web_docs", [])

        context_parts = []

        if rag_docs:
            rag_context = _format_rag_context(rag_docs)
            if rag_context:
                context_parts.append(rag_context)
                logger.info(f"[prompt_assembly] RAG 文档: {len(rag_docs)} 条")

        if db_result and db_result.get("rows"):
            db_context = _format_db_context(db_result)
            if db_context:
                context_parts.append(db_context)
                logger.info(f"[prompt_assembly] 数据库结果: {db_result.get('row_count', 0)} 行")

        if web_docs:
            web_context = _format_web_context(web_docs)
            if web_context:
                context_parts.append(web_context)
                logger.info(f"[prompt_assembly] Web 搜索: {len(web_docs)} 条")

        merged_context = "\n\n".join(context_parts) if context_parts else ""

    # 2. 构建最终 Prompt
    if use_evidence_path:
        # 新路径：根据 enforcement_mode 选择 Citation-aware Prompt
        # ENFORCED → 要求输出结构化 Answer AST（JSON）；DISABLED/SHADOW → 普通文本（向后兼容）
        final_prompt = _build_citation_aware_prompt(
            question=user_question,
            evidence_context=merged_context,
            lang=query_lang,
            enforcement_mode=enforcement_mode,
            conversation_history=conversation_history,
        )
    else:
        # 回退路径：沿用原有 _build_prompt（完全向后兼容）
        final_prompt = _build_prompt(
            user_question=user_question,
            merged_context=merged_context,
            query_lang=query_lang,
            route_target=route_target,
            conversation_history=conversation_history,
        )

    logger.info(f"[prompt_assembly] Prompt 组装完成: context_len={len(merged_context)}, prompt_len={len(final_prompt)}, evidence_path={use_evidence_path}")

    result: dict[str, Any] = {
        "merged_context": merged_context,
        "final_prompt": final_prompt,
        "node_timings": {"prompt_assembly": int((time.time() - start_time) * 1000)},
    }
    # 返回 evidence_snapshot_id（如有），供下游 verifier/references 节点复用同一不可变快照
    if evidence_snapshot_id:
        result["evidence_snapshot_id"] = evidence_snapshot_id
    return result


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


# ========== v2.1 §3.2 改造点 5：Evidence 优先 + Citation-aware Prompt ==========


def _get_enforcement_mode(state: AgentState) -> str:
    """从 state 读取 enforcement_mode（v2.1 §3.2 改造点 5）。

    默认 ``"disabled"``（legacy 模式），确保未配置幻觉检测时向后兼容，
    避免未接入 Evidence 流程的旧调用方误触 ENFORCED 拒答逻辑。

    Args:
        state: 当前 AgentState

    Returns:
        str: ``disabled`` / ``shadow`` / ``enforced``
    """
    mode = state.get("enforcement_mode", "") or ""
    # 规范化为小写，兼容上游可能写入的大小写差异
    mode = mode.strip().lower()
    if mode in ("disabled", "shadow", "enforced"):
        return mode
    # 未知值或缺失时回退 disabled，保证 fail-open 兼容旧流程
    return "disabled"


def _format_evidence_context(evidences: list[dict]) -> str:
    """格式化统一 Evidence 列表为上下文文本（v2.1 §3.2 改造点 5）。

    替代分散的 ``_format_rag_context`` / ``_format_db_context`` / ``_format_web_context``，
    将多源 Evidence 统一格式化，每条标注全局编号 ``[n]`` 供 Citation-aware Prompt 引用。

    设计决策：
        - 按 ``source_type`` 分组（rag → db → web → report），保持来源优先级可读性
        - 全局连续编号 ``[1] [2] ...``，与 Citation-aware Prompt 中的引用编号对齐
        - 相关度优先取 ``relevance_score``，回退 ``confidence``，保证字段缺失时不报错
        - DB 零行结果或 content 为空时，回退到 title/structured_data 摘要，避免空条目

    Args:
        evidences: Evidence 列表（见 ``evidence/models.py`` Evidence TypedDict）

    Returns:
        str: 格式化后的上下文文本；空列表返回空字符串
    """
    if not evidences:
        return ""

    # 分组展示顺序：知识库 → 数据库 → 网络 → 报表（符合 RAG > DB > Web 阅读直觉）
    source_order = ["rag", "db", "web", "report"]
    source_labels = {
        "rag": "知识库检索",
        "db": "数据库查询",
        "web": "网络搜索",
        "report": "报表数据",
    }

    # 按 source_type 分组（保留组内原始顺序，避免打乱 Evidence Fusion 的排序）
    groups: dict[str, list[dict]] = {}
    for ev in evidences:
        st = ev.get("source_type", "unknown")
        groups.setdefault(st, []).append(ev)

    parts = ["【参考资料】"]
    global_idx = 0
    for st in source_order:
        group = groups.get(st)
        if not group:
            continue
        # 分组小标题，便于 LLM 区分来源类型
        label = source_labels.get(st, st)
        parts.append(f"--- {label}（{st}）---")
        for ev in group:
            global_idx += 1
            content = (ev.get("content") or "").strip()
            # DB 零行结果或 content 为空时，回退到 title/structured_data 摘要
            if not content:
                title = ev.get("title", "")
                sd = ev.get("structured_data") or {}
                if sd:
                    content = f"{title}：{sd}" if title else str(sd)
                else:
                    content = title or "(无内容)"
            # 相关度：优先 relevance_score，回退 confidence
            score = ev.get("relevance_score")
            if score is None:
                score = ev.get("confidence", 0.0)
            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.0
            parts.append(f"[{global_idx}] (来源:{st}, 相关度:{score:.2f}) {content}")

    return "\n".join(parts)


def _build_citation_aware_prompt(
    question: str,
    evidence_context: str,
    lang: str,
    enforcement_mode: str,
    conversation_history: list[dict] | None = None,
) -> str:
    """构建 Citation-aware Prompt（v2.1 §3.2 改造点 5）。

    根据 ``enforcement_mode`` 切换 Prompt 模板：
        - ``enforced``：要求 LLM 输出结构化 Answer AST（JSON），每条 Claim 必须标注
          ``citation_indices``，与 ``evidence/answer_ast.py`` Schema 对齐，供下游
          Claim 级验证（fail-closed）
        - ``disabled`` / ``shadow``：普通文本 Prompt（向后兼容），但要求每个事实句后
          标注 ``[n]`` 引用，为后续 shadow 比对预留能力

    设计决策：
        - ENFORCED 模式输出 JSON AST，便于下游 ``AnswerAST`` 解析 + Claim 级验证
        - DISABLED 模式不强制 AST，保持与 legacy 文本流程兼容
        - 两种模式均明确要求"无证据时不编造"，与 ``_build_prompt`` 行为一致
        - 无证据时退化为简单 Prompt（与 ``_build_prompt`` 闲聊分支行为一致）

    Args:
        question: 用户问题
        evidence_context: 已格式化的 Evidence 上下文（含 ``[n]`` 编号）
        lang: 查询语言（zh_CN/zh_TW/en）
        enforcement_mode: 强制级别（disabled/shadow/enforced）
        conversation_history: 对话历史（可选，``[{role, content}]``）

    Returns:
        str: 最终 Prompt
    """
    lang_instruction = LANGUAGE_INSTRUCTIONS.get(lang, LANGUAGE_INSTRUCTIONS["zh_CN"])
    history_block = _format_conversation_history(conversation_history or [])

    # 无证据时退化为简单 Prompt（与 _build_prompt 闲聊分支行为一致）
    if not evidence_context:
        prompt_parts = [lang_instruction]
        if history_block:
            prompt_parts.append("")
            prompt_parts.append(history_block)
        prompt_parts.append("")
        prompt_parts.append(f"用户问题：{question}")
        return "\n".join(prompt_parts)

    if enforcement_mode == "enforced":
        # ★ ENFORCED 模式：要求输出结构化 Answer AST（JSON）
        # Schema 与 evidence/answer_ast.py 对齐：root SectionNode → Paragraph → Claim/Narrative
        prompt_parts = [
            "你是一个智能助手，请根据以下参考资料回答用户问题。",
            "如果参考资料中没有相关信息，请诚实告知，不要编造答案。",
            f"{lang_instruction}",
            "",
            "【输出格式要求】",
            "你必须输出严格的 JSON 格式（Answer AST），结构如下：",
            "{",
            '  "schema_version": "1.0",',
            '  "root": {',
            '    "node_type": "section",',
            '    "title": "",',
            '    "level": 1,',
            '    "content": [',
            "      {",
            '        "node_type": "paragraph",',
            '        "children": [',
            "          {",
            '            "node_type": "claim",',
            '            "claim_id": "c1",',
            '            "text": "事实陈述文本（不含引用标记）",',
            '            "citation_indices": [1, 2],',
            '            "model_claim_type": "statement",',
            '            "model_is_required": false',
            "          },",
            "          {",
            '            "node_type": "narrative",',
            '            "text": "非事实连接文本（如过渡句、总结句）"',
            "          }",
            "        ]",
            "      }",
            "    ]",
            "  }",
            "}",
            "",
            "【引用标注规则】",
            "1. 每个事实声明（Claim）必须在 citation_indices 中标注所引用的参考资料编号 [n]。",
            "2. citation_indices 中的编号必须对应下方参考资料中的 [n] 编号。",
            "3. 非事实连接文本（Narrative，如过渡句）不需标注引用。",
            "4. 如果某个事实无对应证据，不要编造，应省略该 Claim 或在 narrative 中说明。",
            "5. 只输出 JSON，不要输出任何其他文本或 Markdown 代码块标记。",
        ]
        if history_block:
            prompt_parts.append("")
            prompt_parts.append(history_block)
        prompt_parts.extend(
            [
                "",
                evidence_context,
                "",
                f"用户问题：{question}",
            ]
        )
        return "\n".join(prompt_parts)

    # ★ DISABLED / SHADOW 模式：普通文本 Prompt（向后兼容），但要求 [n] 引用标注
    # 不强制 AST 输出，保持与 legacy 文本流程兼容；[n] 标注为 shadow 比对预留能力
    prompt_parts = [
        "你是一个智能助手，请根据以下参考资料回答用户问题。",
        "如果参考资料中没有相关信息，请诚实告知，不要编造答案。",
        f"{lang_instruction}",
        "",
        "【引用标注规则】",
        "请在回答中每个事实句后标注引用编号 [n]，编号对应下方参考资料中的 [n]。",
        "例如：根据资料显示，某某指标增长了 15%[1][3]。",
    ]
    if history_block:
        prompt_parts.append("")
        prompt_parts.append(history_block)
    prompt_parts.extend(
        [
            "",
            evidence_context,
            "",
            f"用户问题：{question}",
            "",
            "请基于以上参考资料给出准确、完整的回答，并在事实句后标注引用编号 [n]。",
        ]
    )
    return "\n".join(prompt_parts)
