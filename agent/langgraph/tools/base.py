"""工具生命周期基类（第三步，模板方法模式）。

把 dispatcher 里在 5 个工具中重复出现的「计时 + 校验 + 执行 + 兜底 + 公共字段回填」
下沉到基类 ``invoke`` 固定骨架中，子类只实现 ``_do_execute``（可变步骤），
按需覆盖 ``_validate`` / ``_post_process`` 两个钩子。

- ``_validate``：执行前校验（默认空实现）；
- ``_do_execute``：唯一抽象方法，各工具的核心逻辑；
- ``_post_process``：执行后统一后处理（默认透传）。

说明：业务失败通过 ``_do_execute`` 返回 ``success=False`` 的 ``ToolOutcome`` 表达，
基类不强制覆盖 ``success``；外部系统异常由 ``except`` 分支统一兜底为
``TOOL_EXECUTION_FAILED``（``ToolUnavailable`` 预留降级语义）。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from agent.langgraph.tools.contract import ToolContext, ToolOutcome


class ToolUnavailable(Exception):
    """外部系统不可用（应走降级而非重试）。

    当前 Adapter 尚无工具主动抛出，预留该语义（方案 §5.3 / §6）。
    """


class BaseTool(ABC):
    """工具生命周期模板基类。

    子类必须设置 ``name`` 并实现 ``_do_execute``。
    """

    name: str = ""

    async def invoke(self, input_data: dict, *, ctx: ToolContext) -> ToolOutcome:
        """固定执行骨架（模板方法）。

        时序：计时 → 校验 → 执行 → 后处理 → 异常兜底 → 统一回填 name/latency。
        """
        started = time.perf_counter()
        try:
            self._validate(input_data)  # 钩子1
            outcome = await self._do_execute(input_data, ctx)  # 抽象：可变步骤
            outcome = self._post_process(outcome)  # 钩子2
        except ToolUnavailable as e:  # 外部系统不可用 → 降级
            outcome = ToolOutcome(
                success=False, error_code="TOOL_UNAVAILABLE", error=str(e)
            )
        except Exception as e:  # 统一兜底
            outcome = ToolOutcome(
                success=False, error_code="TOOL_EXECUTION_FAILED", error=str(e)
            )
        outcome.tool = self.name
        outcome.latency_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    @abstractmethod
    async def _do_execute(
        self, input_data: dict, *, ctx: ToolContext
    ) -> ToolOutcome:
        """各工具唯一必须实现的方法（可变步骤）。"""

    def _validate(self, input_data: dict) -> None:
        """默认空实现，子类按需覆盖。"""

    def _post_process(self, outcome: ToolOutcome) -> ToolOutcome:
        """默认透传，子类按需覆盖（如统一质量评估）。"""
        return outcome