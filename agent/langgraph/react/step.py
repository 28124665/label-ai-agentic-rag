#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
"""ReAct Step — 单步推理（LLM JSON 输出协议）。

核心职责：
1. 构造 ReAct Prompt（基于当前 ReactState + Evidence 历史）
2. 调用 LLM 解析 JSON 输出
3. 容错：JSON 解析失败时尝试修复，失败则降级为 finish

参考 docs §5.4（ReAct Step 输出协议）
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from agent.langgraph.react.models import ReactAction, ReactState

logger = logging.getLogger(__name__)


def _render_prompt(
    goal: str,
    state: ReactState,
) -> str:
    """渲染 ReAct Step Prompt（极简版，不依赖 jinja）。

    生产环境建议使用 jinja2 模板（与现有 reflect.md 一致），
    此处为减少依赖直接做字符串格式化。
    """
    budget = state.get("budget", {}) or {}
    allowed_tools = state.get("allowed_tools", [])
    action_history = state.get("action_history", [])
    evidence = state.get("evidence", [])

    lines: list[str] = []
    lines.append("# ReAct Step")
    lines.append("")
    lines.append(f"**Goal**: {goal}")
    lines.append("")
    lines.append("**Progress**:")
    lines.append(f"- Step: {budget.get('step_count', 0)} / {budget.get('max_steps', 8)}")
    lines.append(f"- Tool calls: {budget.get('tool_call_count', 0)} / {budget.get('max_tool_calls', 6)}")
    lines.append(f"- DB queries: {budget.get('db_query_count', 0)} / {budget.get('max_db_queries', 3)}")
    lines.append("")
    lines.append(f"**Allowed Tools**: {', '.join(allowed_tools) if allowed_tools else '(none)'}")
    lines.append("")
    lines.append("**Action History**:")
    if action_history:
        for i, a in enumerate(action_history, 1):
            summary = a.get("thought_summary") or a.get("purpose") or ""
            action_type = a.get("action_type") or a.get("type", "")
            result_summary = a.get("result_summary", "")
            lines.append(f"- Step {i}: {summary[:150]}")
            lines.append(f"  → Action: {action_type}")
            if result_summary:
                lines.append(f"  → Result: {result_summary[:300]}")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("**Available Evidence**:")
    if evidence:
        for i, ev in enumerate(evidence, 1):
            lines.append(f"- Evidence {i} ({ev.get('source_type', 'unknown')}): {ev.get('title', '')}")
            lines.append(f"  Content: {ev.get('content', '')[:200]}")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("# Task")
    lines.append("")
    lines.append("Decide the next action. Output a single JSON object:")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append('  "thought_summary": "<1-2 sentence summary>",')
    lines.append('  "action": {')
    lines.append('    "type": "rag_search" | "db_query" | "web_search" | "ask_clarification" | "finish",')
    lines.append('    "tool_name": "<same as type>",')
    lines.append('    "purpose": "<natural language description>",')
    lines.append('    "arguments": { ... }')
    lines.append("  },")
    lines.append('  "stop": true | false')
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("# Rules")
    lines.append("1. JSON Only. No preamble, no explanation, no markdown.")
    lines.append("2. Set `stop: true` and `action.type: \"finish\"` when evidence is sufficient.")
    lines.append("3. For db_query, only SELECT statements; must include LIMIT.")
    lines.append("4. thought_summary max 200 chars.")
    lines.append("")
    lines.append("Output:")
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict]:
    """从 LLM 输出中提取 JSON 对象。

    尝试策略：
    1. 直接解析
    2. 提取 ```json ... ``` 代码块
    3. 提取第一个 { ... } 平衡块
    """
    if not text:
        return None

    text = text.strip()

    # 1. 直接解析
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. ```json ... ```
    json_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_block:
        try:
            return json.loads(json_block.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. 任意位置的 { ... } 平衡块
    # 用 regex 找所有可能的 JSON 起点
    starts = [i for i, c in enumerate(text) if c == "{"]
    for start in starts:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        # 该起点不是有效 JSON，尝试下一个
                        break
    return None


def _coerce_to_action(parsed: dict) -> Optional[ReactAction]:
    """将解析出的 dict 规范化为 ReactAction。

    容忍 LLM 输出格式略有偏差（type 字段缺失、字段名错位等）。
    """
    if not isinstance(parsed, dict):
        return None

    # 先尝试从标准结构取
    action_section = parsed.get("action")
    if not isinstance(action_section, dict):
        action_section = {}

    # 平铺格式时，从顶层取
    action_type = (
        action_section.get("type")
        or parsed.get("type")
        or parsed.get("action_type")
        or parsed.get("tool_name")
        or ""
    )
    if isinstance(action_type, str):
        action_type = action_type.strip()
    else:
        action_type = ""

    if not action_type:
        return None

    thought_summary = (
        parsed.get("thought_summary")
        or parsed.get("thought")
        or parsed.get("reasoning")
        or ""
    )
    if isinstance(thought_summary, str) and len(thought_summary) > 200:
        thought_summary = thought_summary[:200]

    # purpose 优先从 action 取，平铺时从顶层取
    purpose = (
        action_section.get("purpose")
        or parsed.get("purpose")
        or ""
    )

    # arguments 优先从 action 取
    arguments = action_section.get("arguments") or parsed.get("arguments")
    if arguments is None:
        # 兼容：arguments 字段平铺在顶层
        if action_section:
            arguments = {
                k: v for k, v in action_section.items() if k not in ("type", "tool_name", "purpose")
            }
        else:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {"value": arguments}

    return ReactAction(
        type=action_type,
        tool_name=action_section.get("tool_name", action_type) if action_section else action_type,
        purpose=str(purpose),
        arguments=arguments,
        thought_summary=str(thought_summary),
    )


def _finish_action(summary: str = "", answer_hint: str = "") -> ReactAction:
    """构造 finish action。"""
    return ReactAction(
        type="finish",
        tool_name="finish",
        purpose=summary or "结束 ReAct",
        arguments={"summary": summary, "answer_hint": answer_hint},
        thought_summary=summary[:200] if summary else "Finish",
    )


def parse_llm_output(llm_output: str) -> ReactAction:
    """解析 LLM 输出为 ReactAction。

    解析失败时返回 finish action（视为 ReAct 自然终止，避免死循环）。

    Args:
        llm_output: LLM 原始输出

    Returns:
        ReactAction: 解析后的 action
    """
    parsed = _extract_json(llm_output)
    if parsed is None:
        logger.warning(f"[react_step] LLM 输出无法解析为 JSON，降级为 finish: {llm_output[:200]}")
        return _finish_action(summary="LLM 输出解析失败，强制结束")

    action = _coerce_to_action(parsed)
    if action is None:
        logger.warning(f"[react_step] JSON 解析成功但 action 字段缺失: {parsed}")
        return _finish_action(summary="Action 字段缺失，强制结束")

    return action


async def reason_step(
    goal: str,
    state: ReactState,
    llm_callable,
) -> ReactAction:
    """执行一次 ReAct 推理步骤。

    Args:
        goal: 目标（用户原始问题或澄清后的问题）
        state: 当前 ReactState
        llm_callable: async 异步 LLM 调用函数，签名 async (prompt: str) -> str

    Returns:
        ReactAction: 解析后的 action

    Raises:
        RuntimeError: LLM 调用失败时抛出（由调用方决定如何处理）
    """
    prompt = _render_prompt(goal, state)

    # LLM 调用异常向上传播（由 graph.py 决定如何处理）
    llm_output = await llm_callable(prompt)

    return parse_llm_output(llm_output)
