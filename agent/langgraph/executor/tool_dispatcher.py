"""工具分发器。

根据 PlanStep.tool 调用对应的工具实例。
M3 起不再维护 if/elif 分支，改为查询工具注册表（ToolRegistry），
由各工具 Adapter（继承 BaseTool）统一承接执行、计时与异常兜底。

核心设计：
- 步骤参数优先：step.args 中的参数覆盖 state 中的默认值
- 多实例支持：step.args.db_id 可指定不同的数据库实例
- 异常隔离：单个步骤失败不影响其他步骤
- 可扩展：新增工具在 tools/executors/ 下新增 Adapter 并 @register_tool 即可，分发器零改动
"""

import logging
import time
from typing import TYPE_CHECKING

from agent.langgraph.routers.models import PlanStep

if TYPE_CHECKING:
    from agent.langgraph.state import AgentState, ToolResult

logger = logging.getLogger(__name__)


class ToolDispatcher:
    """工具分发器。

    根据 PlanStep.tool 类型查注册表，调用对应的工具 Adapter。
    底层工具类（RAGTool / DatabaseTool / WebTool / RestTool / ReportTool）零改动。
    """

    async def dispatch(
        self,
        step: PlanStep,
        state: "AgentState",
        previous_results: dict[str, "ToolResult"],
    ) -> "ToolResult":
        """分发步骤到对应工具。

        Args:
            step: 执行步骤
            state: 全局状态
            previous_results: 前置步骤结果（可用于步骤间数据传递）

        Returns:
            ToolResult: 工具执行结果
        """
        from agent.langgraph.state import ToolResult
        from agent.langgraph.tools.contract import ToolContext
        from agent.langgraph.tools.registry import get_tool_registry

        # 触发 Adapter 自注册（保证 rag/database/web/rest/report 均已注册）
        import agent.langgraph.tools.executors as _executors  # noqa: F401

        start_time = time.time()
        step_desc = step.description or step.step_id

        ctx = ToolContext(
            tenant_id=state.get("tenant_id", ""),
            kb_ids=state.get("kb_ids", []),
            llm_id=state.get("llm_id", ""),
            query_lang=state.get("query_lang", "zh_CN"),
            agent_config=state.get("agent_config", {}) or {},
        )
        input_data = {
            "step": step,
            "state": state,
            "previous_results": previous_results,
        }

        try:
            outcome = await get_tool_registry().dispatch(
                step.tool, input_data, ctx=ctx
            )
            result = outcome.to_state_fields()
            success = bool(result.get("success", True))
        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"[ToolDispatcher] 步骤 {step.step_id} ({step.tool}, {step_desc}) "
                f"执行失败: {e} ({latency_ms}ms)"
            )
            return ToolResult(
                step_id=step.step_id,
                tool=step.tool,
                success=False,
                error=str(e),
                error_code="TOOL_EXECUTION_FAILED",
                latency_ms=latency_ms,
            )

        result["step_id"] = step.step_id
        result["tool"] = step.tool
        result["success"] = success
        result["latency_ms"] = result.get("latency_ms") or int(
            (time.time() - start_time) * 1000
        )

        log_message = (
            f"[ToolDispatcher] 步骤 {step.step_id} ({step.tool}, {step_desc}) "
            f"执行{'成功' if success else '失败'}: {result['latency_ms']}ms"
        )
        if success:
            logger.info(log_message)
        else:
            logger.warning(log_message)
        return ToolResult(**result)

    async def _execute_rag(self, step: PlanStep, state: "AgentState") -> dict:
        """RAG 薄壳委托（M3 后保留的历史入口）。

        实际逻辑已下沉到 RAGToolExecutor；此处保留原 ``_execute_rag`` 入口签名，
        委托 Adapter 执行，返回与旧实现一致的平铺 dict，供历史调用点/测试直接使用。
        """
        from agent.langgraph.tools.executors.rag import RAGToolExecutor

        executor = RAGToolExecutor()
        extra = step.args.extra or {}
        if extra.get("retrieval_skill_id"):
            return await executor._execute_skill_rag(step, state, extra)
        return await executor._execute_rag(step, state)


# 全局实例
_dispatcher_instance: ToolDispatcher | None = None


def get_tool_dispatcher() -> ToolDispatcher:
    """获取 ToolDispatcher 单例实例。

    Returns:
        ToolDispatcher: 工具分发器实例
    """
    global _dispatcher_instance
    if _dispatcher_instance is None:
        _dispatcher_instance = ToolDispatcher()
    return _dispatcher_instance