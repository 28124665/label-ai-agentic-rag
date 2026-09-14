#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""SQL Agent Prompt 构建与 LLM 输出解析（JSON action 协议）。

与 react/step.py 同构：LLM 每步必须输出 JSON，不得输出自由文本动作。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from agent.langgraph.tools.sql_agent.state import (
    ACTION_FINISH,
    ACTION_GIVE_UP,
    ACTION_LIST_DATABASES,
    TOOL_ACTIONS,
)
from agent.langgraph.tools.sql_agent.tools import TOOL_DESCRIPTIONS

logger = logging.getLogger(__name__)

# 单步历史在 Prompt 中的最大长度（防爆上下文）
MAX_HISTORY_ITEM_CHARS = 800
# 最多携带的历史步数
MAX_HISTORY_STEPS = 8


def build_system_prompt(
    query: str,
    query_lang: str = "zh_CN",
    db_id: str = "",
    exploration_hints: Optional[dict[str, Any]] = None,
) -> str:
    """构建 SQL Agent System Prompt。

    Args:
        query: 用户问题（简体化后）
        query_lang: 查询语言
        db_id: 调用方已指定的库（非空时跳过选库步骤）
        exploration_hints: DataSkill 注入的业务域提示
            （preferred_tables / metric_bindings / business_glossary）
    """
    lang_instruction = {
        "zh_TW": "使用者的問題為繁體中文，請用繁體中文思考與總結。",
        "en": "The user question is in English. Think and summarize in English.",
    }.get(query_lang, "用户问题为简体中文，请用简体中文思考与总结。")

    db_directive = (
        f'目标数据库已指定为 "{db_id}"，请直接使用它，跳过 list_databases。'
        if db_id
        else "目标数据库未指定，请先调用 list_databases 并根据业务描述选择最合适的库。"
    )

    hints_section = _render_exploration_hints(exploration_hints)

    return f"""你是企业数据查询专家，通过调用工具逐步探查数据库并回答数据问题。{lang_instruction}

{TOOL_DESCRIPTIONS}

工作规则：
1. 不要猜测表名和字段名：先 list_tables 看清单，再 describe_table 看结构，最后才写 SQL
2. {db_directive}
3. 只生成只读 SELECT 查询（系统会自动追加 LIMIT）
3a. 字段选择原则（重要）：
    - 只 SELECT 回答用户问题所必需的字段，严禁 SELECT *
    - describe_table 后，先判断哪些列与问题相关，再写 SQL
    - 优先选择：数值列（用于聚合计算）、分类列（GROUP BY）、标识列（id/名称）、时间列
    - 避免选择：大文本列（description/note/content）、与问题无关的外键列、冗余状态列
    - 不确定某列是否必需时，宁可多选一列，不要漏掉关键列
4. execute_sql 报错时，分析错误信息；涉及表/字段不存在时，先 describe_table 确认再修正重试
5. 查询结果为空时：先检查过滤条件是否过严（时间范围、精确匹配），可放宽后重试一次；
   确认库中确实无相关数据后，用 give_up（reason_type=no_data）明确说明
6. SQL 持续失败且无法修正时，用 give_up（reason_type=error）并说明原因
7. 得到足以回答问题的数据后，立即用 finish 结束，不要过度探索
8. 每一步只输出一个 JSON action，不要输出其他内容
{hints_section}
输出格式（严格遵守）：
{{"thought": "本步推理（一句话）", "action": "<动作名>", "args": {{...}}}}"""


def _render_exploration_hints(hints: Optional[dict[str, Any]]) -> str:
    """渲染 DataSkill exploration_hints 为 Prompt 片段。"""
    if not hints:
        return ""

    sections: list[str] = ["", "业务域提示（来自受治理的 DataSkill 配置）："]
    preferred_tables = hints.get("preferred_tables")
    if preferred_tables:
        sections.append(f"- 优先考虑的表：{', '.join(str(t) for t in preferred_tables)}")

    metric_bindings = hints.get("metric_bindings")
    if metric_bindings:
        rendered = json.dumps(metric_bindings, ensure_ascii=False)
        sections.append(f"- 指标口径：{rendered}")

    table_aliases = hints.get("table_aliases")
    if table_aliases:
        rendered = json.dumps(table_aliases, ensure_ascii=False)
        sections.append(f"- 表别名对照：{rendered}")

    business_glossary = hints.get("business_glossary")
    if business_glossary:
        rendered = json.dumps(business_glossary, ensure_ascii=False)
        sections.append(f"- 业务术语：{rendered}")

    return "\n".join(sections) if len(sections) > 1 else ""


def build_step_prompt(
    system_prompt: str,
    query: str,
    step_trace: list[dict[str, Any]],
) -> str:
    """构建单步推理 Prompt（System + 用户问题 + 截断的历史轨迹）。"""
    if not step_trace:
        history_text = "（尚无历史步骤，这是第一步）"
    else:
        recent = step_trace[-MAX_HISTORY_STEPS:]
        lines: list[str] = []
        for entry in recent:
            observation = str(entry.get("observation", ""))[:MAX_HISTORY_ITEM_CHARS]
            lines.append(
                f"步骤 {entry.get('step', '?')}:\n"
                f"  推理: {entry.get('thought', '')}\n"
                f"  动作: {entry.get('action', '')} {json.dumps(entry.get('args', {}), ensure_ascii=False)}\n"
                f"  观察: {observation}"
            )
        if len(step_trace) > MAX_HISTORY_STEPS:
            lines.insert(0, f"（省略前 {len(step_trace) - MAX_HISTORY_STEPS} 步）")
        history_text = "\n\n".join(lines)

    return f"""{system_prompt}

用户问题：{query}

历史步骤与观察：
{history_text}

请输出下一步的 JSON action："""


def parse_agent_action(llm_output: str) -> Optional[dict[str, Any]]:
    """解析 LLM 输出为 Agent action。

    Returns:
        dict: {"thought": str, "action": str, "args": dict}，解析失败返回 None
    """
    parsed = _extract_json(llm_output)
    if parsed is None:
        return None

    action_type = parsed.get("action", "")
    if not isinstance(action_type, str) or not action_type:
        return None

    # 规范化：finish/give_up 与工具动作之外的一律拒绝
    valid_actions = TOOL_ACTIONS | {ACTION_FINISH, ACTION_GIVE_UP}
    if action_type not in valid_actions:
        # 容忍常见别名
        alias_map = {
            "list_database": ACTION_LIST_DATABASES,
            "list_dbs": ACTION_LIST_DATABASES,
            "show_tables": "list_tables",
            "describe": "describe_table",
            "query": "execute_sql",
            "run_sql": "execute_sql",
            "sql": "execute_sql",
            "done": ACTION_FINISH,
            "stop": ACTION_FINISH,
        }
        action_type = alias_map.get(action_type, "")
        if action_type not in valid_actions:
            return None

    args = parsed.get("args") or parsed.get("arguments") or {}
    if not isinstance(args, dict):
        args = {}

    return {
        "thought": str(parsed.get("thought") or parsed.get("thought_summary") or ""),
        "action": action_type,
        "args": args,
    }


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    """从 LLM 输出中提取 JSON 对象（容忍 markdown 包裹与前后杂文本）。"""
    if not text:
        return None

    # 1. 尝试 ```json ... ``` 块
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. 括号配平扫描：找到第一个完整 {...} 对象
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, TypeError):
                        break
        start = text.find("{", start + 1)

    return None
