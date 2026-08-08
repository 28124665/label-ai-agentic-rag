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
"""Answer Renderer 节点（v2.1 §3.2 + §4.8.3 SSE 安全约束）。

职责：
1. ``answer_renderer_node`` —— 将结构化 Answer AST 渲染为最终文本，
   仅输出 ``final_status == "supported"`` 的 Claim（验收项 24 fail-closed）。
2. ``SSEStreamFilter`` —— SSE 流式输出过滤器，保证未验证 claim 不在 chunk 中泄露。

设计来源：
- docs/Claim级可追溯幻觉检测设计.md §3.2：新增 answer_renderer 节点（结构化答案渲染）
- docs/Claim级可追溯幻觉检测设计.md §4.8.3：SSE 流式输出安全约束（验收项 24）

类比 Java 中的视图渲染层：
    ``answer_renderer_node`` 类似 Controller 的渲染阶段，``AnswerRenderer`` 是 View，
    ``SSEStreamFilter`` 类似 ``OncePerRequestFilter`` 对流式输出做拦截/放行。
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any

from agent.langgraph.config import EnforcementMode
from agent.langgraph.evidence.answer_ast import AnswerAST, AnswerRenderer
from agent.langgraph.evidence.claim import Claim, ClaimFinalStatus
from agent.langgraph.state import AgentState

logger = logging.getLogger(__name__)

# 节点名（用作 node_timings 的 key，与 evidence_fusion 等节点保持一致风格）
_NODE_NAME = "answer_renderer"


async def answer_renderer_node(state: AgentState) -> dict[str, Any]:
    """答案渲染节点（v2.1 §3.2）。

    将结构化 Answer AST 渲染为最终 Markdown 文本，仅保留
    ``final_status == "supported"`` 的 Claim，未通过验证的事实声明一律丢弃。

    核心设计决策（状态同步）：
        ``state.claims`` 是验证后的权威状态来源（由 VerdictMatrix 计算 final_status），
        而 ``state.answer_ast`` 中的 ClaimNode.final_status 仍停留在解析时的 "pending"
        （LLM 输出解析阶段无法预知验证结果）。因此渲染前必须把 claims 的 final_status
        同步回 AST，否则 AnswerRenderer 会把所有 Claim 当作 pending 过滤掉，导致空答案。

    降级策略：
        AST 缺失或反序列化失败时降级返回 generated_answer（legacy 兼容路径）。
        ENFORCED 模式下 AST 缺失应已在 Policy Engine 阶段拒答，不会到达此节点，
        故此处降级不会破坏 fail-closed 语义。

    Args:
        state: 当前 AgentState

    Returns:
        dict: 更新的状态字段，含 ``final_answer`` 与 ``node_timings``
    """
    start_time = time.time()

    answer_ast_dict = state.get("answer_ast") or {}
    claims_raw = state.get("claims") or []
    generated_answer = state.get("generated_answer", "")

    # 兜底：AST 缺失时降级为 legacy 路径（DISABLED/灰度阶段 AST 可能尚未生成）
    root = answer_ast_dict.get("root") if isinstance(answer_ast_dict, dict) else None
    if not root:
        logger.warning(
            f"[{_NODE_NAME}] answer_ast 缺失或无 root，降级返回 generated_answer（legacy 兼容）"
        )
        return {
            "final_answer": generated_answer or "",
            "node_timings": {_NODE_NAME: int((time.time() - start_time) * 1000)},
        }

    # 构建 claim_id → final_status 映射（claims 列表为验证后权威状态）
    status_map = _build_claim_status_map(claims_raw)

    # 深拷贝 AST 字典后同步 final_status，避免就地修改 LangGraph state（state 应视为只读）
    ast_dict = copy.deepcopy(answer_ast_dict)
    _sync_claim_statuses(ast_dict.get("root") or {}, status_map)

    # 反序列化为强类型 AnswerAST 并渲染
    try:
        ast = AnswerAST.from_dict(ast_dict)
    except Exception as e:
        logger.warning(
            f"[{_NODE_NAME}] AnswerAST 反序列化失败，降级返回 generated_answer: {e}"
        )
        return {
            "final_answer": generated_answer or "",
            "node_timings": {_NODE_NAME: int((time.time() - start_time) * 1000)},
        }

    rendered_text = AnswerRenderer().render(ast)

    supported_count = sum(
        1 for s in status_map.values() if s == ClaimFinalStatus.SUPPORTED.value
    )
    logger.info(
        f"[{_NODE_NAME}] 答案渲染完成: "
        f"claims_total={len(status_map)}, supported={supported_count}, "
        f"rendered_length={len(rendered_text)}"
    )

    return {
        "final_answer": rendered_text,
        "node_timings": {_NODE_NAME: int((time.time() - start_time) * 1000)},
    }


def _build_claim_status_map(claims_raw: list) -> dict[str, str]:
    """从 state.claims 构建 claim_id → final_status 映射。

    state.claims 存储的是 Claim.to_dict() 序列化结果，此处反序列化为 Claim
    以复用其默认值兜底逻辑；反序列化失败的条目直接读字典，保证不中断渲染。

    Args:
        claims_raw: state.claims 原始列表（每项为 Claim 字典）

    Returns:
        dict: {claim_id: final_status}
    """
    status_map: dict[str, str] = {}
    for raw in claims_raw:
        if not isinstance(raw, dict):
            continue
        try:
            claim = Claim.from_dict(raw)
            status_map[claim.claim_id] = claim.final_status
        except Exception:
            # 反序列化失败时直接读字典，确保 claim_id 仍能进入映射
            claim_id = raw.get("claim_id", "")
            if claim_id:
                status_map[claim_id] = raw.get(
                    "final_status", ClaimFinalStatus.PENDING.value
                )
    return status_map


def _sync_claim_statuses(node: dict, status_map: dict[str, str]) -> None:
    """递归同步 final_status 到 AST 节点字典（就地修改）。

    设计决策：直接操作序列化后的字典而非强类型 AST 节点，避免额外导入
    SectionNode/ParagraphNode 等节点类型，保持导入路径与设计文档 §4.8.3 一致。
    schema_version=1.0 下字段名（content/children/row_claims/node_type）稳定。

    遍历规则：
    - claim / table_row_claim 节点：按 claim_id 查表覆盖 final_status
    - 其余节点：递归遍历所有子节点容器（content/children/row_claims）

    Args:
        node: AST 节点字典（从 root section 开始递归）
        status_map: {claim_id: final_status}
    """
    if not isinstance(node, dict):
        return

    node_type = node.get("node_type", "")
    # Claim / TableRowClaim 节点需同步验证状态（渲染器只读 supported）
    if node_type in ("claim", "table_row_claim"):
        claim_id = node.get("claim_id", "")
        if claim_id in status_map:
            node["final_status"] = status_map[claim_id]

    # 递归遍历所有可能的子节点容器（Section.content / Paragraph.children / Table.row_claims）
    for key in ("content", "children", "row_claims"):
        children = node.get(key) or []
        for child in children:
            if isinstance(child, dict):
                _sync_claim_statuses(child, status_map)


class SSEStreamFilter:
    """SSE 流式输出过滤器（v2.1 §4.8.3 验收项 24）。

    设计要点：
    - DISABLED 模式：无验证保证，全部 chunk 立即流式发送（legacy 行为）
    - ENFORCED/SHADOW 模式：narrative 立即发送，claim 缓冲待验证后发送，
      citation 标记一律不发送（避免引用编号在验证前泄露给客户端）

    fail-closed 原则：未验证 claim 不得在 chunk 中出现，违反则验收项 24 失败。

    类比 Java 中的过滤器链（Filter Chain）：
        ``SSEStreamFilter`` 类似 ``OncePerRequestFilter``，对每个 chunk 做拦截/放行
        决策，缓冲态由内部 ``_claim_buffer`` 维护。
    """

    def __init__(self, enforcement: EnforcementMode):
        """初始化流式过滤器。

        Args:
            enforcement: 强制级别（DISABLED/SHADOW/ENFORCED）
        """
        self.enforcement = enforcement
        # 缓冲的 claim 文本（ENFORCED/SHADOW 模式下待验证完成后才决定是否发送）
        self._claim_buffer: list[str] = []

    def on_chunk(self, chunk_type: str, text: str) -> str | None:
        """处理流式 chunk，返回可发送给客户端的内容。

        Args:
            chunk_type: chunk 类型 —— "narrative"（非事实连接文本）/
                "claim"（事实声明）/ "citation"（引用标记）
            text: chunk 文本

        Returns:
            可立即发送的文本；None 表示该 chunk 暂不发送（已缓冲或丢弃）。
        """
        # DISABLED 模式：无验证保证，全部流式发送（legacy 兼容）
        if self.enforcement == EnforcementMode.DISABLED:
            return text

        # ENFORCED/SHADOW 模式：按 chunk 类型分流
        if chunk_type == "narrative":
            # 非事实连接文本（标题/过渡句）不含事实声明，可立即发送
            return text
        if chunk_type == "claim":
            # ★ Claim 文本缓冲，待 on_verification_complete 后按 final_status 决定是否发送
            self._claim_buffer.append(text)
            return None
        if chunk_type == "citation":
            # 引用标记一律不发送（引用目录在验证完成后通过单独事件下发）
            return None
        # 未知 chunk 类型：fail-closed，不发送
        return None

    def on_verification_complete(self, verified_claims: list[dict]) -> list[str]:
        """验证完成后返回可发送的 claim 文本。

        Args:
            verified_claims: 验证后的 claim 列表，每项形如
                ``{"text": "...", "final_status": "supported"}``

        Returns:
            可发送的文本列表（按 verified_claims 顺序）。
            - DISABLED：返回全部缓冲（DISABLED 下 claim 已在 on_chunk 流式发送，
              缓冲为空，故无额外可发送内容）
            - ENFORCED/SHADOW：仅返回 final_status == supported 的文本，
              contradicted/insufficient/verifier_error 一律丢弃
        """
        # DISABLED 模式：claim 已在流式阶段发送，缓冲为空，无额外可发送内容
        if self.enforcement == EnforcementMode.DISABLED:
            return list(self._claim_buffer)

        # ENFORCED/SHADOW 模式：只发送 supported 的 claim，其余 fail-closed 丢弃
        result: list[str] = []
        for claim in verified_claims:
            if not isinstance(claim, dict):
                continue
            if claim.get("final_status") == ClaimFinalStatus.SUPPORTED.value:
                text = claim.get("text", "")
                if text:
                    result.append(text)
        return result
