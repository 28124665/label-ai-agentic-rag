"""SSE 事件翻译层。

将 LangGraph 节点输出（node_name, node_output）翻译为前端可消费的 SSE 事件。
本模块是纯函数映射，无业务逻辑、无状态，不依赖 agent/langgraph 内部实现，
保持 agent 层对 HTTP/SSE 的无感知。

SSE 事件类型：
  - thinking:   思考进度提示
  - tool_call:  工具调用开始/完成
  - tool_result: 工具返回结果
  - reference:  引用来源
  - text:       最终文本答案
  - done:       完成事件（含 message_id）
  - error:      错误事件
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def translate_node_output(
    node_name: str,
    node_output: dict[str, Any],
) -> list[dict[str, Any]]:
    """将单个 LangGraph 节点输出映射为零或多个 SSE 事件字典。

    Args:
        node_name: LangGraph 节点标识（与 graph.py 中 add_node 一致）
        node_output: 节点返回的部分状态更新字典

    Returns:
        list[dict]: SSE 事件列表，每个事件含 ``type`` 及其他字段
    """
    handler = _NODE_HANDLERS.get(node_name)
    if handler is None:
        return []
    return handler(node_output)


def format_sse(event: dict[str, Any]) -> str:
    """将事件字典格式化为 SSE 文本帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ========== 节点处理器 ==========


def _on_question_input(_output: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "thinking", "content": "正在分析问题..."}]


def _on_intent_router(output: dict[str, Any]) -> list[dict[str, Any]]:
    route = output.get("route_target", "chitchat")
    label = _ROUTE_LABELS.get(route, route)
    return [{"type": "thinking", "content": f"选择工具: {label}"}]


def _on_rag_tool(output: dict[str, Any]) -> list[dict[str, Any]]:
    docs = output.get("rag_docs", [])
    events: list[dict[str, Any]] = [
        {"type": "tool_call", "tool": "rag", "status": "completed"},
        {
            "type": "tool_result",
            "tool": "rag",
            "documents": docs,
            "status": "completed",
        },
    ]
    if docs:
        events.append(
            {
                "type": "reference",
                "references": [
                    {
                        "type": "document",
                        "title": d.get("source", ""),
                        "content": (d.get("content") or "")[:200],
                        "score": d.get("score", 0.0),
                    }
                    for d in docs
                ],
            }
        )
    return events


def _on_db_tool(output: dict[str, Any]) -> list[dict[str, Any]]:
    db_result = output.get("db_result", {}) or {}
    sql = db_result.get("sql", "")
    rows = db_result.get("rows", [])
    row_count = db_result.get("row_count", 0)
    events: list[dict[str, Any]] = [
        {"type": "tool_call", "tool": "database", "status": "completed"},
        {
            "type": "tool_result",
            "tool": "database",
            "sql": sql,
            "rows": rows,
            "row_count": row_count,
            "status": "completed",
        },
    ]
    if sql:
        events.append(
            {
                "type": "reference",
                "references": [
                    {
                        "type": "database",
                        "title": f"SQL: {sql[:50]}",
                        "content": f"查询返回 {row_count} 条结果",
                        "source": sql,
                    }
                ],
            }
        )
    return events


def _on_web_tool(output: dict[str, Any]) -> list[dict[str, Any]]:
    docs = output.get("web_docs", [])
    events: list[dict[str, Any]] = [
        {"type": "tool_call", "tool": "web", "status": "completed"},
        {
            "type": "tool_result",
            "tool": "web",
            "results": docs,
            "status": "completed",
        },
    ]
    if docs:
        events.append(
            {
                "type": "reference",
                "references": [
                    {
                        "type": "web",
                        "title": d.get("title", ""),
                        "content": (d.get("content") or "")[:200],
                        "source": d.get("url", ""),
                    }
                    for d in docs
                ],
            }
        )
    return events


def _on_hallucination(output: dict[str, Any]) -> list[dict[str, Any]]:
    score = output.get("hallucination_score", 0.0)
    action = output.get("hallucination_action", "pass")
    return [{"type": "thinking", "content": f"幻觉检测: score={score:.2f}, action={action}"}]


def _on_answer_output(output: dict[str, Any]) -> list[dict[str, Any]]:
    final_answer = output.get("final_answer", "")
    return [{"type": "text", "content": final_answer}]


# ========== 路由标签 ==========

_ROUTE_LABELS = {
    "rag": "知识库检索",
    "database": "数据库查询",
    "hybrid": "混合检索",
    "web": "网络搜索",
    "chitchat": "智能对话",
}


# ========== 节点 → 处理器映射 ==========

_NODE_HANDLERS = {
    "question_input": _on_question_input,
    "intent_router": _on_intent_router,
    "rag_tool": _on_rag_tool,
    "db_tool": _on_db_tool,
    "web_tool": _on_web_tool,
    "hallucination": _on_hallucination,
    "answer_output": _on_answer_output,
}
