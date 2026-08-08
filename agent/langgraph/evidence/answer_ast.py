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
"""Answer AST 严格 Schema（v2.1 §4.3，P0-E + v2 评审 P0-7 修复）。

设计目标：
    生成阶段输出结构化答案 AST（section/paragraph/claim/table 层级），
    验证后由渲染器重建文本，避免 ``" ".join(parts)`` 重组丢失标题/表格/限定词。

核心约束（P0-7 修复）：
    - SectionNode.content 只允许 Paragraph/Table/Section，禁止直接放 ClaimNode
    - ParagraphNode.children 只允许 Claim/Narrative，禁止裸 str（防止事实绕过验证）
    - ClaimNode 含 final_status / server_is_required / schema_version，渲染器只读 supported
    - TableNode 按行生成 Claim（TableRowClaimNode），不是整表引用
    - NodeType 作为 type discriminator，支持 Pydantic 判别联合类型解析

类比 Java 中的领域模型：
    ``AnswerAST`` 类似聚合根（Aggregate Root），``SectionNode`` 类似 ``@Entity``，
    ``ClaimNode`` / ``NarrativeNode`` 类似 ``@Embeddable`` 值对象。
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T", bound="AnswerASTBase")

# Claim 最终状态字符串常量（取值与 claim.py 的 ClaimFinalStatus 保持一致）。
# 此处刻意不跨模块导入，保证 AST schema 模块自包含、可独立序列化。
_STATUS_SUPPORTED = "supported"


class NodeType(str, Enum):
    """AST 节点类型标识（type discriminator）。

    作为 Pydantic 判别联合的 discriminator 字段，解析时按此字段选择具体节点类型。
    """

    SECTION = "section"
    PARAGRAPH = "paragraph"
    CLAIM = "claim"
    TABLE = "table"
    TABLE_ROW_CLAIM = "table_row_claim"
    NARRATIVE = "narrative"  # 非事实连接文本（标题/过渡句）


class AnswerASTBase(BaseModel):
    """所有 AST 节点的基类。

    设计决策：
        - ``extra="forbid"``：严格 schema，未知字段直接报错，防止 LLM 偷塞脏数据
        - ``to_dict`` / ``from_dict``：显式序列化入口，便于 state 持久化与审计；
          嵌套判别联合由 Pydantic 自动按 node_type 分发，无需手写分支
    """

    model_config = ConfigDict(extra="forbid")

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 安全的字典（枚举转字符串值）。"""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        """从字典反序列化（嵌套判别联合由 Pydantic 自动分发）。"""
        return cls.model_validate(data)


class ClaimNode(AnswerASTBase):
    """Answer AST 中的 Claim 节点（v2 修复 P0-7）。

    修复要点：
        - 含 ``final_status`` 字段（渲染器需要读取以决定是否输出）
        - ``model_is_required`` 仅记录模型声明，``server_is_required`` 为服务端重算结果
        - 必须标注 ``citation_indices``，便于 Claim 级引用绑定
        - 显式 ``schema_version`` 支持后续 schema 演进
    """

    node_type: Literal[NodeType.CLAIM] = NodeType.CLAIM
    schema_version: str = "1.0"
    claim_id: str
    text: str  # claim 文本（不含引用标记）
    citation_indices: list[int] = Field(default_factory=list)
    model_claim_type: str = "statement"  # 模型声明的类型（仅供审计）
    model_is_required: bool = False  # 模型声明是否关键（仅供审计）
    # ★ 运行时填充（服务端重新计算 + verifier 验证后）
    final_status: str = "pending"  # pending/supported/contradicted/insufficient/verifier_error
    server_claim_type: str = ""  # 服务端重新计算的类型
    server_is_required: bool = False  # 服务端重新计算的关键性


class NarrativeNode(AnswerASTBase):
    """非事实连接文本节点（v2 修复 P0-7）。

    ★ 必须保证只包含非事实连接文本，不能包含事实声明。
    渲染器原样输出，不经验证。
    （事实关键词升级为 ClaimNode 的校验由服务端 NarrativeValidator 负责，不在本 schema 内。）
    """

    node_type: Literal[NodeType.NARRATIVE] = NodeType.NARRATIVE
    text: str  # 纯连接文本（如"综上所述"、"以下是分析结果"）


class TableRowClaimNode(AnswerASTBase):
    """表格行级 Claim 节点（v2 修复 P0-7）。

    ★ 表格按行生成 Claim，不是整表引用。
    每行的关键单元格形成可验证的 Claim。
    """

    node_type: Literal[NodeType.TABLE_ROW_CLAIM] = NodeType.TABLE_ROW_CLAIM
    claim_id: str
    row_index: int  # 行号
    row_values: dict[str, str] = Field(default_factory=dict)  # {列名: 值}
    citation_indices: list[int] = Field(default_factory=list)
    final_status: str = "pending"


class TableNode(AnswerASTBase):
    """表格节点（v2 修复：按行生成 Claim）。"""

    node_type: Literal[NodeType.TABLE] = NodeType.TABLE
    headers: list[str] = Field(default_factory=list)
    row_claims: list[TableRowClaimNode] = Field(default_factory=list)  # ★ 每行一个 Claim


