"""意图路由数据模型定义。

定义三层路由架构的统一输出结构和执行计划模型。
"""

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class RouteDecision(BaseModel):
    """路由决策统一输出结构。

    无论哪一层决策，路由输出统一为此对象。

    Attributes:
        target: 路由目标（rag/database/web/hybrid/chitchat）
        confidence: 置信度（0.0-1.0）
        source: 决策来源（rule/llm/planner/prefilter/react_planner）
        reason: 决策理由
        complexity: 复杂度（simple/moderate/complex）
        metadata: 附加信息（entities、sub_intents、plan 等）
    """

    target: Literal["rag", "database", "web", "hybrid", "chitchat"]
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["rule", "llm", "planner", "prefilter", "react_planner"]
    reason: str = ""
    complexity: Literal["simple", "moderate", "complex"] = "simple"
    metadata: dict[str, Any] = Field(default_factory=dict)


class StepArgs(BaseModel):
    """步骤参数（规范化，支持指定工具实例）。

    Attributes:
        query: 该步骤的查询语句（可能与原始问题不同）
        query_simplified: 简化后的查询
        kb_ids: 指定知识库（RAG 专用）
        db_id: 指定数据库实例（如 hr_system / supply_chain）
        mcp_server_name: MCP 服务名（Database 专用）
        search_engine: 搜索引擎（Web 专用）
        extra: 额外参数
    """

    query: str = ""
    query_simplified: str = ""
    kb_ids: list[str] = Field(default_factory=list)
    db_id: str = ""
    mcp_server_name: str = ""
    search_engine: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StepArgs":
        """从 dict 构造 StepArgs，容忍 None 或空值。

        Args:
            data: 原始参数字典

        Returns:
            StepArgs: 规范化后的参数对象
        """
        if not data:
            return cls()
        # 过滤掉不属于 StepArgs 的字段，避免 Pydantic 验证警告
        known_fields = {
            "query", "query_simplified", "kb_ids", "db_id",
            "mcp_server_name", "search_engine", "extra"
        }
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


class PlanStep(BaseModel):
    """执行计划步骤。

    Attributes:
        step_id: 步骤唯一标识
        tool: 工具名称（rag/database/web）
        args: 工具参数（结构化）
        depends_on: 依赖的前置步骤 ID 列表
        can_parallel: 是否可并行执行
        description: 步骤描述（便于调试和日志）
    """

    step_id: str
    tool: Literal["rag", "database", "web", "report"]
    args: StepArgs = Field(default_factory=StepArgs)
    depends_on: list[str] = Field(default_factory=list)
    can_parallel: bool = False
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanStep":
        """从 dict 构造 PlanStep，自动将 args dict 转为 StepArgs。

        Args:
            data: 原始步骤字典

        Returns:
            PlanStep: 规范化后的步骤对象
        """
        step_args = StepArgs.from_dict(data.get("args", {}))
        return cls(
            step_id=data.get("step_id", ""),
            tool=data.get("tool", "rag"),
            args=step_args,
            depends_on=data.get("depends_on", []),
            can_parallel=data.get("can_parallel", False),
            description=data.get("description", ""),
        )


class ExecutionPlan(BaseModel):
    """执行计划（DAG 结构）。

    Attributes:
        plan_id: 计划唯一标识
        steps: 步骤列表
        estimated_tokens: 预估 Token 消耗
        fallback_strategy: 降级方案
    """

    plan_id: str
    steps: list[PlanStep] = Field(default_factory=list)
    estimated_tokens: int = 0
    fallback_strategy: str = "default"


class ClarificationRequest(BaseModel):
    """用户澄清请求。

    当路由置信度 < 0.6 时，需要向用户澄清。

    Attributes:
        question: 澄清问题
        options: 可选的路由目标列表
    """

    question: str
    options: list[Literal["rag", "database", "web", "hybrid", "chitchat"]] = Field(
        default_factory=list
    )
