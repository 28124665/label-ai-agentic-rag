"""工具统一契约（第一步新增，暂不接业务）。

建立工具调用的统一契约：
- ``Tool`` Protocol：上层只依赖此抽象，不依赖具体工具类；
- ``ToolContext``：从 AgentState 提取的请求级上下文；
- ``ToolOutcome``：统一返回对象，公共字段 + payload 承载类型特有数据。

本步不改任何现有工具、dispatcher 分支或消费方，新契约先「存在」，
供第二步（注册表）与第三步（模板方法）接入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolContext:
    """从 AgentState 提取的请求级上下文，供各工具统一取参。

    由 dispatcher 填充，避免每个工具各自从 state 里挖字段。
    """

    tenant_id: str = ""
    kb_ids: list[str] = field(default_factory=list)
    llm_id: str = ""
    query_lang: str = "zh_CN"
    agent_config: dict = field(default_factory=dict)


@dataclass
class ToolOutcome:
    """工具统一返回对象（新契约）。

    - 公共字段：质量评估 / 证据归一化 / 反思统一消费；
    - payload：承载类型特有数据，公共层不感知。

    过渡期兼容：Adapter 层把原 dispatcher 返回的 ``xxx_*`` 平铺字段整体放入
    ``payload``，``to_state_fields()`` 再反向展平，保证消费方零改动。
    """

    success: bool = True
    tool: str = ""
    error_code: str = ""
    error: str = ""
    latency_ms: int = 0

    # 公共语义字段（面向未来统一消费，当前 Adapter 仍以 payload 为主）
    docs: list[dict] = field(default_factory=list)
    quality_score: float = 0.0
    has_relevant: bool = False
    relevant_count: int = 0

    # 类型特有数据（如 rag_docs / db_result / rest_result / report_artifacts 等平铺字段）
    payload: dict = field(default_factory=dict)

    def to_state_fields(self) -> dict:
        """过渡期适配器：展平回旧的平铺字段（兼容 ToolResult 消费方）。

        返回公共字段 + payload 平铺字段；``step_id`` 由 dispatch 追加。
        """
        out = {
            "success": self.success,
            "tool": self.tool,
            "latency_ms": self.latency_ms,
        }
        # 失败时附带错误信息；成功路径不含空错误字段，保持与旧返回结构一致。
        if self.error:
            out["error"] = self.error
        if self.error_code:
            out["error_code"] = self.error_code
        out.update(self.payload)
        return out


@runtime_checkable
class Tool(Protocol):
    """工具统一入口协议。

    上层只依赖此抽象，不依赖 RAGTool / DatabaseTool 等具体类。
    """

    name: str

    async def invoke(self, input_data: dict, *, ctx: ToolContext) -> ToolOutcome: ...