# Paragraph 的子节点：只允许 Claim 或 Narrative（禁止裸 str，防止事实绕过验证）
ParagraphChild = Annotated[Union[ClaimNode, NarrativeNode], Field(discriminator="node_type")]


class ParagraphNode(AnswerASTBase):
    """段落节点（v2 修复 P0-7）。

    ★ 不再允许 str 类型，必须用 NarrativeNode 包装。
    防止普通 str 包含事实但绕过验证。
    """

    node_type: Literal[NodeType.PARAGRAPH] = NodeType.PARAGRAPH
    children: list[ParagraphChild] = Field(default_factory=list)


# Section 的内容：只允许 Paragraph/Table/Section（明确禁止 ClaimNode）
# Claim 必须先进入 Paragraph，保证段落语义边界。
SectionContent = Annotated[Union["SectionNode", ParagraphNode, TableNode], Field(discriminator="node_type")]


class SectionNode(AnswerASTBase):
    """章节节点（v2 修复 P0-7）。

    ★ content 明确不包含 ClaimNode，只允许 Paragraph/Table/Section。
    """

    node_type: Literal[NodeType.SECTION] = NodeType.SECTION
    title: str = ""
    level: int = 1
    content: list[SectionContent] = Field(default_factory=list)


# 递归引用 SectionNode，需 rebuild 解析前向引用
SectionNode.model_rebuild()


class AnswerAST(AnswerASTBase):
    """结构化答案 AST（v2 修复 P0-7）。

    强类型 schema + 显式版本号。单根 SectionNode，可嵌套子 Section。
    """

    schema_version: str = "1.0"
    root: SectionNode


AnswerAST.model_rebuild()


class AnswerRenderer:
    """答案渲染器：AST → Markdown 文本（v2 修复 P0-7）。

    ★ 只渲染 ``final_status == "supported"`` 的 Claim；Narrative 原样输出。
    过滤后重建时保留标题、段落、表格结构，避免 ``" ".join`` 重组破坏语义。
    """

    def render(self, ast: AnswerAST) -> str:
        """渲染 AST 为 Markdown 文本。

        Args:
            ast: 答案 AST

        Returns:
            str: 仅包含 supported claim 的 Markdown 文本
        """
        return self._render_section(ast.root).strip()

    def _render_section(self, section: SectionNode) -> str:
        parts: list[str] = []
        # 根 section（level=0, title="") 不输出标题，避免多余空标题
        if section.title:
            parts.append(f"{'#' * max(section.level, 1)} {section.title}")

        for node in section.content:
            if isinstance(node, ParagraphNode):
                rendered = self._render_paragraph(node)
                if rendered:
                    parts.append(rendered)
            elif isinstance(node, TableNode):
                rendered = self._render_table(node)
                if rendered:
                    parts.append(rendered)
            elif isinstance(node, SectionNode):
                parts.append(self._render_section(node))
        return "\n\n".join(p for p in parts if p)

    def _render_paragraph(self, para: ParagraphNode) -> str:
        parts: list[str] = []
        for child in para.children:
            if isinstance(child, ClaimNode):
                # ★ 只渲染 supported 的 claim，其余丢弃
                if child.final_status == _STATUS_SUPPORTED:
                    citation_str = "".join(f"[{i}]" for i in child.citation_indices)
                    parts.append(f"{child.text}{citation_str}" if citation_str else child.text)
            elif isinstance(child, NarrativeNode):
                # Narrative 原样输出（已校验不含事实）
                parts.append(child.text)
        return " ".join(parts)

    def _render_table(self, table: TableNode) -> str:
        # ★ 只渲染 supported 的行；全行被滤掉则整表不输出
        visible_rows = [rc for rc in table.row_claims if rc.final_status == _STATUS_SUPPORTED]
        if not visible_rows:
            return ""

        lines = ["| " + " | ".join(table.headers) + " |"]
        lines.append("| " + " | ".join("---" for _ in table.headers) + " |")
        for rc in visible_rows:
            row_str = " | ".join(rc.row_values.get(h, "") for h in table.headers)
            citation_str = "".join(f"[{i}]" for i in rc.citation_indices)
            lines.append(f"| {row_str} |{citation_str}" if citation_str else f"| {row_str} |")
        return "\n".join(lines)


