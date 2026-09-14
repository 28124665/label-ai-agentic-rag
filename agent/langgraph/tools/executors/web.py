"""Web Tool Adapter。

承接 ``ToolDispatcher`` 原 ``_execute_web`` 逻辑，复用 ``get_web_tool()``。
"""
from __future__ import annotations

from agent.langgraph.tools.base import BaseTool
from agent.langgraph.tools.contract import ToolContext, ToolOutcome
from agent.langgraph.tools.registry import register_tool


@register_tool("web")
class WebToolExecutor(BaseTool):
    """Web 搜索 Adapter。"""

    name = "web"

    async def _do_execute(
        self, input_data: dict, *, ctx: ToolContext
    ) -> ToolOutcome:
        from agent.langgraph.tools.web_tool import get_web_tool

        step = input_data["step"]
        state = input_data["state"]

        web_tool = get_web_tool()

        query = step.args.query or state.get("user_question", "")

        tool_input = {
            "query": query,
            "query_lang": state.get("query_lang", "zh_CN"),
        }

        # 合并 extra 参数
        if step.args.extra:
            tool_input.update(step.args.extra)

        result = await web_tool.invoke(tool_input)

        return ToolOutcome(
            success=True,
            tool=self.name,
            payload={
                "web_docs": result.get("docs", []),
            },
        )