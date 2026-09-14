"""Rest Tool Adapter。

承接 ``ToolDispatcher`` 原 ``_execute_rest`` 逻辑，复用 ``get_rest_tool()``。
"""
from __future__ import annotations

from agent.langgraph.tools.base import BaseTool
from agent.langgraph.tools.contract import ToolContext, ToolOutcome
from agent.langgraph.tools.registry import register_tool


@register_tool("rest")
class RestToolExecutor(BaseTool):
    """REST/ERP 调用 Adapter。"""

    name = "rest"

    async def _do_execute(
        self, input_data: dict, *, ctx: ToolContext
    ) -> ToolOutcome:
        from agent.langgraph.tools.rest_tool import get_rest_tool

        step = input_data["step"]
        state = input_data["state"]

        rest_tool = get_rest_tool()

        # 步骤参数优先，state 兜底
        query = step.args.query or state.get("user_question", "")
        function_name = step.args.function or ""
        function_params = step.args.function_params or {}
        rest_endpoint = step.args.rest_endpoint or ""
        rest_method = step.args.rest_method or ""
        rest_payload = step.args.rest_payload or {}
        erp_domain = step.args.erp_domain or ""

        # 如果 step.args 没有指定 function，尝试从 route_decision.metadata 获取
        if not function_name:
            route_decision = state.get("route_decision")
            if route_decision and hasattr(route_decision, "metadata"):
                metadata = route_decision.metadata or {}
                function_name = metadata.get("function", "")
                function_params = metadata.get("function_params", function_params)
                rest_endpoint = metadata.get("rest_endpoint", rest_endpoint)
                rest_method = metadata.get("rest_method", rest_method)
                rest_payload = metadata.get("rest_payload", rest_payload)
                erp_domain = metadata.get("erp_domain", erp_domain)

        tool_input = {
            "query": query,
            "function": function_name,
            "function_params": function_params,
            "rest_endpoint": rest_endpoint,
            "rest_method": rest_method,
            "rest_payload": rest_payload,
            "erp_domain": erp_domain,
            "tenant_id": state.get("tenant_id", ""),
            "kb_ids": state.get("kb_ids", []),
        }

        result = await rest_tool.invoke(tool_input)

        return ToolOutcome(
            success=True,
            tool=self.name,
            payload={
                "rest_result": result,
                "rest_quality_score": result.get("quality_score", 0.0),
                "rest_endpoint": result.get("endpoint", rest_endpoint),
                "rest_method": result.get("method", rest_method),
                "erp_domain": result.get("erp_domain", erp_domain),
            },
        )