class AnswerParser:
    """答案解析器：LLM 输出 → AnswerAST（v2 修复 P0-7）。

    LLM 按 citation-aware prompt 输出 JSON（sections/paragraphs/claims 层级），
    解析器负责：
        1. 剥离 markdown 代码围栏并提取 JSON
        2. 归一化字段名（type→node_type, paragraphs→children, is_required→model_is_required）
        3. 补全 claim_id / node_type / 默认状态
        4. 通过 Pydantic 严格校验，产出 AnswerAST
    """

    def __init__(self) -> None:
        self._claim_seq = 0

    def parse(self, text: str) -> AnswerAST:
        """从 LLM 输出文本解析 AnswerAST。

        Args:
            text: LLM 输出（JSON 或带 markdown 围栏的 JSON）

        Returns:
            AnswerAST: 校验通过的结构化答案 AST

        Raises:
            ValueError: 无法解析 JSON 或 schema 校验失败
        """
        self._claim_seq = 0
        payload = self._extract_json(text)
        normalized = self._normalize(payload)
        return AnswerAST.from_dict(normalized)

    def _extract_json(self, text: str) -> dict[str, Any]:
        """从文本中提取 JSON 字典（兼容 markdown 代码围栏）。"""
        stripped = text.strip()

        # 剥离 markdown 代码围栏 ```json ... ```
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            lines = lines[1:]  # 去掉首行围栏
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]  # 去掉末行围栏
            candidate = "\n".join(lines)
        else:
            candidate = stripped

        # 直接尝试整段解析
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # 兜底：截取首个 { 到末尾 } 的子串（LLM 可能在 JSON 前后附带解释文字）
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            try:
                result = json.loads(candidate[start : end + 1])
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        raise ValueError(f"无法从 LLM 输出中解析 JSON: {text[:200]!r}")

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """将 LLM 原始输出归一化为 AnswerAST schema 字典。"""
        if isinstance(payload.get("root"), dict):
            root = self._normalize_node(payload["root"])
        elif isinstance(payload.get("sections"), list):
            sections = [self._normalize_node(s) for s in payload["sections"] if isinstance(s, dict)]
            if len(sections) == 1:
                root = sections[0]
            else:
                # 多 section → 包装为根 section，保留原层级嵌套
                root = {"node_type": "section", "title": "", "level": 0, "content": sections}
        elif isinstance(payload.get("section"), dict):
            root = self._normalize_node(payload["section"])
        else:
            # 整体当作单个 section 处理
            root = self._normalize_node(payload)
        return {
            "schema_version": str(payload.get("schema_version") or "1.0"),
            "root": root,
        }

    def _normalize_node(self, node: dict[str, Any]) -> dict[str, Any]:
        """递归归一化单个节点：统一字段名并补全 discriminator。"""
        raw_type = node.get("type") or node.get("node_type") or ""
        ntype = str(raw_type).lower().strip()

        # 无 type 时按结构特征兜底判定，提升对 LLM 输出漂移的鲁棒性
        if not ntype:
            if "paragraphs" in node or "children" in node:
                ntype = "paragraph"
            elif "row_claims" in node:
                ntype = "table"
            elif "row_index" in node:
                ntype = "table_row_claim"
            elif "content" in node or "title" in node:
                ntype = "section"
            elif "claim_id" in node or "citation_indices" in node:
                ntype = "claim"
            else:
                ntype = "narrative"

        if ntype == "section":
            content = [self._normalize_node(c) for c in node.get("content", []) if isinstance(c, dict)]
            # level=0 是合法值（根 section），需显式处理 None，不能用 ``or``
            level = node.get("level", 1)
            return {
                "node_type": "section",
                "title": str(node.get("title") or ""),
                "level": int(level) if level is not None else 1,
                "content": content,
            }
        if ntype == "paragraph":
            raw_children = node.get("paragraphs") or node.get("children") or []
            children = [self._normalize_node(c) for c in raw_children if isinstance(c, dict)]
            return {"node_type": "paragraph", "children": children}
        if ntype == "table":
            raw_rows = node.get("row_claims", [])
            row_claims = [self._normalize_node(r) for r in raw_rows if isinstance(r, dict)]
            return {
                "node_type": "table",
                "headers": [str(h) for h in (node.get("headers") or [])],
                "row_claims": row_claims,
            }
        if ntype == "table_row_claim":
            row_index = node.get("row_index", 0)
            return {
                "node_type": "table_row_claim",
                "claim_id": node.get("claim_id") or self._gen_claim_id(),
                "row_index": int(row_index) if row_index is not None else 0,
                "row_values": {str(k): str(v) for k, v in (node.get("row_values") or {}).items()},
                "citation_indices": list(node.get("citation_indices") or []),
                "final_status": str(node.get("final_status") or "pending"),
            }
        if ntype == "claim":
            # is_required / claim_type 是 prompt 模板的字段名，归一化为 model_* 审计字段
            return {
                "node_type": "claim",
                "schema_version": str(node.get("schema_version") or "1.0"),
                "claim_id": node.get("claim_id") or self._gen_claim_id(),
                "text": str(node.get("text") or ""),
                "citation_indices": list(node.get("citation_indices") or []),
                "model_claim_type": node.get("claim_type") or node.get("model_claim_type") or "statement",
                "model_is_required": bool(node.get("is_required", node.get("model_is_required", False))),
                "final_status": str(node.get("final_status") or "pending"),
                "server_claim_type": str(node.get("server_claim_type") or ""),
                "server_is_required": bool(node.get("server_is_required", False)),
            }
        # narrative 或未知类型 → 作为纯连接文本（fail-soft，避免解析中断）
        return {"node_type": "narrative", "text": str(node.get("text") or "")}

    def _gen_claim_id(self) -> str:
        """生成确定性 claim_id（单次 parse 内递增，便于追踪）。"""
        self._claim_seq += 1
        return f"claim_{self._claim_seq:03d}"
