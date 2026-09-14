"""工具注册表（第二步）。

用「注册表 + 策略模式」替换 ``tool_dispatcher.dispatch`` 里的 if/elif 链：
- ``@register_tool``：装饰器自注册，新增工具只需注册一次，不改分发器；
- ``ToolRegistry.dispatch``：O(1) 查表分发，未知工具名快速失败（抛 ``ValueError``）；
- ``get_tool_registry``：进程级单例。

注册表是「可变全局表 + 不可变快照读」的极简形态，后续如需热加载可
引入 ``SkillRegistry`` 的快照原子替换机制（方案 §6）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent.langgraph.tools.contract import ToolContext, ToolOutcome

if TYPE_CHECKING:
    from agent.langgraph.tools.contract import Tool

# 全局注册表：工具名 → 工具实例（策略对象）。
# 仅在此模块内修改；外部通过 ToolRegistry 只读访问。
_REGISTRY: dict[str, "Tool"] = {}


def register_tool(name: str):
    """装饰器：将工具类实例化后注册进全局表，并返回原类（不改变类本身）。"""

    def decorator(cls):
        instance = cls()
        _REGISTRY[name] = instance
        return cls

    return decorator


class ToolRegistry:
    """工具注册表：``key → 策略实例``，O(1) 查表分发。"""

    def get(self, name: str):
        """按名取工具实例；未注册返回 None。"""
        return _REGISTRY.get(name)

    def names(self) -> list[str]:
        """返回所有已注册工具名（调试/契约校验用）。"""
        return list(_REGISTRY.keys())

    async def dispatch(
        self,
        name: str,
        input_data: dict,
        *,
        ctx: ToolContext,
    ) -> ToolOutcome:
        """按工具名分发到对应策略执行。

        Raises:
            ValueError: 工具名未注册（保留原「未知工具类型」快速失败语义）。
        """
        tool = _REGISTRY.get(name)
        if tool is None:
            raise ValueError(f"未知工具类型: {name}")
        return await tool.invoke(input_data, ctx=ctx)


# 进程级单例
_REGISTRY_SINGLETON = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    """获取工具注册表单例。"""
    return _REGISTRY_SINGLETON