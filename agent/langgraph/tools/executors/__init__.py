"""工具 Adapter 层（第三步新增）。

每个 Adapter 继承 ``BaseTool``，把 ``ToolDispatcher`` 原 ``_execute_*`` 逻辑迁入
``_do_execute``，并通过 ``@register_tool`` 自注册进 ``ToolRegistry``。
底层工具类（RAGTool / DatabaseTool / WebTool / RestTool / ReportTool）零改动。

导入本包即触发所有 Adapter 注册（``@register_tool`` 装饰器自注册）。
"""
from __future__ import annotations

# isort: off  （按注册顺序导入，触发自注册；顺序不影响功能）
from agent.langgraph.tools.executors import (  # noqa: F401
    database,
    rag,
    report,
    rest,
    web,
)

__all__ = [
    "DatabaseToolExecutor",
    "RAGToolExecutor",
    "ReportToolExecutor",
    "RestToolExecutor",
    "WebToolExecutor",
]