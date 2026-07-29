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
"""Policy Guard — ReAct 子图工具调用前置策略校验。

所有 ReAct Action 在执行前必须经过 Policy Guard。

设计原则（docs/受限ReAct子图落地设计.md §6.1）：
- decision 只允许 5 种：allow / deny / needs_approval / needs_clarification / rewrite_arguments
- 白名单校验：tool_name 必须在 allowed_tools 中
- DB 工具 readonly + AST 校验
- 高风险操作（needs_approval）目前仅记录，由后续审批流处理

本模块专注于"决策"本身，不负责工具实际执行（由 executor.py 负责）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PolicyDecisionType(str, Enum):
    """Policy Guard 决策类型。"""

    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_CLARIFICATION = "needs_clarification"
    REWRITE_ARGUMENTS = "rewrite_arguments"


# 高风险操作关键词（触发 needs_approval）
HIGH_RISK_KEYWORDS = [
    "drop",
    "delete",
    "truncate",
    "update",
    "insert",
    "alter",
    "create",
    "grant",
    "revoke",
    "--",
    "/*",
    "xp_",
    "exec",
]


@dataclass
class PolicyContext:
    """Policy Guard 输入上下文。

    Attributes:
        tenant_id: 租户 ID
        user_id: 用户 ID
        tool_name: 待执行工具名（rag_search / db_query / web_search / finish / ask_clarification）
        action_type: action 类型（与 tool_name 同义）
        purpose: 调用目的
        arguments: 工具参数
        allowed_tools: 工具白名单
        agent_config: 完整 agent 配置（含工具权限细节）
    """

    tenant_id: str = ""
    user_id: str = ""
    tool_name: str = ""
    action_type: str = ""
    purpose: str = ""
    arguments: dict = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    agent_config: dict = field(default_factory=dict)


@dataclass
class PolicyResult:
    """Policy Guard 输出结果。

    Attributes:
        decision: 决策类型
        reason: 决策理由（用于日志/审计）
        sanitized_arguments: 清洗/重写后的参数
        requires_approval: 是否需要人工审批
        risk_level: low / medium / high
    """

    decision: PolicyDecisionType
    reason: str = ""
    sanitized_arguments: dict = field(default_factory=dict)
    requires_approval: bool = False
    risk_level: str = "low"


# ========== 默认白名单 ==========
# 三个核心工具 + finish + ask_clarification 始终允许
DEFAULT_ALLOWED_TOOLS = [
    "rag_search",
    "db_query",
    "web_search",
    "finish",
    "ask_clarification",
]


class PolicyGuard:
    """Policy Guard — ReAct 子图工具调用前置校验。

    校验流程：
    1. 基础检查：action_type 是否合法
    2. 白名单检查：tool_name 是否在 allowed_tools 中
    3. 工具类型专项检查：
       - db_query: 危险 SQL 关键词检测、必填字段检查
       - web_search: URL 注入检查
       - rag_search: 知识库 ID 校验
    4. 风险评估：高风险操作标记为 needs_approval
    """

    def __init__(self, agent_config: Optional[dict] = None):
        """初始化 Policy Guard。

        Args:
            agent_config: 完整 agent 配置（用于读取工具级权限）
        """
        self.agent_config = agent_config or {}
        # 解析白名单
        react_cfg = self.agent_config.get("react", {}) or {}
        self.allowed_tools = react_cfg.get("allowed_tools") or DEFAULT_ALLOWED_TOOLS
        # 工具级配置（如 db_query readonly）
        self.tool_policies = react_cfg.get("policy", {}) or {}

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        """评估 action 是否允许执行。

        Args:
            context: 待评估的 action 上下文

        Returns:
            PolicyResult: 决策结果
        """
        action_type = context.action_type or context.tool_name

        # 1. finish / ask_clarification 始终允许
        if action_type in ("finish", "ask_clarification"):
            return PolicyResult(
                decision=PolicyDecisionType.ALLOW,
                reason=f"{action_type} 始终允许",
                sanitized_arguments=context.arguments or {},
            )

        # 2. 白名单检查
        if action_type not in self.allowed_tools:
            return PolicyResult(
                decision=PolicyDecisionType.DENY,
                reason=f"工具 {action_type} 不在白名单 {self.allowed_tools} 中",
                risk_level="medium",
            )

        # 3. 工具类型专项检查
        if action_type == "db_query":
            return self._evaluate_db_query(context)
        if action_type == "web_search":
            return self._evaluate_web_search(context)
        if action_type == "rag_search":
            return self._evaluate_rag_search(context)

        # 其他工具暂直接放行（保留扩展点）
        return PolicyResult(
            decision=PolicyDecisionType.ALLOW,
            reason=f"工具 {action_type} 通过基础校验",
            sanitized_arguments=context.arguments or {},
        )

    def _evaluate_db_query(self, context: PolicyContext) -> PolicyResult:
        """DB 工具策略评估。"""
        args = context.arguments or {}
        sql = (args.get("sql") or args.get("query") or "").strip()
        db_id = args.get("db_id", "")

        # 危险操作检查（即使 readonly 也要拦截）
        sql_lower = sql.lower()
        for kw in HIGH_RISK_KEYWORDS:
            if kw in sql_lower:
                return PolicyResult(
                    decision=PolicyDecisionType.DENY,
                    reason=f"SQL 包含高风险关键词 '{kw}'，拒绝执行",
                    risk_level="high",
                )

        # 工具级策略检查
        db_policy = self.tool_policies.get("db_query", {}) or {}
        if db_policy.get("readonly", True):
            # readonly 模式下，再做一次关键词检查
            write_keywords = ["insert", "update", "delete", "drop", "alter", "create", "truncate", "replace"]
            for kw in write_keywords:
                # 仅匹配单词边界，避免 like 'update' 这种误判
                if re.search(rf"\b{kw}\b", sql_lower):
                    return PolicyResult(
                        decision=PolicyDecisionType.DENY,
                        reason=f"readonly 模式禁止写操作 '{kw}'",
                        risk_level="high",
                    )

        # 强制 LIMIT 检查
        if not re.search(r"\blimit\b", sql_lower) and sql_lower.startswith("select"):
            return PolicyResult(
                decision=PolicyDecisionType.NEEDS_APPROVAL,
                reason="SQL 缺少 LIMIT 子句，需要审批",
                risk_level="medium",
                requires_approval=True,
            )

        # 跨数据库查询标记
        if db_id and db_policy.get("allowed_db_ids"):
            allowed = db_policy["allowed_db_ids"]
            if isinstance(allowed, list) and allowed and db_id not in allowed:
                return PolicyResult(
                    decision=PolicyDecisionType.DENY,
                    reason=f"数据库 {db_id} 不在白名单 {allowed} 中",
                    risk_level="high",
                )

        return PolicyResult(
            decision=PolicyDecisionType.ALLOW,
            reason="DB 工具通过策略校验",
            sanitized_arguments=args,
        )

    def _evaluate_web_search(self, context: PolicyContext) -> PolicyResult:
        """Web 工具策略评估。"""
        args = context.arguments or {}
        query = (args.get("query") or "").strip()

        if not query:
            return PolicyResult(
                decision=PolicyDecisionType.DENY,
                reason="Web 搜索 query 为空",
                risk_level="low",
            )

        # URL 注入检测
        for kw in ["javascript:", "data:", "file://", "<script", "</script"]:
            if kw.lower() in query.lower():
                return PolicyResult(
                    decision=PolicyDecisionType.DENY,
                    reason=f"Web 搜索 query 包含可疑内容 '{kw}'",
                    risk_level="high",
                )

        # Web 工具级策略
        web_policy = self.tool_policies.get("web_search", {}) or {}
        if web_policy.get("allow_private_ip", False) is False:
            # 简单 IP 段检查（仅内网 IP 段）
            for ip_prefix in ["127.", "10.", "192.168.", "172.16.", "localhost"]:
                if ip_prefix in query:
                    return PolicyResult(
                        decision=PolicyDecisionType.DENY,
                        reason=f"Web 搜索禁止访问内网地址 '{ip_prefix}'",
                        risk_level="high",
                    )

        return PolicyResult(
            decision=PolicyDecisionType.ALLOW,
            reason="Web 工具通过策略校验",
            sanitized_arguments=args,
        )

    def _evaluate_rag_search(self, context: PolicyContext) -> PolicyResult:
        """RAG 工具策略评估。"""
        args = context.arguments or {}
        query = (args.get("query") or "").strip()

        if not query:
            return PolicyResult(
                decision=PolicyDecisionType.NEEDS_CLARIFICATION,
                reason="RAG 检索 query 为空，需要澄清",
                risk_level="low",
            )

        # 知识库白名单
        rag_policy = self.tool_policies.get("rag_search", {}) or {}
        kb_ids = args.get("kb_ids", [])
        if kb_ids and rag_policy.get("allowed_kb_ids"):
            allowed = rag_policy["allowed_kb_ids"]
            if isinstance(allowed, list) and allowed:
                for kb in kb_ids:
                    if kb not in allowed:
                        return PolicyResult(
                            decision=PolicyDecisionType.DENY,
                            reason=f"知识库 {kb} 不在白名单中",
                            risk_level="medium",
                        )

        return PolicyResult(
            decision=PolicyDecisionType.ALLOW,
            reason="RAG 工具通过策略校验",
            sanitized_arguments=args,
        )
